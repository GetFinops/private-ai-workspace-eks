# Milestone Instructions for AI Contributors

This directory contains one instruction file per delivery milestone. Each file
turns a milestone from `docs/10-delivery-roadmap.md` into concrete, build-ready
guidance for an automated contributor (and for humans reviewing automated
work).

These files are public documentation. They describe *how to build this
project*, not internal secrets. Detailed provenance mapping and
security-sensitive porting decisions stay in maintainer review and `NOTICE`.

## How to Use These Files

1. Identify the lowest-numbered milestone whose exit criteria are not yet met.
2. Read that milestone file in full before making changes.
3. Confirm the prerequisites are satisfied. Do not start a milestone whose
   dependencies are incomplete.
4. Work only inside the milestone scope. Defer anything listed under
   non-goals.
5. Prefer small, reviewable pull requests over large speculative ones.
6. Run the validation steps and meet the exit criteria before considering the
   milestone advanced.

## Standing Rules for Every Milestone

These rules apply to all milestones and are not repeated in full inside each
file.

### Change discipline

- Keep changes minimal, explicit, reversible, and well documented.
- Respect repository boundaries: `app/`, `infra/`, `deploy/`, `scripts/`,
  `tests/`, `docs/`. Do not blur them without a strong reason.
- Update documentation when behavior, deployment, or governance changes.
- Add or update tests when the risk justifies it.

### Provenance and licensing

- Adapt upstream code selectively; never vendor large amounts of third-party
  code casually.
- Preserve required upstream notices for any adapted code.
- Record provenance in `NOTICE` for every adapted file or subsystem.
- Keep copyleft-sensitive optional features out of the default build.
- If provenance is unclear, stop and request maintainer review.

### Security red lines

- Never store or log secrets, prompts, tokens, or private user content without
  an explicit, reviewed policy.
- Never weaken authentication or authorization checks.
- Never assume anonymous or localhost bypasses are acceptable for hosted
  deployments.
- Never expose internal-only inference services publicly.
- Never reduce isolation between users or organizations.

### Contribution mechanics

- Every commit must carry a DCO `Signed-off-by:` line.
- Default-branch changes go through pull requests with maintainer review.
- Do not force-push or rewrite shared history.

### Escalate instead of guessing

Stop and request maintainer input before implementing changes that touch:

- authentication or session semantics
- secret handling
- branch protection or governance
- licensing or provenance uncertainty
- copyleft-sensitive components
- production networking exposure
- tenant or user isolation behavior

### Dev deployment validation for Phase 2

This rule applies to every Phase 2 milestone (M9–M14) and is referenced
from each milestone's "Dev deployment validation" section rather than
duplicated in full.

Every Phase 2 milestone must be exercised end-to-end in the dev
deployment, not just in unit tests, before its exit criteria are
considered met. The dev-deployment smoke test for each milestone must:

1. Enable the milestone's feature in `deploy/values/dev/` against a
   freshly-deployed dev cluster (or one in a known-good state).
2. Exercise the feature's primary user path end-to-end through the
   public control-plane API.
3. Exercise the M1-adapted-from-Odysseus control-plane surfaces that
   the feature inevitably touches: `app/control_plane/routing.py`,
   `app/control_plane/inference.py`, and (when an authenticated path is
   tested) `app/control_plane/token_verifier.py`. This is the project's
   integration check that the upstream pattern adaptations still behave
   correctly once a real Phase 2 feature drives them.
4. Validate at least one cross-tenant or cross-user isolation case
   appropriate to the milestone (no leakage).
5. Validate the operator kill-switch or feature flag for any milestone
   that introduces one.
6. Capture the run in the milestone PR (logs, exit code, what was
   exercised). Failures block merge.

For features that produce events (M10 indexing, M11 agent tasks, M14
media tasks), the smoke test additionally verifies the producer event
reaches the M9 notifications feed when M9 is deployed in the same dev
cluster, and that cross-tenant publishers cannot emit into another
tenant's feed.

