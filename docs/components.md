# Components & tech stack

A component-by-component reference for `private-ai-workspace-eks` — what each
piece does and the technology it is built on. For how to install and wire them
together, see [`install.md`](install.md).

## Architecture at a glance

A **two-plane** design on AWS EKS:

- **CPU control plane** (`app/control_plane/`) — a stateless HTTP service that
  stays usable when the GPU is cold. Auth, chat orchestration, retrieval/memory,
  agents, integrations, media routing, notifications.
- **GPU inference plane** — isolated, internal-only model servers (vLLM for
  chat, TEI for embeddings, Whisper/SDXL for media) that the control plane
  reaches over in-cluster DNS.

Everything is packaged as Helm charts on EKS; identity is OIDC/IRSA end to end;
every add-on capability is **deny-by-default** with an operator kill-switch.

```
 Browser ── HTTPS/ALB ─▶ Web UI (nginx SPA) ──同origin proxy──▶ Control plane (CPU, :8080)
                                                                   │  in-cluster, internal-only
                            ┌──────────────────────────────────────┼───────────────────────────┐
                            ▼                    ▼                  ▼               ▼             ▼
                      vLLM (GPU)          Embeddings/TEI (CPU)   Media (GPU)   RDS/pgvector   S3 artifacts
                                                                                (Postgres 16)
```

## Tech stack at a glance

| Layer | Technology |
| --- | --- |
| Control plane | **Python 3.11+ standard library only** for app logic (image runs 3.14-slim); `http.server.ThreadingHTTPServer`, hand-rolled router, `urllib`. No web framework. |
| Control-plane image extras | PyJWT + cryptography (OIDC JWT), `psycopg[binary,pool]` (RDS/pgvector), boto3 (S3 + Secrets Manager), prometheus-client, opentelemetry-sdk + OTLP/gRPC — all imported lazily so the unit suite stays stdlib-only |
| Web UI | Vanilla JS (no framework, no build) + HTML/CSS, served by **nginx** (Alpine); OIDC Authorization Code + **PKCE**; vendored pdf.js (Apache-2.0); PWA/service worker |
| Inference | **vLLM** (OpenAI-compatible), HuggingFace **TEI** embeddings, faster-whisper / SDXL media |
| Data | **PostgreSQL 16 + pgvector** (RDS), **S3** (artifacts) |
| Identity | **Amazon Cognito** (dev) / any OIDC IdP; **IRSA** for every workload |
| Platform | **EKS 1.35**, managed node groups + **Karpenter**, VPC, ECR |
| Observability | **kube-prometheus-stack** (Prometheus/Grafana/Alertmanager), OpenTelemetry, DCGM GPU exporter |
| Infra as code | **Terraform** (AWS provider ≥6.42) |
| CI/CD | **GitHub Actions** (OIDC to AWS), Helm v3, CodeQL, Dependabot |

---

## 1. Control plane (`app/control_plane/`, `app/sandbox/`, `app/db/`)

A single-process, stdlib-only Python service (`server.py`, ~1,880 lines): a
`ThreadingHTTPServer` + one `BaseHTTPRequestHandler` that matches ~40 `/v1`
routes and delegates to pure `build_*_response()` handlers in feature modules.
Every stateful subsystem has the same shape — an immutable dataclass model, a
structural `Protocol` store, an **InMemory** dev backend and a **Postgres**
production backend selected when `DATABASE_URL` is set.

**Cross-cutting invariants:** tenant = OIDC email domain; tenant/user isolation
enforced at the storage layer **and re-checked per request**; deny-by-default
per-tenant allow-lists + operator kill-switches on every capability; **M5 content
policy** — logs/telemetry/audit/notifications carry *shape* only (key names,
type/size, counts), never values.

