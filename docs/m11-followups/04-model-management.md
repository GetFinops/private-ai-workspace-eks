# M11 Follow-up #4 — Model management & self-serve install

> Status: **Phase 0 + 1a shipped.** Phase 0 = read-only catalog + live GPU
> status. Phase 1a = tenant-scoped install-**request** records, gated by a
> **per-user permission** (kill-switch + HF allow-list + per-tenant cap;
> cross-tenant isolation tests; NO cluster mutation, NO HF token). There is **no
> in-app approval step** — holding the permission is the authorization; apply
> stays an out-of-band operator/pipeline step. Permissions are documented in
> [`../14-user-permissions.md`](../14-user-permissions.md). Phases 1b→3 remain
> **design + escalation register** — not built.

The web UI now has a **Models** screen and a **GPU cold-start** flow. This
document is the durable design behind them: how far self-serve model
installation can go on this platform, what ships now, and what must clear
security/provenance review before it is built.

It exists because the request — *"a Models screen to install/manage models
including Hugging Face config"* — describes a capability that, taken literally
(the control plane downloads arbitrary Hugging Face weights and reconfigures the
GPU inference plane on a user's click), touches **five** `AGENTS.md` red lines at
once: cluster mutation, new outbound/supply-chain surface, secret handling,
tenant isolation, and licensing/provenance. An adversarial design review
**rejected building that pipeline as proposed** and prescribed the phased,
least-privilege path below.

## 1. Scope & non-goals

**Ships now (Phase 0):** a read-only model catalog and honest, live GPU
warm/cold/loading status. No new privilege, egress, or secret handling.

**Explicit non-goals (this workstream, v1):**
- The control plane never holds `kubectl`/`helm` or any cluster-mutation RBAC.
- The control plane never holds, proxies, echoes, or logs a Hugging Face token.
- No per-tenant *private* GPU model in v1 — served models are a **cluster-global
  shared asset**; per-tenant control is *entitlement/visibility*, not isolation
  of the weights themselves.
- No auto-derivation of vLLM serving params that could CrashLoop a GPU node.

## 2. Ground-truth constraints (why the obvious design is wrong)

- **vLLM serves exactly one base model per server process.** "Runtime model
  swap" is a pod restart with head-of-line blocking and no isolation — no better
  than a redeploy. Serve N models as **N Deployments**, each Karpenter
  scale-to-zero, fronted by a name→Service router.
- **GPU nodes are Karpenter scale-to-zero (~120 s cold start).** The first
  request wakes the node; the control plane already returns `503 + Retry-After`
  during this window (see `server.py` degraded path).
- **Deploys are the existing GitHub Actions `helm upgrade`.** There is **no
  ArgoCD/Flux** deployed today — a "GitOps reconciler" is itself a new,
  separately-reviewed project, not a free primitive.
- **The control plane is stdlib-only** (no boto3/kubectl); it reads/writes RDS
  and brokers HTTP to trusted internal services (the M11 `job_executor.py` →
  tool-runner pattern). Reuse that boundary; do not widen it.
- **A weight download at pod start re-downloads on every cold start** (a fresh
  Karpenter node has an empty disk). For 8–70B models that is minutes of repeated
  HF egress. Weights must be **staged once** to a shared, commit-SHA-keyed,
  read-only cache and mounted, never pulled per-pod.

## 3. Trust boundary — who does what

```
 user ──▶ control plane (RDS intent + status reads ONLY; no cluster mutation)
                │  writes ModelInstallRequest row, emits operator notification
                ▼
          operator review + approval  (human gate; a request is a proposal)
                │
                ▼
          apply happens OFF the control plane:
          near-term = a maintainer merges a values change → existing
          GitHub Actions `helm upgrade` renders a per-model vLLM release
                │
                ▼
          GPU inference plane: Karpenter node, vLLM loads from the read-only
          commit-SHA weight cache; irsa-vllm reads the HF token (ESO), never
          the control plane
```

The internet-facing, multi-tenant, auth-bearing control plane holding
cluster-mutation RBAC is **rejected outright** — it turns any request-handling
bug or authz gap into cluster compromise, the single largest escalation in the
repo.

## 4. Least-privilege apply — ranked

1. **Intent record + human-merged CI apply (near-term).** The control plane
   writes a desired-state record; a maintainer applies it via the already-trusted
   Deploy workflow. Zero new standing privilege, zero cluster-mutation RBAC, zero
   secret write. **Recommended.**
2. **GitOps + a real reconciler (mid-term).** Only once an ArgoCD/Flux
   controller is stood up under its own scoped identity and signed off. The
   mutating identity reconciles human-reviewed git state, never a request handler.
3. **Namespace-scoped `model-installer` controller (Phase 3+, escalation).**
   Consumes *only operator-approved* records; scoped to create/patch the vLLM
   release in the `inference` namespace — no cluster-wide verbs, no node/RBAC
   access, no broad secret read/write. Acceptable only after security +
   provenance review and only if (1)/(2) latency proves unacceptable.

## 5. Data model (RDS; plain SQL migration, stdlib store like `session_postgres.py`)

- `model_registry` (**global**): `id, hf_repo_id, revision (pinned commit SHA),
  served_model_name (unique slug), display_name, status (enum), sha256_manifest,
  size_bytes, quantization, max_model_len, dtype, tensor_parallel_size,
  gpu_profile, cache_uri, error_class (shape-safe), requested_by,
  requested_by_tenant, timestamps`.
- `model_access` (**per-tenant entitlement join — where isolation is enforced**):
  `id, model_id FK, tenant_id, enabled, granted_by, created_at`.
- `model_install_request`: tenant-scoped intent (`hf_repo_id, revision,
  requested_params, status: requested→approved|rejected→applied|failed,
  error_class, requested_by, tenant_id, timestamps`).
- `install_audit` (content-safe, M5): `actor_subject, action, model_id,
  status_transition, error_class, latency` — never weights, token, or user
  content.
- `hf_repo_allowlist` (operator-managed, deny-by-default).
- `hf_token` marker: `configured bool, last_rotated_at` — **the token value is
  never in RDS or the control-plane store.**

## 6. Lifecycle state machine

`requested → approved → provisioning → downloading → loading → ready | failed(error_class)`;
`ready → removing → removed`. A single component (the apply path) is the sole
writer of status; vLLM `/health` is **continuously reconciled** back into
`status` so a pod that OOMs after `ready` cannot leave a stale `ready`.

## 7. Front-end data contract (what the shipped UI codes against)

- `GET /v1/models` — **enhanced, back-compat additive (shipped).** Keeps
  `{models, default}` for the chat `<select>`; adds `items[]` (per-model rows)
  and a **deny-by-default `capabilities{}`** block computed server-side plus
  `phase`. Management actions render **disabled** while their capability is
  `false`.
- `GET /v1/inference/status?probe=1` — **enhanced (shipped).** Adds
  `state ∈ warm|loading|cold|unconfigured|unknown`, `model`, `detail`
  (content-safe enum), `progress`/`eta_seconds` (null placeholders),
  `updated_at`. Consumed by **both** the chat cold-start flow and the Models
  screen via one poller.
- **Status-pill mapping** (client, `createElement`/`textContent` only):
  `warm→Ready` (green), `loading→Loading` (amber), `cold→Idle (cold)` (grey),
  `failed→Failed` (red), `unknown→Status unavailable`.
- **Install requests** (Phase 1a, shipped): `POST /v1/models/install-requests`
  (permission-gated create) and `GET` (own requests; admins see all; returns
  `can_request` so the UI enables the action per-user). **No in-app approval
  endpoint** — the operator PATCH/approve step is intentionally omitted; a
  permitted user's request is authoritative and applied out-of-band.
- **Still-gated endpoints** (render disabled until the matching `capabilities.*`
  flips; unbuilt endpoints return **HTTP 501**): `GET /v1/models/search`,
  `PUT /v1/models/hf-token`, `POST /v1/models/{id}/warm`,
  `DELETE /v1/models/{id}`.
- **Warm-up** in Phase 0 is client-side: a 1-token `POST /v1/chat/completions`
  triggers Karpenter scale-from-zero — no new endpoint.

## 8. Mandatory controls (any phase that mutates or touches the token)

1. No `kubectl`/`helm`/cluster-mutation RBAC and no infra-secret-write on the
   control plane — ever. It writes RDS intent rows and reads status only.
2. Apply happens off the control plane (§4).
3. Human/operator approval gate before any weights are pulled or any workload
   changes. `requested→approved` is explicit; a tenant request is a proposal.
4. `MODEL_INSTALL_ENABLED` kill-switch, default **off** (mirror
   `AGENT_TOOLS_ENABLED`); install/remove/token/search endpoints admin-only.
5. Deny-by-default HF org/repo allow-list (parse like `agent_tools.parse_allowlist`
   — empty/invalid ⇒ deny all).
6. Pin an **immutable commit SHA** at request time; never serve a moving
   branch/tag (provenance red line). Record the pin + `sha256` manifest.
7. `trust_remote_code=false` enforced **always**; models requiring it are
   rejected (operator-only exception).
8. Size cap + `sha256` verification + pickle/malware scan **before** mount/serve,
   on the inference/CI plane — never in the stdlib control plane.
9. HF egress happens **only on the inference plane** (download Job / vLLM),
   allow-listed via NetworkPolicy. Catalog *search* proxying, if ever built, goes
   only through the existing `outbound.py` SSRF chokepoint.
10. HF token stays in Secrets Manager via ESO + `irsa-vllm` read-only; the
    control plane never holds/proxies/echoes/logs it; the UI shows a presence
    flag only. Keep operator-set out-of-band until a brokered write independently
    clears review.
11. Gated/licensed models (Llama, etc.): never auto-bypass; surface
    `error_class=license_not_accepted` and require a recorded operator license
    attestation.
12. Tenant isolation at the storage layer **and** re-checked per request
    (`model_registry` global + `model_access` join; `/v1/models` filters by
    tenant; served-model routing is tenant-scoped). Every isolation-sensitive
    endpoint needs a cross-tenant regression test.
13. Per-tenant install rate limit + quota + global concurrency cap (reuse
    `agent_tools.RateLimiter`) to stop install-spam pinning GPU nodes.
14. Per-tenant/per-model GPU cost attribution.
15. Content-safe audit; any new structured log field is whitelisted in
    `logging_config.py` with a proven no-value-leak.
16. Weights from an immutable, commit-SHA-keyed, **read-only** shared cache
    (Mountpoint-for-S3 preferred, EFS fallback); reject AZ-bound RWO EBS.
17. Fail-fast GPU-fit validation (params/quant/TP/`max_model_len` vs VRAM) →
    `failed(oom|unschedulable)` **before** provisioning; admin override path.
18. Continuous `/health` reconcile into status (§6).
19. Escalate to a maintainer before merge — this surface touches secrets,
    production networking, provenance, and isolation simultaneously.

## 9. Phasing

| Phase | Scope | New privilege | Verdict |
| --- | --- | --- | --- |
| **0** | Read-only catalog (`items[]` + `capabilities{}`) + live GPU status (`?probe=1`) + client-side warm-up + cold-start UX | none | **shipped** |
| **1a** | Tenant-scoped **install-request** records + requester notification; `GET/POST /v1/models/install-requests`; **permission-gated (no in-app approval)**; apply stays human/CI | none (no cluster mutation) | **shipped**, gated by `MODEL_INSTALL_ENABLED` (default off; on for dev) **and** a per-user permission (`MODEL_INSTALL_ALLOW_ALL_USERS` for dev / `MODEL_INSTALL_GROUP` / admin — see `../14-user-permissions.md`), deny-by-default HF allow-list, per-tenant rate limit + open-request cap; cross-tenant isolation tests |
| **1b** | Brokered HF-token write (`PUT /v1/models/hf-token`) | secret write | **escalate** — independent security review + M5 no-leak proof; until then, operator-set out-of-band |
| **2** | Catalog search proxy (`GET /v1/models/search` via `outbound.py`) | HF egress from control plane | **escalate** |
| **3** | Actual apply — the **scoped `model-installer` reconciler** (`app/model_installer/`): a separate component that picks up requests and makes the model servable (scale the GPU nodegroup 0→1, point vLLM at the model via a ConfigMap-backed `MODEL_ID`, wait ready, mark `applied`). Auto (`requested→installing→applied/failed`), no operator. | cluster mutation **off the control plane** — its own scoped IRSA (`eks:UpdateNodegroupConfig` on GPU nodegroups only) + namespace RBAC (patch only the vLLM ConfigMap+Deployment) | **shipped for dev, escalation-flagged** (§4 option 3). Control plane gains no infra rights (verified). Controls in place: deny-by-default allow-list (re-checked by the reconciler) + repo-id format re-check, one-install-at-a-time cap, kill-switch halts auto-install, **rollback** to the prior model on failed load, **`--load-format safetensors`** (refuses pickle/`.bin` → closes the torch.load RCE vector), `trust_remote_code` at vLLM's safe default. **Open follow-ups (tracked):** commit-SHA pinning (§8.6 — `revision` is captured but not yet applied; serves the default branch), model size / GPU-fit validation (§8.17 — keep the dev allow-list to small models), weight cache (§8.16), and the inherent **shared-inference caveat**: one vLLM release serves one model, so an install swaps the model for all tenants (per-model releases are a later increment). |

## 10. Test & audit plan

Cross-tenant isolation tests (requests + `model_access`); probe
warm/cold/loading/unknown + timeout-degradation tests (shipped in
`tests/test_control_plane_inference.py` / `test_control_plane_routes.py`);
`logging_config.py` whitelist proof for any new field; allow-list
deny-all-on-empty; kill-switch-off test.

## 11. Open questions / escalation register

- Operator-only install vs user-request+approve as the default posture.
- Stand up ArgoCD/Flux, or stay on human-merged CI apply?
- Brokered token write vs out-of-band — the deciding review.
- Weight-cache backend (Mountpoint-for-S3 vs EFS) and GPU quota/chargeback
  governance.
- License-attestation mechanism for gated models.

## 12. Provenance & PR expectations

DCO sign-off; a security/licensing/provenance impact statement on every PR in
this workstream; **maintainer escalation before merge** for any phase ≥ 1b.
