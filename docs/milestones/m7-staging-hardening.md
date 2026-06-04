# M7 — Staging Hardening

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

Not started.

## Objective

Make the system a production candidate in a staging environment that behaves
like production.

## Primary workstreams

- all workstreams

## Prerequisites

- M6 complete.

## In scope

- staging soak testing under production-like topology
- rollback and failed-deployment verification
- data backup and restore verification
- a security-posture and dependency review
- verification that branch protection and contribution flow work as intended

## Non-goals

- the production launch itself (M8)
- new feature development

## Build tasks

1. Run staging soak tests against the production-like topology and record
   behavior under sustained load.
2. Verify rollbacks and intentionally failed deployments recover cleanly.
3. Verify data backup and restore for the managed database and object storage.
4. Conduct a security-posture review: secret handling, network exposure,
   isolation, image and dependency scanning.
5. Confirm branch protection and the contribution flow operate as documented in
   `docs/04-governance-and-contribution.md`.
6. Document recovery procedures and known operational risks with owners.

## Provenance and licensing checkpoints

- Run a dependency and license review across the full deployed stack.
- Confirm `NOTICE` reflects all adapted code currently in the build.
- Confirm no copyleft-sensitive optional features have entered the default
  build.

## Security checkpoints

- Validate that internal services remain non-public.
- Validate tenant and user isolation under realistic load.
- Validate that secrets are managed and never logged.
- Validate image and supply-chain scanning in the promotion path.

## Testing and validation

- Soak test results captured.
- Successful rollback and failed-deployment recovery captured.
- Successful backup and restore captured.
- Security-review findings recorded and triaged.

## Exit criteria

- Staging behaves like the production architecture.
- Recovery procedures are documented.
- Major operational risks are known and owned.

## Escalation triggers

- any security-review finding in a sensitive area
- backup, restore, or data-durability gaps
- governance or branch-protection gaps
