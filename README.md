# Private AI Workspace on EKS

`private-ai-workspace-eks` is an open-source bootstrap for a self-hosted,
multi-user AI workspace designed for private organizational deployment on
AWS EKS.

## Positioning

This project is intended for:

- SMB and enterprise teams running a dedicated deployment inside their own environment
- private model serving via vLLM or compatible inference backends
- maintainer-controlled open-source development

This project is not presented as the official Odysseus project or an
AWS-endorsed derivative.

## Current Status

The project is delivered as a sequence of milestones (`docs/10-delivery-roadmap.md`).
The committed roadmap (M0–M8) builds the platform baseline; per-milestone build
instructions live in `docs/milestones/`.

| Milestone | Scope | Status |
| --- | --- | --- |
| M0 | Project bootstrap, governance, docs | Complete |
| M1 | Control-plane skeleton: authenticated chat path, OIDC token verification, session-store interface | Complete |
| M2 | EKS baseline: Terraform (VPC/EKS/ECR/RDS/S3), IRSA, ingress, External Secrets, CI/CD | Complete |
| M3 | Stateful dependency externalization (managed DB, object storage, session store) | Planned |
| M4 | Inference plane MVP (isolated vLLM on GPU) | Planned |
| M5 | Observability baseline (metrics, logs, traces) | Planned |
| M6 | Elastic GPU scaling | Planned |
| M7 | Staging hardening | Planned |
| M8 | Production release | Planned |

A component-level comparison of what is built versus planned is maintained in
`docs/11-gap-analysis.md`. Proposed product features beyond the baseline are
sequenced as a candidate M9+ track in `docs/12-phase-2-feature-adoption.md`.

## Architecture Direction

The target architecture follows a two-plane EKS design:

- a CPU-oriented control plane for API, auth, sessions, orchestration, and
  background work
- an isolated GPU-backed inference plane for vLLM or compatible model-serving
  workloads

The control plane should remain healthy and operationally visible when GPU
capacity is cold, scaling, or unavailable.

### Phase 1 — Platform Baseline (M0–M8)

The diagram below is the committed baseline architecture. It is rendered as a
native Mermaid diagram (diagram-as-code) so it stays in version control and
renders directly on GitHub. Cylinder nodes are managed data services; the
control plane and inference plane are isolated node groups inside private
subnets.

```mermaid
flowchart TB
    user(["Users / API clients"])
    dns["Route 53 (DNS)"]
    acm["ACM (TLS certificates)"]

    subgraph vpc["VPC"]
        subgraph public["Public subnets"]
            alb["Application Load Balancer"]
        end
        subgraph private["Private subnets"]
            subgraph eks["EKS cluster"]
                albc["AWS Load Balancer Controller"]
                eso["External Secrets Operator"]
                subgraph cp["Control plane (CPU node group)"]
                    app["Control-plane API: /healthz, /readyz, /v1/chat/completions"]
                end
                subgraph ip["Inference plane (GPU node group)"]
                    vllm["vLLM OpenAI-compatible service (internal-only)"]
                end
            end
        end
    end

    subgraph managed["Managed AWS services"]
        rds[("RDS PostgreSQL")]
        s3[("S3 (artifacts)")]
        sm[("Secrets Manager")]
        ecr[("ECR (images)")]
    end

    oidc["OIDC issuer (Cognito / Okta / Keycloak)"]

    subgraph obs["Observability"]
        prom["Prometheus / AMP"]
        graf["Grafana / AMG"]
        cw["CloudWatch (logs)"]
    end

    user --> dns --> alb
    acm -. "TLS" .-> alb
    alb --> app
    app --> vllm
    app --> rds
    app --> s3
    app -. "verify bearer token" .-> oidc
    eso -. "sync config" .-> app
    eso --> sm
    ecr -. "images" .-> app
    ecr -. "images" .-> vllm
    app --> prom
    vllm --> prom
    prom --> graf
    app --> cw

    classDef edge fill:#e8f0fe,stroke:#4285f4,color:#111;
    classDef data fill:#fff4e5,stroke:#ff9900,color:#111;
    classDef infra fill:#f3e8fd,stroke:#a142f4,color:#111;
    classDef compute fill:#e6f4ea,stroke:#34a853,color:#111;
    class user,dns,acm,alb edge;
    class rds,s3,sm,ecr data;
    class albc,eso,oidc,prom,graf,cw infra;
    class app,vllm compute;
```

### Phase 2 — Proposed Feature Additions (M9+)

The diagram below shows the proposed, maintainer-gated product features layered
on the Phase 1 baseline. It is exploratory and not committed scope; see
`docs/12-phase-2-feature-adoption.md` for the licensing and security analysis.
Dashed external nodes are excluded from the default build (AGPL-sensitive or
non-vendored) and shown only for context.

