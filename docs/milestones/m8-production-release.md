# M8 — Production Release

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

Not started.

## Objective

Launch the first public, production-capable version with operational coverage.

## Primary workstreams

- all workstreams

## Prerequisites

- M7 complete.

## In scope

- release notes
- finalized public documentation
- production deployment enablement
- early production monitoring and incident-pattern review

## Non-goals

- new feature scope beyond the released version
- speculative architecture not grounded in the documented direction

## Build tasks

1. Publish release notes for the first production-capable version.
2. Finalize public documentation so it matches the released behavior, scope, and
   non-goals. Keep marketing claims within the project's actual maturity.
3. Enable the gated production deployment path described in
   `docs/06-cloud-architecture.md`.
4. Confirm baseline SLOs from `docs/07-observability.md` are tracked in
   production.
5. Confirm maintainers have runbooks for incidents, scaling, and rollback.
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