| Component | Role | Tech |
| --- | --- | --- |
| **HTTP server + router** (`server.py`) | The single entrypoint: parse → verify bearer → delegate → JSON. Hosts health/readiness/status/models/metrics + SSE chat & notification streams; wires every store as a class attribute in `run_server()`. | `http.server.ThreadingHTTPServer`, stdlib json, daemon threads (backpressure) |
| **Config** (`config.py`) | Frozen `ControlPlaneConfig` from ~60 env vars; `readiness_checks()`, `model_list()`, `make_token_verifier()`. Every kill-switch/allow-list/limit is a field defaulting to safe/off. | `dataclasses`, `os.environ` |
| **Auth / token verification** (`token_verifier.py`, `auth.py`) | `OIDCTokenVerifier` (JWKS, RS256/ES256, iss/aud/exp, groups) for prod; `DevTokenVerifier` (static token, refused in staging/prod). Verifies the Cognito **ID token**. | PyJWT (`PyJWKClient`) + cryptography (lazy), OIDC/JWKS |
| **Sessions** (`session.py`, `session_postgres.py`) | `WorkspaceSession` + store; InMemory (dev) / Postgres (multi-replica). 8h TTL. | psycopg pool |
| **Conversations** (`conversations.py`) | Per-(tenant,user) chat threads + messages so history survives tab close/device switch. Strict isolation, authoritative delete. | psycopg pool |
| **Retrieval / RAG** (`retrieval.py`) | Tenant-isolated document retrieval (M10): chunk → embed → store → cosine query scoped to the token's tenant. | **pgvector** `vector(384)`, cosine `<=>` |
| **Memory** (`memory.py`) | Per-(tenant,user) long-term memory; opt-in consented writes; cross-user recall impossible (storage-layer `user_id` filter). | pgvector |
| **Embeddings** (`embeddings.py`) | Text → 384-dim vectors. `DeterministicEmbeddingClient` (dev, SHA256 feature-hash, no GPU) / `InferenceEmbeddingClient` (prod, in-cluster `/v1/embeddings`). | stdlib, OpenAI embeddings contract |
| **Notes / Documents / Compare** (`notes.py`, `documents.py`, `compare.py`) | Per-user notes/tasks/docs; AI doc-edit; blind A/B of one prompt across ≤4 models + optional synthesis. | psycopg pool, reuses inference client |
| **Notifications** (`notifications.py`) | In-app feed + **SSE** push (`/v1/notifications/stream`). Also the shared auth/tenant helpers (`_verify_and_extract`, `_extract_tenant_id`) imported by nearly every module. Events carry class/resource-id/time only. | Server-Sent Events, psycopg pool |
| **Agent tools + sandbox** (`agent_tools.py`, `app/sandbox/`) | M11 allow-listed, **out-of-process** sandboxed tool execution: subprocess with scrubbed env, RLIMITs, timeout+SIGKILL; deny-by-default per-tenant allow-list, kill-switch, rate/concurrency limits, shape-only audit. Only an inert `text_stats` stub ships. | stdlib `subprocess`/`resource`/`signal`; sandbox is stdlib-only (no config/boto3/DB) |
| **Agent loop** (`agent_loop.py`) | LLM-driven plan→act→observe over the sandboxed tools; extracts one balanced JSON action from untrusted output; re-checks the allow-list every call; server-enforced budgets. | stdlib, OpenAI chat contract |
| **Job executor** (`job_executor.py`, `app/dispatcher/`) | Control-plane client for the out-of-namespace **tool-runner** dispatcher (M11 follow-up 3): IO-capable tools run as isolated K8s Jobs, not in the control plane (which holds no cluster privileges). | urllib, shared-token HTTP boundary |
| **MCP** (`mcp.py`, `app/mcp_servers/`) | M12 per-tenant, deny-by-default MCP servers as out-of-process subprocesses speaking JSON-RPC 2.0 over stdio; fresh process per call. Only a `stub` echo server ships. | subprocess, JSON-RPC 2.0/stdio |
| **Integrations** (`integrations.py`, `integration_secrets.py`, `integrations_google.py`) | M13 personal-info integration harness (Google Calendar first). Pure request *builders* sent through the SSRF guard; per-tenant creds resolved from Secrets Manager via IRSA with a TTL cache. | boto3 (lazy), OAuth2, guarded outbound |
| **Outbound URL guard** (`outbound.py`) | The deny-by-default egress chokepoint: https-only, host allow-list, resolve-and-reject private/loopback/link-local/cloud-metadata, **IP-pinned** connection (DNS-rebind defense). | stdlib `http.client`/`ipaddress`/`ssl` |
| **Deep research + web search** (`deep_research.py`, `web_search.py`) | Constrained plan→retrieve→synthesize over the tenant's **own** corpus (+ optional web via the guarded client). No search engine vendored. | stdlib, embeddings, outbound guard |
| **Media** (`media.py`) | M14 routing to isolated GPU media backends (STT/image/TTS); per-tenant allow-list + kill-switches + size caps + per-tenant S3 isolation. Weights live only in the GPU service. | urllib, S3, RateLimiter |
| **Model install requests + reconciler API** (`model_requests.py`) | Self-serve model-install **intent** records (Phase 1a) + the shared-token internal endpoints (`/v1/internal/model-installer/*`) that feed the separate reconciler. Control plane never downloads a model or mutates the cluster. | stdlib, hmac shared-token |
| **Inference client + routing** (`inference.py`, `routing.py`) | Narrow contract to vLLM: OpenAI-compatible chat + SSE relay, retry policy, `probe_inference_health()` (warm/loading/cold) for the UI, URL normalisation. | urllib, SSE, W3C traceparent |
| **Observability** (`logging_config.py`, `tracing.py`, `metrics.py`) | Content-safe JSON logging (whitelisted keys only), OTel tracing (no-op when unset), Prometheus golden-signal + retrieval metrics with a path sanitiser. | stdlib logging, opentelemetry-sdk (lazy), prometheus-client (lazy) |
| **Database layer** (`app/db/`) | One `psycopg_pool.ConnectionPool`; idempotent `schema.sql` (8 migrations) applied under a `pg_advisory_xact_lock` so replicas serialise on startup. | `psycopg[binary,pool]`, PostgreSQL 16 + pgvector |
| **S3 object storage** (`app/storage/s3.py`) | Per-tenant object storage for media artifacts, via IRSA. | boto3 (lazy) |

