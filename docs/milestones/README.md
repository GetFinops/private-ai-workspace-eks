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

### Currently unowned (tracked for future decision)

These upstream-Odysseus surfaces are referenced in the planning bundle but are
not owned by any current milestone. They are listed here so they are not lost;
adopting any of them requires opening a follow-up milestone (or folding the
scope into an existing milestone with an explicit decision record).

- **Notifications service** — referenced in
  [`../03-implementation-plan.md`](../03-implementation-plan.md) target topology
  but not assigned to M0–M8 or M9–M14. Likely belongs in the platform baseline
  rather than the Phase 2 feature track if adopted.
- **"Deep-research" component (Apache-2.0)** — referenced in the licensing
  analysis of [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md).
  If adopted, the most natural home is M11 (as a multi-step agent workflow);
  see the M11 file for the attribution checkpoint.

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
