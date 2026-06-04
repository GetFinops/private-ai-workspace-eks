# M3 — Stateful Dependency Externalization

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

Not started. Configuration variables for the database and object storage exist,
but there is no persistence layer, object-storage flow, or externalized session
store in code. See `docs/11-gap-analysis.md`.

## Objective

Remove local-state assumptions and back the control plane with managed,
externalized state so it can scale safely.

## Primary workstreams

- product-app
- platform-infra

## Prerequisites

- M2 complete.

## In scope

- a managed-PostgreSQL-backed persistence layer with schema and migrations
- object-storage-backed flows for uploads and artifacts
- an externalized session store implementation behind the M1 interface
- a chosen vector-storage strategy, integrated or explicitly deferred
- isolation or removal of local-only background processes

## Non-goals

- GPU inference (M4)
- observability depth (M5)
- autoscaling (M6)

## Build tasks

1. Implement a persistence layer in `app/` for the relational database. Provide
   schema definitions and a migration path. Do not default to an embedded local
   database, and do not assume a local data directory.
2. Replace any development-only in-memory session store with an externalized
   implementation behind the interface defined in M1.
3. Implement object-storage flows for uploads and artifacts. Do not assume
   bind-mounted local disk for production state.
4. Choose and integrate a vector-storage strategy, or document an explicit
   deferral with rationale.
5. Audit the control plane for remaining local-state coupling and remove,
   replace, or explicitly defer each instance.
6. Update `readiness_checks()` so persistence dependencies are reflected
   accurately.

## Provenance and licensing checkpoints

- New data-layer or storage dependencies require maintainer review for license
  compatibility.
- Any adapted persistence or storage patterns must keep upstream notices and a
  provenance record in `NOTICE`.

## Security checkpoints

- Keep database and storage credentials in managed secret storage.
- Do not log connection strings, credentials, or stored user content.
- Preserve user and tenant isolation in the data model and access paths.
- Keep the database private-only.

## Testing and validation

- Add tests for the persistence layer and migrations against a disposable
  database target.
- Add tests for object-storage flows against a local-compatible storage stand-in
  where practical.
- Add tests for the externalized session store.
- `python3 -m unittest discover -s tests` passes.
- Demonstrate that no production-critical feature depends on local
  bind-mounted disk.

## Exit criteria

- No production-critical feature depends on local bind-mounted disk.
- Local-state dependencies are removed, replaced, or explicitly deferred.
- Persistence, object storage, and session state run against externalized
  services.

## Escalation triggers

- the database schema and migration strategy
- the vector-storage decision
- any change affecting tenant or user isolation
- new data-layer dependencies
