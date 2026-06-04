# Private AI Workspace on EKS

`private-ai-workspace-eks` is an open-source bootstrap for a self-hosted, multi-user AI workspace designed for private organizational deployment on AWS EKS.

## Positioning

This project is intended for:

- SMB and enterprise teams running a dedicated deployment inside their own environment
- private model serving via vLLM or compatible inference backends
- maintainer-controlled open-source development

This project is not presented as the official Odysseus project or an AWS-endorsed derivative.

## Current Status

This repository is currently in bootstrap mode. It includes:

- governance and contribution policy files
- an initial docs baseline
- CI and pull request scaffolding
- a minimal control-plane application skeleton under `app/`
- tests for the initial configuration, auth/session, readiness, and inference contract
- a Helm deployment skeleton under `deploy/helm/private-ai-workspace/`
- a Terraform baseline under `infra/terraform/`
- structured implementation directories for `infra/`, `deploy/`, `scripts/`, and `tests/`

## Planned Scope

- control plane for users, sessions, model routing, and orchestration
- EKS deployment packaging
- externalized state and managed secret handling
- local model inference integration via vLLM
- observability and scaling hooks for production deployments

## Initial MVP Scope

The first build milestone focuses on a control plane that can run without GPU
capacity being available. The initial application surface includes:

- `/healthz` for liveness
- `/readyz` for explicit external dependency readiness
- `/v1/inference/status` for internal inference-plane configuration status
- a vLLM-compatible chat-completions request contract
- initial auth and session domain primitives

Initial non-goals:

- public exposure of inference services
- bundled AGPL-sensitive optional features
- local bind-mounted production state
- SQLite as a production database default

## Architecture Direction

The target architecture follows a two-plane EKS design:

- a CPU-oriented control plane for API, auth, sessions, orchestration, and
  background work
- an isolated GPU-backed inference plane for vLLM or compatible model-serving
  workloads

The control plane should remain healthy and operationally visible when GPU
capacity is cold, scaling, or unavailable.

## Initial AWS Stack Decisions

The planning bundle recommends starting with:

- RDS PostgreSQL for relational state
- S3 for uploads and artifacts
- AWS Secrets Manager for managed secrets
- Terraform for infrastructure provisioning
- Helm for Kubernetes packaging
- CPU nodes for the control plane
- managed GPU node groups first, with Karpenter considered after the baseline
- Prometheus-compatible metrics with CloudWatch/AMP/AMG integration options

## Local Control-Plane Smoke Test

The current scaffold uses only the Python standard library:

```bash
python -m unittest discover -s tests
python -m app.control_plane
```

Production readiness requires external dependency configuration, for example:

```bash
DATABASE_URL=postgresql://example.invalid/workspace \
OBJECT_STORAGE_BUCKET=workspace-artifacts \
INFERENCE_BASE_URL=http://vllm.inference.svc:8000 \
AUTH_ISSUER_URL=https://issuer.example.com \
AUTH_AUDIENCE=private-ai-workspace \
AUTH_ADMIN_GROUP=workspace-admins \
python -m app.control_plane
```

## Repository Layout

```text
.
├── .github/
├── app/
├── deploy/
├── docs/
├── infra/
├── scripts/
└── tests/
```

## Governance

- pull requests are required for default-branch changes
- at least one maintainer review is required
- force-pushes to the protected branch are disabled
- contributors are expected to use DCO sign-off

See `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `NOTICE`.

## Licensing

The repository is released under the MIT License. Attribution expectations for upstream-inspired work are documented in `NOTICE`.
