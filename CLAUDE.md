# CLAUDE.md

Operational guidance for Claude Code (and other AI coding agents) working in
`private-ai-workspace-eks`.

This file is the **quick operational reference**. The durable policy —
project posture, security red lines, licensing/provenance rules, escalation
triggers — lives in [`AGENTS.md`](AGENTS.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md).
**Read `AGENTS.md` first; it governs. This file does not repeat it.**

## What this project is

A self-hosted, multi-user AI workspace for **private organizational** deployment
on AWS EKS. Two-plane design: a CPU control plane (`app/control_plane/`) that
stays usable when GPU is cold, and an isolated GPU inference plane (vLLM). Not a
shared multi-tenant SaaS; not the official Odysseus project.

## Repository layout

| Path | Contents |
| --- | --- |
| `app/control_plane/` | Control-plane HTTP service: auth, sessions, chat, notifications, retrieval/memory, agent tools |
| `app/sandbox/` | Out-of-process tool sandbox (stdlib only — no config/boto3/DB imports) |
| `infra/terraform/` | VPC/EKS/RDS/S3/ECR, IRSA, Cognito, external-dns, Karpenter |
| `deploy/helm/` | Charts; `deploy/values/<env>/` holds per-env overrides |
| `scripts/` | `smoke-test.sh` and helper tooling |
| `tests/` | Unit/integration/architecture/regression tests |
| `docs/` | Public docs; `docs/milestones/` has per-milestone specs; `docs/10-delivery-roadmap.md` is the roadmap |

Keep these boundaries (see `AGENTS.md` → Repository Boundaries).

## Build, test, run

The control plane is **Python 3.11+ standard library only** for app logic — no
`pip install` to run or test it. (Production deploys add `psycopg[binary,pool]`
for RDS/pgvector; that is in the image, not needed for the unit suite.)

```bash
# Same checks as CI (.github/workflows/ci.yml → docs-and-structure job):
python3 -m compileall app tests
python3 -m unittest discover -s tests        # full suite

# Run the dev server (binds 0.0.0.0:8080):
python3 -m app.control_plane

# Local smoke test (spins up the server with a dev token verifier):
./scripts/smoke-test.sh
# Against a live deployment with real OIDC tokens:
./scripts/smoke-test.sh --base <url> --token "$ID_TOKEN" [--token-b "$OTHER_TENANT"]
```

Useful endpoints: `GET /healthz` (liveness), `GET /readyz` (readiness — reports
missing DB/auth/inference config), `GET /v1/inference/status`.

No linter/type-checker is wired yet; `compileall` is the compile gate.

## Dev cluster operations

A live dev EKS cluster exists (`private-ai-workspace-dev`). Operating it:

- **AWS:** `export AWS_PROFILE=personal AWS_REGION=us-west-2` (account
  `069133419519`). Pre-import the GitHub Actions OIDC provider before
  `terraform apply` or it fails `EntityAlreadyExists`.
- **Deploy:** dispatch the Deploy workflow — it builds the control-plane image
  from the branch and `helm upgrade`s, applying `-f deploy/values/dev/app.yaml`.
  Add-on charts (UI, embedding, observability, vLLM, external-dns) are
  opt-in inputs that default to `false`; leave them off unless you mean to
  touch them.

  ```bash
  gh workflow run deploy.yml --ref <branch> -f environment=dev
  ```
- **Reach the control plane:** `kubectl -n app port-forward
  svc/private-ai-workspace-private-ai-workspace 18080:8080`.
- **OIDC tokens for the dev smoke:** the control plane verifies the Cognito
  **ID token** (the access token lacks `aud`+`email`). Mint via
  `aws cognito-idp admin-initiate-auth ... --query AuthenticationResult.IdToken`.
  Tenant = the email domain. Reset test-user passwords out-of-band with
  `admin-set-user-password --permanent` (never commit credentials).

See `.claude/` memory and the per-milestone docs for environment specifics.

## Commits and PRs

- **DCO sign-off is required** on every commit (`CONTRIBUTING.md`):

  ```text
  Signed-off-by: Your Name <you@example.com>
  ```
- AI-assisted commits also carry a `Co-Authored-By:` trailer for the assistant.
- `main` is branch-protected (review required). Branch for changes; do not push
  to `main`. Merging is a maintainer decision — surface the validated PR rather
  than self-merging unless explicitly told to.
- PR description: state the problem, summarize the change, note
  test/verification coverage, and call out any security/licensing/provenance
  impact (`AGENTS.md` → Pull Request Expectations).

## Hard rules (see `AGENTS.md` for the full list)

- **Content policy (M5):** never log prompts, completions, tokens, secrets, or
  user content. Audit/telemetry record *shape* (key names, type/size, counts),
  never values. The JSON log formatter only surfaces whitelisted keys — if you
  add structured fields via `extra=`, whitelist them in
  `app/control_plane/logging_config.py` and prove no values leak.
- **Tenant/user isolation** is enforced at the storage layer and re-checked per
  request. Every isolation-sensitive feature needs a cross-tenant test.
- **Agent tools** run out-of-process, deny-by-default, with an operator
  kill-switch. Arbitrary exec / network egress / FS-write / credential-access
  tools are excluded by default. See the M11 docs below.
- **Escalate, don't guess** on auth/session semantics, secrets, branch
  protection, licensing/provenance, production networking, or isolation.

## Milestone status (high level)

Platform baseline M0–M6 complete; M7a hardening partial; product surface M9,
retrieval+memory M10, and the agent/tool framework M11 (first increment)
delivered and validated on dev. See `docs/10-delivery-roadmap.md` and
`README.md` for the authoritative table.

### M11 agent/tool framework

Shipped: out-of-process sandbox (`app/sandbox/`, framework in
`app/control_plane/agent_tools.py`), deny-by-default per-tenant allow-list,
kill-switch (`AGENT_TOOLS_ENABLED`), rate/concurrency limits, content-safe
audit, `POST /v1/agent/tools/invoke`, and `agent_task_completed` notifications.
Only an inert `text_stats` stub tool ships. Planned extensions are in
[`docs/m11-followups/`](docs/m11-followups/) — read them before building the
agent loop, deep-research, or any IO-capable tool.
