# M12 — MCP Integration Layer

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.
>
> This is a Phase 2 milestone. The full Phase 2 governance — adoption gating,
> licensing rules, isolation requirements, and excluded-by-default
> components — is in [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md).
> Read that document before opening any M12 work.

## Status

Not started. Scaffolded as part of the Phase 2 kickoff. Requires explicit
maintainer adoption (see the Decision Checklist in the Phase 2 doc) before
implementation begins.

## Objective

Expose selected capabilities through the Model Context Protocol (MCP), with
per-tenant credential scoping and connection isolation.

## Primary workstreams

- product-app
- governance-security

## Prerequisites

- M11 agent and tool framework (MCP servers are a constrained form of
  tools and must run inside the M11 sandbox).

## In scope

- a connection-manager pattern for MCP servers reused as a *pattern* from
  upstream, not vendored wholesale
- per-tenant credential scoping for every MCP server
- connection isolation per tenant
- audit logging of every MCP server invocation
- an explicit per-tenant allow-list of MCP servers

## Non-goals

- arbitrary MCP server adoption — each server is an independent decision
- personal-information integrations exposed via MCP (M13, separate
  milestone with stronger constraints)

## Build tasks

1. Adopt the MCP connection-manager pattern. Implementation runs in the
   M11 sandbox; this milestone does not introduce a new execution model.
2. Define the per-tenant MCP allow-list. Default state: empty — every
   MCP server is explicit opt-in.
3. Implement per-tenant credential scoping. MCP servers that need
   credentials reach them via AWS Secrets Manager through the M3 hardened
   secret layer; never through environment variables shared across
   tenants.
4. Implement per-tenant connection isolation. One tenant's MCP session
   must never see another tenant's data, even on the same MCP server
   binary.
5. Wire audit logging for every MCP invocation: tenant, MCP server name,
   tool/method, sanitised argument shape, result class, latency. Respect
   the M5 content policy.
6. Implement an operator kill-switch (env var or feature flag) that
   disables MCP integrations cluster-wide.
7. Pick at least one MCP server to ship as the validation case for the
   pipeline. The server's dependencies must pass the M11 sandbox
   requirements and the Phase 2 licensing gate before it ships.

## Provenance and licensing checkpoints

- Treat each MCP server as an opt-in, independently reviewed integration.
- Exclude MCP servers whose dependencies fail the licensing or isolation
  gates.
- Record provenance and license per MCP server in `NOTICE`.

## Security checkpoints

- Each MCP server is sandboxed (M11 boundary) and authorized per tenant.
- Secrets via AWS Secrets Manager only; never plaintext environment
  variables shared across tenants.
- Per-tenant credential isolation enforced at the secret-fetch layer.
- Operator kill-switch is functional.
- Network egress from MCP servers follows the M11 allow-list policy.

## Testing and validation

- At least one MCP server runs with tenant-scoped credentials and
  isolation.
- Cross-tenant attempts to invoke another tenant's MCP session are
  rejected and audit-logged.
- The kill-switch disables MCP integrations within seconds.
- Backup/restore drill on credential storage succeeds without exposing
  cleartext values.

## Dev deployment validation

Per the standing Phase 2 rule in `docs/milestones/README.md`:

- Enable MCP in `deploy/values/dev/` with exactly one safe stub MCP
  server (no real external network reach in dev) once the chart values
  exist.
- Run a dev-deployment smoke test that invokes the stub MCP server,
  exercises the per-tenant credential scoping (a second dev tenant's
  token must be rejected), and exercises the kill-switch.
- The smoke test runs through the M11 sandbox and therefore exercises
  the M1-adapted-from-Odysseus inference path that the agent loop uses.
- Record the run in the milestone PR; failures block merge.

## Exit criteria

- At least one MCP server runs with tenant-scoped credentials and
  isolation, audited end-to-end.
- Per-tenant allow-list is enforced and observable.
- Operator kill-switch is functional.
- Dev-deployment smoke test passes against a freshly-deployed dev
  cluster.

## Escalation triggers

- adoption of any individual MCP server (per-server review)
- any MCP server whose default credentials are not per-tenant
- any MCP server whose dependencies cannot satisfy the M11 sandbox
- any cross-tenant isolation finding