Packaged as one image (`app/Dockerfile`, `FROM python:3.14-slim`), run as
`python -m app.control_plane` on `0.0.0.0:8080`.

---

## 2. Web UI (`app/ui/`)

A self-contained, **no-framework/no-build** single-page app served by nginx.

| Component | Role | Tech |
| --- | --- | --- |
| **nginx server + reverse proxy** (`nginx.conf`) | Serves the SPA, same-origin-proxies `/v1`,`/auth`,`/healthz`,`/readyz` to the control plane, enforces a **strict CSP** + security headers, blocks `/v1/internal/` from the browser, `no-cache`+ETag on `/static`, SPA history fallback. | nginx 1.31-alpine, `envsubst`, HTTP/1.1 proxy |
| **Image + entrypoint** (`Dockerfile`, `docker-entrypoint.sh`) | Non-root Alpine image; at start renders `/config.json` (OIDC + model config) and the CSP OIDC allow-list from Helm env, then execs nginx. | Docker, POSIX sh, `envsubst` |
| **App shell** (`index.html`) | Full-screen routed layout: left feature rail (12 screens), topbar, `#screen-host`, notification drawer, GPU cold-start banner. No inline scripts. | HTML5, inline SVG, `@font-face` FiraCode |
| **SPA logic** (`app.js`) | The whole client runtime: OIDC callback + PKCE exchange, hash router (prototype-pollution-safe), SSE streaming chat with cold-start retry, agent mode, notifications, and every screen handler. **No-`innerHTML`** markdown renderer (createElement/textContent) keeps model output CSP-clean. | Vanilla JS, `fetch`+ReadableStream SSE, sessionStorage |
| **Login** (`login.html`, `login.js`) | PKCE verifier/challenge (Web Crypto SHA-256), redirect to the OIDC authorize endpoint. Kept external to satisfy `script-src 'self'`. | Web Crypto, OAuth2 PKCE (RFC 7636) |
| **Design system** (`style.css`) | One Dark / light-theme tokens, warm-coral accent (`--brand-color #ee6a5f`), shell/screens/pills/GPU banner. | CSS custom properties, `color-mix()` |
| **Service worker + PWA** (`sw.js`, `manifest.json`) | Network-first cache for the app shell (never `/v1`/`/config.json`); installable PWA. | Service Worker + Cache Storage API |
| **Vendored pdf.js** (`static/vendor/`) | Client-side PDF **text** extraction on upload — PDFs never hit the server. `isEvalSupported:false` for the CSP. | pdf.js 3.11.174 (Apache-2.0) |
| **12 feature screens** | Chat (SSE), Documents (RAG), Editor, Memory, Notes/Tasks, Compare, Agent/Research, Media, Calendar/Integrations, Tools/MCP, Models, Settings. Each has a lazy first-visit loader; management actions stay disabled until the server confirms capability. | fetch → control-plane `/v1` |
| **GPU cold-start flow** | Probes `/v1/inference/status?probe=1`, maps warm/loading/cold to a banner, auto-retries `503+Retry-After` sends with a countdown, offers a warm-up ping. | adaptive `setTimeout` polling |

