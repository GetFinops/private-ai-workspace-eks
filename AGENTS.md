# AGENTS.md

AI-agent instructions for contributing to `private-ai-workspace-eks`.

## Project Posture

This repository is a bootstrap for a self-hosted, multi-user AI workspace
designed for private organizational deployment on AWS EKS.

Treat the project as:

- self-hosted
- organization-private
- maintainer-controlled
- not a shared multi-tenant SaaS platform

Do not describe the project as the official Odysseus project or an AWS-endorsed
derivative.

## Current Stage

> Operational state moves faster than this policy doc. For the current build,
> test, and dev-cluster commands and the live milestone status, see
> [`CLAUDE.md`](CLAUDE.md). The platform baseline (M0–M6) plus the product
> surface (M9), retrieval+memory (M10), and the M11 agent/tool-framework first
> increment are delivered and validated on a live dev EKS cluster.

The durable priorities below still hold regardless of stage:

- keep structure clean
- keep governance strong
- keep architecture explicit
- avoid premature feature sprawl

Prefer small, reviewable changes over broad speculative implementation.

## Repository Boundaries

- `app/`: control-plane application logic, auth, sessions, orchestration, APIs
- `infra/`: infrastructure provisioning, cloud resources, IAM, networking
- `deploy/`: Kubernetes packaging, manifests, Helm, environment values
- `scripts/`: local tooling and helper automation
- `tests/`: integration, architecture, and regression coverage
- `docs/`: public documentation only

Do not blur these boundaries without a strong reason.

## Architecture Direction

Follow the two-plane design documented in the planning bundle:

- control plane on CPU-oriented workloads
- inference plane on isolated GPU-backed vLLM workloads

The control plane must remain usable even when GPU capacity is cold, degraded,
or unavailable.

## Production Constraints

For production-oriented changes, prefer these assumptions:

- externalized state
- managed secrets
- managed or explicitly deployed relational database
- object storage instead of local artifact assumptions
- observability from day one

Avoid building new features around:

- SQLite as the intended production database
- local bind-mounted persistent state
- plaintext credentials
- auth shortcuts or bypasses
- hidden coupling between app and inference internals

## Security Red Lines

Never introduce or preserve changes that:

- store secrets in plaintext
- log secrets, prompts, tokens, or private user content without clear policy
- weaken auth or authorization checks
- assume anonymous or localhost bypasses are acceptable for hosted deployments
- expose internal-only inference services publicly
- reduce isolation between users or organizations

Auth, secret handling, deployment exposure, and data isolation are sensitive
areas and should be treated conservatively.

## Licensing And Provenance

This repository is MIT-licensed, but upstream-inspired work may carry notice
obligations.

When adapting or copying code:

- preserve required upstream notices
- document provenance in the PR
- avoid introducing AGPL-sensitive optional features by default
- do not vendor third-party code casually

If provenance is unclear, stop and ask for review instead of guessing.

## Change Guidelines

Prefer changes that are:

- minimal
- explicit
- reversible
- well-documented
- easy to review

When making non-trivial changes:

- update docs if behavior, deployment, or governance changes
- add or update tests when the risk justifies it
- include validation notes

## What To Avoid

Avoid:

- inventing architecture not grounded in the documented direction
- broad refactors without a clear problem statement
- introducing optional integrations just because they are available upstream
- mixing infra and application logic in the same change without need
- marketing claims that exceed the current maturity of the project

## Pull Request Expectations

AI-assisted contributions should make it easy for maintainers to review:

- state the problem
- summarize the change
- note test or verification coverage
- note any security, licensing, or provenance impact

Follow the contribution model in `CONTRIBUTING.md`, the reporting guidance in
`SECURITY.md`, and the attribution expectations in `NOTICE`.

## Contributor Legal Model

Commits are expected to use DCO sign-off:

```text
Signed-off-by: Your Name <you@example.com>
```

## Escalate Instead Of Guessing

Stop and ask for maintainer input if a change touches:

- auth or session semantics
- secrets handling
- branch protection or governance
- licensing or provenance uncertainty
- AGPL-sensitive components
- production networking exposure
- tenant or user isolation behavior

## Cursor Cloud specific instructions

### Current repository stage

`main` is a governance and layout bootstrap. There are no third-party Python or
Node dependencies to install, no Docker Compose stack, and no Kubernetes cluster
required for local verification today.

The CI workflow in `.github/workflows/ci.yml` only verifies required policy
files and top-level directories exist.

### When the control-plane skeleton is present

The open implementation branch adds a stdlib-only Python 3.11+ control plane
under `app/control_plane/`. Once that code is on your branch:

- **Requirements:** Python 3.11 or newer (`python3 --version`). No `pip install`
  step is needed; the skeleton uses the standard library only.
- **Tests (same as CI):**

  ```bash
  python3 -m compileall app tests
  python3 -m unittest discover -s tests
  ```

- **Run the dev server:**

  ```bash
  python3 -m app.control_plane
  ```

  Default bind: `http://0.0.0.0:8080`. Useful smoke endpoints:

  - `GET /healthz` — liveness (works without external deps)
  - `GET /readyz` — readiness (reports missing DB/auth/inference config)
  - `GET /v1/inference/status` — internal inference configuration status

- **Optional prod-style env vars** (for readiness checks): `DATABASE_URL`,
  `OBJECT_STORAGE_BUCKET`, `INFERENCE_BASE_URL`, `AUTH_ISSUER_URL`,
  `AUTH_AUDIENCE`, `AUTH_ADMIN_GROUP`. See `README.md` on branches that include
  the control plane.

### Services

| Service | Required locally? | Notes |
|---------|-------------------|-------|
| Control plane (`python3 -m app.control_plane`) | When `app/control_plane/` exists | CPU-only HTTP skeleton |
| PostgreSQL / S3 / vLLM | No for local smoke test | Required for production readiness |
| EKS / Terraform / Helm | No for app dev | Used for deployment milestones |

### Lint

No dedicated linter is configured yet. Use `python3 -m compileall app tests` as
the compile check until ruff/mypy or similar is added.
