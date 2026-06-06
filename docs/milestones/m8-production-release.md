# M8 — Production Release

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

Not started. Blocked on M7b (full staging hardening), which is itself blocked
on the Phase 2 milestones that the release intends to include. See
`docs/12-phase-2-feature-adoption.md` for the sequencing rationale.

## Objective

Launch the first public, production-capable version with operational coverage.
The release scope is the platform baseline plus any Phase 2 features (M9–M14)
that have been adopted and have passed M7b.

## Primary workstreams

- all workstreams

## Prerequisites

- M7b complete (which itself requires M7a and all in-scope Phase 2
  milestones).

## In scope

- release notes that accurately reflect platform + Phase 2 scope
- finalized public documentation
- production deployment enablement
- early production monitoring and incident-pattern review

## Non-goals

- new feature scope beyond the released version
- speculative architecture not grounded in the documented direction

## Build tasks

1. Publish release notes for the first production-capable version, including
   the platform baseline and every adopted Phase 2 feature.
2. Finalize public documentation so it matches the released behavior, scope,
   and non-goals. Keep marketing claims within the project's actual maturity.
3. Enable the gated production deployment path described in
   `docs/06-cloud-architecture.md`.
4. Confirm baseline SLOs from `docs/07-observability.md` are tracked in
   production. Confirm any additional Phase 2 SLOs are tracked too.
5. Confirm maintainers have runbooks for incidents, scaling, and rollback
   covering both platform and adopted Phase 2 features.
6. Monitor early production usage and record incident patterns for follow-up.

## Provenance and licensing checkpoints

- Confirm `NOTICE` and attribution are complete and accurate for the released
  artifact.
- Confirm the released build excludes copyleft-sensitive optional features.

## Security checkpoints

- Confirm production exposure matches the intended network boundaries.
- Confirm secret handling, isolation, and audit logging are in force.
- Confirm the security reporting path in `SECURITY.md` is current.

## Testing and validation

- A successful production deployment.
- SLO tracking active in production.
- Runbooks validated against at least a tabletop incident exercise.

## Exit criteria

- The production deployment succeeds.
- Baseline SLOs are tracked.
- Maintainers have runbooks for incidents, scaling, and rollback.

## Escalation triggers

- production networking exposure at launch
- any unresolved security or licensing item
- public claims that exceed current maturity