```mermaid
flowchart TB
    subgraph base["Phase 1 baseline (committed)"]
        api["Control-plane API"]
        vllm["vLLM inference"]
        rds[("PostgreSQL")]
        s3[("S3")]
        sm[("Secrets Manager")]
    end

    subgraph p2["Phase 2 product features (proposed, gated)"]
        gui["M9 Web UI / API client"]
        rag["M10 Retrieval / RAG"]
        agents["M11 Agent + tool framework (sandboxed)"]
        mcp["M12 MCP integration layer"]
        pim["M13 PIM integrations (optional)"]
        media["M14 Media services (optional)"]
    end

    subgraph ext["External / excluded by default"]
        vec[("Vector store: pgvector / managed")]
        search["External search service (AGPL, not vendored)"]
        shell["Arbitrary shell execution (excluded)"]
    end

    gui --> api
    api --> agents
    agents --> vllm
    agents --> mcp
    agents --> rag
    rag --> vec
    rag --> s3
    media --> vllm
    pim -. "hardened secret + URL layer" .-> sm
    mcp -. "opt-in, per-tenant" .-> ext
    agents -. "tools call (network only)" .-> search
    agents -. "excluded (multi-tenant)" .-> shell

    classDef baseline fill:#e6f4ea,stroke:#34a853,color:#111;
    classDef data fill:#fff4e5,stroke:#ff9900,color:#111;
    classDef feature fill:#e8f0fe,stroke:#4285f4,color:#111;
    classDef excluded fill:#fdecea,stroke:#d93025,color:#111,stroke-dasharray:4 3;
    class api,vllm baseline;
    class rds,s3,sm,vec data;
    class gui,rag,agents,mcp,pim,media feature;
    class search,shell excluded;
```

## Initial MVP Scope

The control plane runs without GPU capacity being available. The current
application surface includes:

- `/healthz` for liveness
- `/readyz` for explicit external dependency readiness
- `/v1/inference/status` for internal inference-plane configuration status
- `POST /v1/chat/completions`, an authenticated vLLM-compatible chat path that
  degrades gracefully when inference is unavailable
- OIDC bearer-token verification (RS256/ES256) with a development-only token
  verifier for local use
- auth and session domain primitives with a session-store interface

Non-goals:

- public exposure of inference services
- bundled AGPL-sensitive optional features
- local bind-mounted production state
- SQLite as a production database default

## Initial AWS Stack Decisions

The planning bundle recommends starting with:

- RDS PostgreSQL for relational state
- S3 for uploads and artifacts
- AWS Secrets Manager for managed secrets (synced via External Secrets Operator)
- Terraform for infrastructure provisioning
- Helm for Kubernetes packaging
- CPU nodes for the control plane
- managed GPU node groups first, with Karpenter considered after the baseline
- Prometheus-compatible metrics with CloudWatch/AMP/AMG integration options

Cost estimates for the provisioned infrastructure are in `ESTIMATION_COSTS.md`.

## Local Control-Plane Smoke Test

Install dependencies, run the tests, and start the service:

```bash
python3 -m pip install -e .
python3 -m unittest discover -s tests
python3 -m app.control_plane
```

For a local chat path in development mode, enable the development token
verifier (never set `DEV_AUTH_TOKEN` in staging or production):

```bash
ENVIRONMENT=development \
DEV_AUTH_TOKEN=local-dev-token \
INFERENCE_BASE_URL=http://vllm.inference.svc:8000 \
python3 -m app.control_plane
```

Production readiness requires external dependency configuration, for example:

```bash
DATABASE_URL=postgresql://example.invalid/workspace \
OBJECT_STORAGE_BUCKET=workspace-artifacts \
INFERENCE_BASE_URL=http://vllm.inference.svc:8000 \
AUTH_ISSUER_URL=https://issuer.example.com \
AUTH_AUDIENCE=private-ai-workspace \
AUTH_ADMIN_GROUP=workspace-admins \
python3 -m app.control_plane
```

## Repository Layout

```text
.
├── .github/        CI, deploy pipeline, issue and PR templates
├── app/            control-plane application (config, server, auth, inference)
├── deploy/         Helm charts (app, vLLM, observability, cluster add-ons)
├── docs/           planning bundle, gap analysis, milestone instructions
├── infra/          Terraform (VPC, EKS, ECR, RDS, S3, IRSA)
├── scripts/        local tooling and helper automation
└── tests/          control-plane and artifact tests
```

## Documentation

- Planning bundle and architecture: `docs/README.md`
- Delivery roadmap: `docs/10-delivery-roadmap.md`
- Gap analysis: `docs/11-gap-analysis.md`
- Phase 2 feature adoption track (proposed): `docs/12-phase-2-feature-adoption.md`
- Per-milestone build instructions: `docs/milestones/`
- Cost estimates: `ESTIMATION_COSTS.md`

## Governance

- pull requests are required for default-branch changes
- at least one maintainer review is required
- force-pushes to the protected branch are disabled
- contributors are expected to use DCO sign-off

See `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `NOTICE`.

## Licensing

The repository is released under the MIT License. Attribution expectations for
upstream-inspired work are documented in `NOTICE`.
