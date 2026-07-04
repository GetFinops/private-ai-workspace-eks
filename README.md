# Private AI Workspace on EKS

`private-ai-workspace-eks` is an open-source bootstrap for a self-hosted,
multi-user AI workspace designed for private organizational deployment on
AWS EKS.

[![CI](https://github.com/GetFinops/private-ai-workspace-eks/actions/workflows/ci.yml/badge.svg)](https://github.com/GetFinops/private-ai-workspace-eks/actions/workflows/ci.yml)
[![CodeQL](https://github.com/GetFinops/private-ai-workspace-eks/actions/workflows/codeql.yml/badge.svg)](https://github.com/GetFinops/private-ai-workspace-eks/actions/workflows/codeql.yml)
[![GitHub Advanced Security](https://img.shields.io/badge/GitHub_Advanced_Security-CodeQL_%2B_Dependabot-2088FF?logo=github&logoColor=white)](https://github.com/GetFinops/private-ai-workspace-eks/security)
[![Dependabot](https://img.shields.io/badge/Dependabot-enabled-025E8C?logo=dependabot&logoColor=white)](.github/dependabot.yml)
[![Security policy](https://img.shields.io/badge/security-policy-informational)](SECURITY.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

> **Security.** This repository uses **GitHub Advanced Security** — [CodeQL
> code scanning](https://github.com/GetFinops/private-ai-workspace-eks/security/code-scanning),
> [Dependabot](.github/dependabot.yml) version + security updates, and secret
> scanning — alongside branch protection with required review. Scanning results
> live in the [Security tab](https://github.com/GetFinops/private-ai-workspace-eks/security);
> report vulnerabilities per [`SECURITY.md`](SECURITY.md).

> [!IMPORTANT]
> **Licensing & attribution.** This is an independently maintained,
> **MIT-licensed** project. It is **not** the official Odysseus project and is
> **not** endorsed by the Odysseus maintainers or AWS. Some control-plane and UI
> patterns were *adapted* from the **MIT-licensed Odysseus v1.0 snapshot**
> (`pewdiepie-archdaemon/odysseus`, commit `e5c99a5`, 2026-05-31). Upstream
> Odysseus **relicensed to AGPL-3.0-or-later on 2026-06-09**; those MIT-snapshot
> adaptations predate the relicense and remain usable under MIT, but **no further
> code may be adapted from the current (AGPL) upstream** — any new feature
> inspired by Odysseus must be built clean-room (independent implementation, not
> adapted from its source). Full provenance and the preserved upstream notices
> are in [`NOTICE`](NOTICE).

**Acknowledgments.** This project is built on the work of many open-source
projects, standards, and communities — see [`ACKNOWLEDGMENTS.md`](ACKNOWLEDGMENTS.md)
(legal provenance is in [`NOTICE`](NOTICE)).

## Positioning

This project is intended for:

- SMB and enterprise teams running a dedicated deployment inside their own environment
- private model serving via vLLM or compatible inference backends
- maintainer-controlled open-source development

This project is not presented as the official Odysseus project or an
AWS-endorsed derivative.

## Current Status

The project is delivered as a sequence of milestones
(`docs/10-delivery-roadmap.md`). The platform baseline (M0–M6) and a
minimum pre-Phase-2 hardening pass (M7a) precede a committed Phase 2 feature
track (M9–M14, individually adoption-gated). The original single-pass M7 has
been split: M7a runs before Phase 2 and M7b runs after, against the combined
platform + feature surface. M8 is the public production release at the end.

Per-milestone build instructions live in `docs/milestones/`.

| Milestone | Scope | Status |
| --- | --- | --- |
| M0 | Project bootstrap, governance, docs | Complete |
| M1 | Control-plane skeleton: authenticated chat path, OIDC token verification, session-store interface | Complete |
| M2 | EKS baseline: Terraform (VPC/EKS/ECR/RDS/S3), IRSA, ingress, External Secrets, CI/CD | Complete |
| M3 | Stateful dependency externalization (managed DB, object storage, session store) | Complete |
| M4 | Inference plane MVP (isolated vLLM on GPU) | Complete |
| M5 | Observability baseline (metrics, logs, traces) | Complete |
| M6 | Elastic GPU scaling (Karpenter + HPA + degrade-only fallback) | Complete |
| M7a | Platform hardening (minimum pass on M6 surface) | Complete — paper review + sweeps + both live drills (rollback, backup/restore) passed on dev 2026-07-04 ([report](docs/m7a-report.md)) |
| M9 | Product surface: vanilla JS SPA (Odysseus-derived design tokens, OIDC PKCE), notifications service, nginx chart | Complete (dev) |
| M10 | Retrieval + per-user memory on pgvector | Complete (dev) |
| M11 | Agent + tool framework (sandbox, agent loop, Job sandbox, deep-research) | Complete (dev) |
| M12 | MCP integration layer (sandboxed, per-tenant, deny-by-default) | Complete (dev) |
| M13 | Personal-info integrations: shared harness + Google Calendar (first provider) | Harness complete (dev-validated); providers adoption-gated |
| M14 | Phase 2: media services — adoption-gated | Planned |
| M7b | Full staging hardening across platform + adopted Phase 2 features | Planned (post–Phase 2) |
| M8 | Public production release | Planned (post–Phase 2) |

Execution order: **M0–M6 → M7a → Phase 2 (M9–M14) → M7b → M8.**

A component-level comparison of what is built versus planned is maintained in
`docs/11-gap-analysis.md`. The Phase 2 feature track and its adoption
governance live in `docs/12-phase-2-feature-adoption.md`.

## Architecture Direction

The target architecture follows a two-plane EKS design:

- a CPU-oriented control plane for API, auth, sessions, orchestration, and
  background work
- an isolated GPU-backed inference plane for vLLM or compatible model-serving
  workloads

The control plane should remain healthy and operationally visible when GPU
capacity is cold, scaling, or unavailable.

Diagrams are maintained as diagram-as-code: the AWS infrastructure baseline with
[`awsdac`](https://github.com/awslabs/diagram-as-code) (classic AWS reference
style with the AWS Cloud frame, VPC, and subnets), conceptual diagrams with the
[`diagrams`](https://diagrams.mingrammer.com/) library, and software views as
UML with PlantUML. Sources and regeneration instructions are in
[`docs/diagrams/`](docs/diagrams/README.md); regenerate with
`scripts/generate-diagrams.sh`.

### Phase 1 — Platform Baseline (M0–M6 + M7a)

The platform baseline, drawn in the classic AWS reference style: an AWS Cloud
frame containing the VPC with public and private subnets across two Availability
Zones. A public ALB fronts the CPU control-plane node group; the GPU inference
node group (vLLM) stays private/internal-only; regional managed services hold
state, secrets, images, and telemetry; the control plane verifies bearer tokens
against an OIDC issuer.

![Phase 1 platform baseline architecture](docs/diagrams/phase1_baseline.png)

### Phase 2 + Closeout — Target Production Topology

Target topology after Phase 2 features (M9–M14, individually adoption-gated)
have been adopted and have passed M7b's full staging soak. This is the
surface the public production release (M8) will ship. Compared to the
Phase 1 diagram it adds a UI tier (M9), a sandboxed agent runtime (M11),
an MCP gateway (M12), optional media services on the GPU node group (M14),
the pgvector extension on RDS for retrieval (M10), and an optional
external-integrations egress lane (M13).

![Phase 2 + closeout platform baseline](docs/diagrams/phase2_baseline.png)

### Phase 2 — Component view (proposed features)

Component-level view of the Phase 2 feature track, complementary to the AWS
topology above. See `docs/12-phase-2-feature-adoption.md` for the licensing
and security analysis. Components in the right-hand group are excluded from
the default build (AGPL-sensitive or non-vendored, e.g. arbitrary shell
execution) and shown only for context.

![Phase 2 proposed feature additions architecture](docs/diagrams/phase2_features.png)

### Delivery and Software Views

The CI/CD and image supply chain, plus UML component and request-flow views of
the control plane, are in the [diagram gallery](docs/diagrams/README.md).

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
- **external-provider inference fallback** (OpenAI, Bedrock, etc.) — the
  platform is self-hosted and organization-private by design; when GPU
  capacity is unavailable the control plane returns `503 + Retry-After`
  rather than forwarding prompts to a third party.  See
  [`docs/09-scaling-policy.md`](docs/09-scaling-policy.md) for the full
  fallback policy.

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
- **Pre-production gap plan (UI + functional surface, before M7b/M8):** `docs/13-pre-production-gap-plan.md`
- Phase 2 feature adoption track (proposed): `docs/12-phase-2-feature-adoption.md`
- Per-milestone build instructions: `docs/milestones/`
- **Building an integration module (M13 standard):** `docs/m13-followups/01-integration-module-guide.md`
- Observability content & telemetry policy: `docs/07-observability.md`
- Scaling and fallback policy (M6): `docs/09-scaling-policy.md`
- Cost estimates: `ESTIMATION_COSTS.md`

## Governance

- pull requests are required for default-branch changes
- at least one maintainer review is required
- force-pushes to the protected branch are disabled
- contributors are expected to use DCO sign-off

See `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `NOTICE`.

## Licensing

The repository is released under the [MIT License](LICENSE). Attribution
expectations for upstream-inspired work — and the full provenance record — are
documented in [`NOTICE`](NOTICE).

Upstream provenance summary (see [`NOTICE`](NOTICE) → "Source 2" for the
authoritative detail):

- The Odysseus-derived control-plane and UI patterns were adapted from the
  **MIT-licensed Odysseus v1.0 snapshot** (commit `e5c99a5`, 2026-05-31). MIT
  permissions on a released snapshot are irrevocable, so those adaptations remain
  usable by this MIT project.
- Upstream Odysseus **relicensed to AGPL-3.0-or-later on 2026-06-09**. This
  project contains **no AGPL-licensed code** and adapts nothing from the
  post-relicense upstream. The prior "selective adaptation from upstream" posture
  no longer applies: new work must be **clean-room**. This boundary is owned by
  maintainer/legal review.
- The "Odysseus" name is used only nominatively, for attribution and comparison.
  No Odysseus logo, wordmark, or other brand asset is used, and nothing here
  implies endorsement by or affiliation with the Odysseus project or AWS.