---

## 3. Inference / GPU plane

Independently-deployed Helm releases pinned to the isolated inference node group
(`nodeSelector private-ai-workspace/plane=inference`, tolerating the
`nvidia.com/gpu` taint), all **ClusterIP / internal-only**.

| Component | Role | Tech |
| --- | --- | --- |
| **vLLM inference** (`deploy/helm/vllm`) | OpenAI-compatible LLM serving. Dev: `Qwen/Qwen2.5-1.5B-Instruct` (Apache-2.0, ungated) on one **g5.xlarge** (A10G 24 GB) under the stable alias `default`. Scale-to-zero (GPU node group at `desired=0`). Installer mode reads `MODEL_ID` from a ConfigMap and pins `--load-format safetensors`. | `vllm/vllm-openai:v0.9.0`, HPA on `vllm_num_requests_waiting`, ServiceMonitor, IRSA, NetworkPolicy |
| **Embeddings (TEI)** (`deploy/helm/embedding`) | CPU `/v1/embeddings` for retrieval/memory; **must** emit 384-dim to match `EMBEDDING_DIM`. Weights pulled at pod start. | HuggingFace **TEI** `cpu-1.6`, `BAAI/bge-small-en-v1.5` (MIT, 384-dim) |
| **Media services** (`deploy/helm/media-service`) | Generic GPU media chart deployed once per model: `whisper-stt` (STT) and `sdxl-image` (image). | faster-whisper (MIT); SDXL (OpenRAIL++-M — image left empty pending license review) |
| **Tool-runner dispatcher** (`deploy/helm/tool-runner`, `app/dispatcher/`) | The **only** identity allowed to create K8s Jobs for agent tools; trusts the control plane by shared bearer; runs each tool in a locked-down, network-denied Job. | Python stdlib, runs from the control-plane image; hardened Job template |
| **Model-installer reconciler** (`app/model_installer/`, `deploy/helm/model-installer`) | Separate scoped component (design Phase 3): `requested → installing → scale GPU nodegroup 0→1 → patch vLLM ConfigMap + restart → wait /health → applied/failed`. Own narrow IRSA (scale GPU nodegroups only) + namespace RBAC (patch only the vLLM ConfigMap+Deployment); rolls back on failed load. | Python 3.12 image with **boto3 + kubernetes**; the only image with these deps |

---

## 4. Data & identity

| Component | Role | Tech |
| --- | --- | --- |
| **RDS PostgreSQL** (`modules/rds`) | Control-plane state (chat, sessions, retrieval, memory). Private, KMS-encrypted gp3; master password auto-generated into Secrets Manager. pgvector at the app layer. | PostgreSQL 16, `db.t3.medium` (dev), Performance Insights |
| **S3 artifacts** (`modules/s3`) | Per-tenant object storage (media artifacts, uploads). Public access blocked, KMS SSE, versioned. IRSA-scoped only. | S3 + KMS |
| **Amazon Cognito (dev)** (`modules/cognito`) | Dev OIDC IdP: user pool (email login), public SPA **PKCE** client, hosted-UI `/oauth2/*`, seeded cross-tenant test users. Prod points at a real external IdP. | Cognito user pool + client + domain |
| **Secrets Manager + ESO** | Terraform creates operator-populated placeholders (`<project>/<env>/app`, `hf-token`) + RDS creds; **External Secrets Operator** (Helm) syncs them into K8s Secrets. Values set out-of-band, never in state. | AWS Secrets Manager, ESO |
| **IRSA roles** (`modules/irsa-*`, `modules/karpenter`) | Least-privilege per-ServiceAccount identities: app (RDS/app-config/integration secrets + S3), vLLM (HF token read), model-installer (scale GPU nodegroups only), cluster-autoscaler, Karpenter (controller + node), external-dns. | `AssumeRoleWithWebIdentity`, OIDC-scoped to exact SA |

---

## 5. Platform / infrastructure (`infra/terraform/`)

A single root module wiring 13 child modules. Terraform ≥1.6, AWS provider
≥6.42. State is **local** today (wire an S3/DynamoDB backend before shared/prod
use).

