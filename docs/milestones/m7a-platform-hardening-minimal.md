# M7a — Platform Hardening (minimal, pre–Phase 2)

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

In progress (Phase 2 kickoff). The original `M7 — Staging Hardening` was split
into M7a and M7b; see `m7-staging-hardening.md` for the index and the
maintainer rationale recorded in `NOTICE`.

## Objective

Make the M0–M6 platform baseline operationally safe **before** any Phase 2
product features (M9–M14) land on top of it. The full staging soak under the
expanded post-Phase-2 topology is deferred to M7b.

## Primary workstreams

- governance-security
- platform-infra

## Prerequisites

- M6 complete.

## In scope

- security-posture review of the surface shipped through M6 (auth, secret
  handling, network exposure, IAM scoping for the M6 scaling controllers —
  cluster-autoscaler, Karpenter controller and node roles, prometheus-adapter)
- rollback and intentionally-failed-deployment verification on dev
- managed-database backup and restore verification on dev
- object-storage versioning/lifecycle verification on dev
- confirmation that branch protection and the contribution flow operate as
  documented in `docs/04-governance-and-contribution.md`
- a short M7a report committed under `docs/` recording findings, owners, and
  the date of each drill

## Non-goals

- staging soak under production-like sustained load (deferred to M7b)
- Phase 2 feature attack-surface review — there are no Phase 2 features yet
- the production launch itself (M8, post-Phase-2)
- net-new product feature work (Phase 2 milestones own that)

## Build tasks

1. **Security posture pass**: walk the surface added through M6 and confirm
   that auth, secret handling, IAM trust policies, and network exposure match
   the architecture documents. Pay specific attention to the new M6 IRSA
   roles (cluster-autoscaler, Karpenter controller, Karpenter node role) and
   to the Karpenter NodePool's GPU taint + capacity constraints. Record
   findings in `docs/m7a-report.md`.
2. **Rollback drill**: deploy a deliberately broken control-plane image to
   dev, confirm the readiness gates catch it, and roll back via the
   `deploy.yml` workflow. Capture timings and unexpected behavior.
3. **Backup and restore drill**: trigger an out-of-band RDS snapshot, restore
   to a new instance, and verify the control plane can connect with the
   restored credentials. For the S3 artifact bucket, confirm versioning and
   any lifecycle rules.
4. **Governance check**: confirm branch protection settings against
   `docs/04-governance-and-contribution.md`. Confirm DCO sign-off is enforced
   on the default branch.
5. **Document operational owners**: in the M7a report, record who owns each
   of the four areas above and what evidence was captured.

## Provenance and licensing checkpoints

- Run a license sweep across the runtime dependencies introduced through M6
  (M5 `prometheus-client`, `opentelemetry-*`; M6 cluster-autoscaler,
  Karpenter, prometheus-adapter Helm charts).
- Confirm `NOTICE` reflects every adapted artifact currently in the build.
- Confirm no copyleft-sensitive optional features have entered the default
  build.

## Security checkpoints

- Validate that internal services (vLLM, Karpenter controller, scaling
  metrics path) remain non-public.
- Validate that the M6 IRSA trust policies are scoped to the exact namespace
  and service-account names they claim.
- Validate that secrets remain managed (no plaintext in ConfigMaps, no
  secrets logged through the M5 structured-logging path).

## Testing and validation

- The M7a report exists in `docs/` and captures: scope, findings, owners,
  drill dates, and any deferred items.
- Backup/restore drill produces a working restored database.
- Rollback drill returns the dev environment to a healthy state.
- Branch-protection settings match the contribution guide.

## Exit criteria

- Security posture of the M0–M6 surface is reviewed and recorded.
- Backup, restore, and rollback drills have been performed at least once on
  dev and are documented.
- Known operational risks at the platform layer are recorded with owners.
- Phase 2 can begin without un-validated platform debt.

## Escalation triggers

- any security-review finding in a sensitive area (auth, secrets, IAM)
- backup, restore, or data-durability gaps that block Phase 2
- governance or branch-protection gaps
- any finding that suggests the M6 surface should be revised before Phase 2
  begins
