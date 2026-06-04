# Gap Analysis

## Public Disclosure Scope

This document is part of the public documentation set. It is written to be
safe for public exposure. It therefore:

- describes implementation gaps at the component and milestone level
- states the project's reuse posture (selective adaptation, not a fork)
- avoids publishing a file-by-file upstream porting list
- avoids restating specific upstream vulnerability details
- defers detailed provenance and security-sensitive porting decisions to
  maintainer review

Detailed per-file provenance mapping and any security-sensitive porting
decisions are handled through maintainer review and recorded in `NOTICE`, not
enumerated here. See `docs/04-governance-and-contribution.md` for the
escalation model.

## Purpose

This report compares the current state of the repository against the planned
scope in the planning bundle (`docs/03-implementation-plan.md`,
`docs/10-delivery-roadmap.md`, and the root `README.md`). It identifies what is
built, what is missing, and how remaining work should be approached.

## Method

The analysis reviewed:

- the root `README.md` scope and MVP definition
- the planning bundle in `docs/`
- the control-plane code under `app/`
- the infrastructure baseline under `infra/`
- the deployment scaffolding under `deploy/`
- the test suite under `tests/`
- the provenance record in `NOTICE`

## Current State Summary

The repository has completed project bootstrap and a partial control-plane
skeleton.

### Built

- **Governance**: `LICENSE`, `NOTICE`, `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, `CODEOWNERS`, issue and pull-request templates, and a
  CI workflow that verifies structure and runs tests.
- **Documentation**: the full planning bundle and the internal inference
  contract.
- **Control-plane skeleton** (`app/control_plane/`):
  - environment-driven configuration model
  - a standard-library HTTP surface exposing `/healthz`, `/readyz`, and
    `/v1/inference/status`
  - authentication and session domain primitives (types only)
  - a vLLM endpoint-routing and URL-normalization layer
  - a standard-library inference client able to call an OpenAI-compatible
    backend
- **Infrastructure baseline** (`infra/terraform/`): VPC, EKS, ECR, RDS, and S3
  modules wired through a root module.
- **Deployment scaffolding** (`deploy/helm/`): charts for the control plane,
  a vLLM service, and an observability stack.
- **Tests**: a standard-library test suite covering configuration, routes,
  auth and session primitives, the inference contract, and roadmap artifacts.

### Provenance posture

Selective upstream adaptation is already in use and recorded in `NOTICE`. The
inference routing and client logic were adapted from MIT-licensed upstream
patterns, and parts of the Terraform baseline were adapted from a permissive
AWS sample. The repository is intentionally a new project rather than a fork.

## Gap Table

The MVP scope calls for a chat and model-orchestration core, authentication and
admin controls, externalized persistence, and vLLM integration. Measured
against that scope:

| Area | Planned | Current state | Gap |
| --- | --- | --- | --- |
| Chat / orchestration | chat and model-routing core | endpoint URL building only | no chat endpoint; the inference client is not wired into the HTTP surface |
| Authentication | authentication and admin controls | identity and settings types only | no token verification, no login flow, no request-level enforcement |
| Sessions | externalized session state | in-memory session dataclass | no session store or persistence backend |
| Database | managed PostgreSQL | configuration variable only | no models, migrations, or database driver; readiness only checks variable presence |
| Object storage | S3 for artifacts | configuration variable only | no storage client or upload flows |
| Secrets | managed secret retrieval | provider name string only | no secret-manager integration |
| Observability | metrics, logs, traces | observability chart scaffold | no application metrics endpoint, instrumentation, or tracing |
| HTTP serving | horizontally scalable API | standard-library server, read-only routes | no write handling or production-grade serving stack |

Milestones beyond the control-plane skeleton (EKS deployment, state
externalization, inference-plane MVP, observability baseline, elastic GPU
scaling, staging hardening, and production release) are scaffolded in
infrastructure and deployment but not yet exercised end to end.

## Reuse Posture: Adapt Selectively, Do Not Fork

The planning bundle already decided on a new repository with selective reuse
rather than a wholesale fork, and that decision still holds. The rationale:

- Upstream inspiration is oriented toward local-first, single-host operation.
  Direct reuse would re-import local-state assumptions that conflict with this
  project's externalized-state, cloud-native direction.
- Some upstream optional features carry copyleft-sensitive licensing and must
  stay excluded from the default build.
- The MVP needs a much smaller feature surface than upstream provides.

### What to adapt

At the category level, the strongest candidates for adaptation are narrow,
permissively licensed building blocks that fit a stateless, externalized-state
control plane:

- request and endpoint validation patterns
- authentication and session-handling patterns, reworked for an external
  identity provider and an externalized session store
- hardened outbound URL validation and secret-handling patterns
- readiness and degraded-mode patterns

Each adaptation must preserve required upstream notices and record provenance
in `NOTICE`.

### What to build fresh

- the managed-database persistence layer
- object-storage flows
- managed-secret retrieval
- production-grade HTTP serving and request handling
- observability instrumentation

### What to exclude

- anything assuming a local default database, local data directories, or local
  vector storage
- copyleft-sensitive optional features
- the broad set of upstream features that fall outside the MVP

## Recommended Next Focus

The highest-value near-term work is completing the control-plane core:

1. wire the existing inference client into an authenticated request path
2. add real authentication verification and request-level enforcement
3. introduce a managed-database-backed persistence and session layer

These are the items that move the project from a skeleton to a usable control
plane, and they unblock the deployment and inference milestones that follow.

## Escalation Notes

Per `AGENTS.md` and `docs/04-governance-and-contribution.md`, several of the
gaps above touch sensitive areas that require maintainer review before
implementation:

- authentication and session semantics
- secret handling
- licensing and provenance decisions for any adapted code
- production networking exposure
- tenant and user isolation behavior

These should not be resolved unilaterally by an automated contributor.

## Milestone Instructions

Per-milestone build instructions for automated and human contributors live in
`docs/milestones/`. Each file expands a roadmap milestone into objective,
scope, tasks, provenance and security checkpoints, testing requirements, and
exit criteria.