Dev-deployment cost is a real constraint: dev values use the smallest
viable models, the lowest `maxReplicas`, and a cold GPU pool by default
(per `../09-scaling-policy.md`). Smoke tests should be parameterised to
work within those bounds.

## Milestone Index

Execution order: **M0 – M6 → M7a → Phase 2 (M9–M14, adoption-gated) → M7b → M8.**
See `../10-delivery-roadmap.md` for the full dependency graph and
`../12-phase-2-feature-adoption.md` for Phase 2 governance.

### Platform baseline

- [M0 — Project Bootstrap](m0-project-bootstrap.md)
- [M1 — Control Plane Skeleton](m1-control-plane-skeleton.md)
- [M2 — EKS Baseline Deployment](m2-eks-baseline-deployment.md)
- [M3 — Stateful Dependency Externalization](m3-state-externalization.md)
- [M4 — Inference Plane MVP](m4-inference-plane-mvp.md)
- [M5 — Observability Baseline](m5-observability-baseline.md)
- [M6 — Elastic GPU Scaling](m6-elastic-gpu-scaling.md)

### Platform hardening (pre–Phase 2)

- [M7a — Platform Hardening (minimal, pre–Phase 2)](m7a-platform-hardening-minimal.md)
- [M7 — Staging Hardening (index)](m7-staging-hardening.md) — split into M7a + M7b

### Phase 2 feature track (committed; individual features adoption-gated)

The product-feature milestones M9–M14 are analyzed and sequenced in
[`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md);
the per-milestone instruction files below are scaffolds that mirror the
Phase 1 instruction format. Each individual feature still requires
explicit maintainer adoption per the Phase 2 Decision Checklist before
implementation begins.

- [M9 — Product Surface (API client / Web UI)](m9-product-surface.md)
- [M10 — Retrieval and Memory on externalized vector storage](m10-retrieval.md)
- [M11 — Agent and Tool Framework (sandboxed) — high-risk](m11-agent-tool-framework.md)
- [M12 — MCP Integration Layer](m12-mcp-integration.md)
- [M13 — Personal-Information Integrations (optional) — high-risk](m13-personal-info-integrations.md)
- [M14 — Media Services (optional)](m14-media-services.md)

### Closeout (post–Phase 2)

- [M7b — Full Staging Hardening (post–Phase 2)](m7b-full-staging-hardening.md)
- [M8 — Production Release](m8-production-release.md)

### Coverage map for upstream-Odysseus surfaces

The planning bundle references several upstream-Odysseus surfaces that did not
have an obvious primary owner when M9–M14 were first scaffolded. They are
listed here for traceability; all are now assigned, and the table is kept so
the assignment is easy to audit.

| Upstream surface | Owner | Notes |
| --- | --- | --- |
| `NotificationService` (`../03-implementation-plan.md` topology) | **M9** | M9 owns the user-facing in-app notification feed and the basic server-side notifications service. M10/M11/M14 are producers. |
| "Deep-research" component (Apache-2.0, `../12-phase-2-feature-adoption.md` licensing analysis) | **M11** | Labelled in-scope optional sub-feature of the agent and tool framework; the Apache-2.0 attribution checkpoint lives in the M11 instruction file. |

## File Structure

Each milestone file follows the same structure:

- **Status**: current progress against this milestone
- **Objective**: the single outcome the milestone delivers
- **Primary workstreams**: which workstreams own the work
- **Prerequisites**: what must be complete first
- **In scope**: work that belongs in this milestone
- **Non-goals**: work explicitly deferred
- **Build tasks**: concrete, repository-aware steps
- **Provenance and licensing checkpoints**: reuse-specific obligations
- **Security checkpoints**: security obligations specific to the milestone
- **Testing and validation**: how to prove the work
- **Exit criteria**: the bar for declaring the milestone done
- **Escalation triggers**: when to stop and request maintainer review