| Component | Role | Tech |
| --- | --- | --- |
| **VPC** (`modules/vpc`) | 3-AZ private/public subnets, NAT egress, ELB/Karpenter discovery tags. | `terraform-aws-modules/vpc` 5.21 |
| **EKS + node groups** (`modules/eks`) | K8s 1.35; **control-plane** managed NG (ON_DEMAND `m7i.large`) + **GPU inference** NG (`g5.xlarge`, `desired=0` scale-to-zero, AL2023 NVIDIA AMI, `nvidia.com/gpu` taint). Core addons + AWS Load Balancer Controller. | `terraform-aws-modules/eks` ~21, EKS addons |
| **ECR** (`modules/ecr`) | Registries: control-plane (immutable, KMS, scan-on-push) + ui (mutable). | ECR |
| **GitHub Actions deploy role** (`modules/github-actions-role`) | CI/CD OIDC identity: push ECR images + EKS cluster-admin access entry. | OIDC provider + IAM role + EKS access entry |
| **ACM + Route53** (root `main.tf`) | DNS-validated UI cert + `external-dns` sync of the ALB hostname. | ACM, Route53 |

---

## 6. Observability (`deploy/helm/observability`, `app/control_plane/`)

Opt-in (`deploy_observability=true`) Prometheus/Grafana/Alertmanager baseline
plus first-party OpenTelemetry tracing and content-safe logging.

| Component | Role | Tech |
| --- | --- | --- |
| **observability umbrella chart** | Wraps kube-prometheus-stack + pre-configured vLLM/DCGM scrape + dashboards + alert rules into namespace `monitoring`. | Helm umbrella, **kube-prometheus-stack 72.9.1** |
| **Prometheus** | Scrapes all ServiceMonitors + vLLM/DCGM pods. Retention 15d/10GB (prod), 3d/2GB (dev). | Prometheus Operator |
| **Grafana** | Dashboards UI; sidecar auto-loads ConfigMaps labelled `grafana_dashboard=1`. Reached via port-forward (no Ingress). | Grafana |
| **Alertmanager + rules** | `InferenceQueueDepthHigh`, `InferenceReplicaUnavailable`, `ControlPlaneNotReady`. Disabled in dev to save resources. | PrometheusRule |
| **Dashboards** | DCGM GPU, vLLM Performance, vLLM Query (vendored, Apache-2.0), + first-party Control Plane Retrieval (M10). | Grafana JSON |
| **Control-plane metrics** (`metrics.py`) | `GET /metrics` golden-signal + inference/DB/retrieval histograms; path sanitised to bound cardinality; **no** content/tokens/ids in labels. | prometheus-client (no-op fallback) |
| **OTel tracing** (`tracing.py`) | Per-request spans, OTLP/gRPC exporter, W3C propagation to vLLM; no-op when `OTEL_EXPORTER_OTLP_ENDPOINT` unset. | opentelemetry-sdk |
| **DCGM GPU exporter** | GPU utilisation/memory/temp/power metrics (installed via `cluster-addons`, scraped by the observability config). | NVIDIA DCGM Exporter |

---

## 7. Helm charts & CI/CD

**Charts** (`deploy/helm/`, namespaces in parentheses): `private-ai-workspace`
(app — **core, always deployed**), `private-ai-ui` (app), `vllm` (inference),
`embedding` (inference), `media-service` (inference), `model-installer`
(inference), `tool-runner` (agent-jobs), `observability` (monitoring),
`external-dns` (kube-system), `cluster-addons` (kube-system — ESO + NVIDIA
device plugin + DCGM always; cluster-autoscaler/Karpenter/prometheus-adapter
opt-in). Everything except the control plane and `cluster-addons` base is
**opt-in**.

**CI/CD** (`.github/workflows/`):

| Workflow | Role | Tech |
| --- | --- | --- |
| `ci.yml` | Gates every push/PR on structure + `python3 -m compileall` + `python3 -m unittest discover` (stdlib-only, no pip). | GitHub Actions |
| `codeql.yml` | CodeQL security-and-quality for python + javascript-typescript, push/PR + weekly. | github/codeql-action |
| `deploy.yml` | `workflow_dispatch`: OIDC-assume AWS role → build/push images (SHA-tagged) → `helm upgrade` the core chart + opt-in charts by toggle. | configure-aws-credentials, ECR login, Helm v3.17.4 |

See [`install.md`](install.md) for the ordered end-to-end procedure, the repo
variables/secrets, and the monitoring walkthrough.
