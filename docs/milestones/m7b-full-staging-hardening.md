# M7b — Full Staging Hardening (post–Phase 2)

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

Not started. Blocked on completion of M7a and the Phase 2 milestones (M9–M14)
that the upcoming production release will include.

## Objective

Make the system a production candidate in a staging environment that behaves
like production and includes whatever subset of Phase 2 features (M9–M14)
will ship in the upcoming M8 release.

## Primary workstreams

- all workstreams

## Prerequisites

- M7a complete.
- All Phase 2 milestones that the upcoming release intends to include are
  complete. (Phase 2 is adoption-gated; a release may include any subset
  M9–M14, but each included milestone must be complete.)

## In scope

- staging soak under sustained production-like load against the full topology
  (platform + adopted Phase 2 features)
- rollback and intentionally-failed-deployment verification across the
  expanded surface
- backup/restore verification for every datastore in the build, including any
  Phase 2 datastores (for example a vector store added by M10)
- a security-posture review focused on Phase 2 additions:
  - agent/tool sandbox boundaries (if M11 is in the release)
  - MCP credential scoping and per-tenant isolation (if M12 is in the release)
  - personal-information integration credential handling (if M13 is in the
    release)
  - per-tenant index isolation in retrieval (if M10 is in the release)
- re-verification that branch protection, DCO sign-off, and the contribution
  flow continue to operate as documented

## Non-goals

- the production launch itself (M8)
- new feature development

## Build tasks

1. Run staging soak tests against the production-like topology and record
   behavior under sustained load, including any Phase 2 features in scope.
2. Verify rollbacks and intentionally failed deployments recover cleanly for
   each Phase 2 surface in the release (UI tier, agent runtime, MCP gateway,
   etc.).
3. Verify data backup and restore for the managed database, object storage,
   **and** any Phase 2 datastores.
4. Conduct a security-posture review focused on the Phase 2 attack-surface
   additions listed under "In scope".
5. Confirm branch protection and the contribution flow remain in force.
6. Update the M7a report (or create an M7b report) with findings, owners,
   and remediation status.

## Provenance and licensing checkpoints

- Run a dependency and license review across the full deployed stack
  including every Phase 2 feature in the release.
- Confirm `NOTICE` reflects every adapted artifact currently in the build,
  including each Phase 2 attribution.
- Confirm no AGPL-sensitive optional features have entered the default build
  through any Phase 2 milestone.

## Security checkpoints

- Validate that internal services remain non-public, including any Phase 2
  internal surfaces.
- Validate per-tenant and per-user isolation under realistic load across
  retrieval, agents, MCP, and any personal-information integrations.
- Validate that secrets remain managed and never logged across the expanded
  surface.
- Validate image and supply-chain scanning across all charts and images in
  the promotion path.

## Testing and validation

- Soak test results captured and reviewed.
- Successful rollback and failed-deployment recovery captured for each
  Phase 2 surface in scope.
- Successful backup and restore captured for every datastore in the build.
- Security-review findings recorded and triaged before M8 is opened.

## Exit criteria

- Staging behaves like the production architecture across platform and
  adopted Phase 2 features.
- Recovery procedures are documented for every datastore in the build.
- Major operational risks across platform and features are known and owned.
- M8 can be opened without unresolved security or licensing items.

## Escalation triggers

- any security-review finding in a sensitive area (auth, secrets, code
  execution sandbox, per-tenant isolation)
- backup, restore, or data-durability gaps for any datastore in scope
- governance or branch-protection gaps
- any finding that requires reopening a closed Phase 2 milestone
