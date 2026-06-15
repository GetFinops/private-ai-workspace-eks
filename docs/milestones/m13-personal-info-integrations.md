# M13 — Personal-Information Integrations (Optional)

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.
>
> This is a Phase 2 milestone. The full Phase 2 governance — adoption gating,
> licensing rules, isolation requirements, and excluded-by-default
> components — is in [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md).
> Read that document before opening any M13 work.

> **High-risk, optional milestone.** Personal-information integrations
> (calendar, contacts, mail) handle credentials for third-party services on
> behalf of users. The upstream review in `docs/02-review-summary.md`
> specifically flagged weaker URL validation and plaintext credential
> handling in this area. Adoption is opt-in per integration and requires
> maintainer review for both the integration and its credential model.

## Status

Not started. Scaffolded as part of the Phase 2 kickoff. Requires explicit
maintainer adoption per integration (see the Decision Checklist in the
Phase 2 doc) and explicit credential-handling review before any individual
integration ships.

Shared-harness plan + escalation (decisions A–C, signed off in `NOTICE`):
[`../m13-shared-harness-escalation.md`](../m13-shared-harness-escalation.md) and
[`../m13-followups/00-build-plan.md`](../m13-followups/00-build-plan.md).

**Shared harness: delivered.** Build-tasks #2–#6 (the reusable machinery — URL
guard, per-tenant Secrets Manager resolver + scoped IRSA, deny-by-default
allow-list, operator + per-tenant kill-switches, shape-only audit) shipped as
original code and validated end-to-end in the local smoke against a synthetic
loopback fixture (`./scripts/smoke-test.sh --integrations`). The dev-cluster run
against real Secrets Manager/IRSA remains the maintainer step. **No real
provider is adopted** — build-task #1 (pick the first calendar/mail/contacts
integration) is a separate per-integration decision with its own credential
review.

## Objective

Optional calendar, contacts, and mail integrations behind the hardened
secret + URL-validation layer with per-tenant credential isolation.

## Primary workstreams

- product-app
- governance-security

## Prerequisites

- M3 hardened secret and URL-validation layer.
- M12 MCP integration layer if a given integration is exposed via MCP
  (recommended path; reuses the existing sandbox).

## In scope

- adapted *integration concepts* only — not vendored upstream integration
  code
- all credential handling routed through AWS Secrets Manager via IRSA
- all outbound URLs routed through the hardened validation layer
- per-tenant credential isolation
- audit logging of every outbound integration call

## Non-goals

- arbitrary OAuth provider adoption — each integration is an independent
  decision
- in-house mail relays, calendar servers, or contact stores

## Build tasks

1. Choose the *first* integration target (calendar, mail, or contacts —
   pick one). Each subsequent integration is its own decision and its
   own per-integration M13 sub-task; this scaffold covers the shared
   harness.
2. Implement the OAuth or API-key flow with credentials stored in AWS
   Secrets Manager, scoped per tenant (per-user inside a tenant where
   relevant). Never store credentials in environment variables or
   ConfigMaps.
3. Route all outbound HTTP through the M3 hardened URL-validation layer.
   No outbound call may bypass it. Block private IP ranges, link-local
   metadata service, and any non-allow-listed host.
4. Wire audit logging of every outbound call: tenant, user, integration
   name, request method and host (never the body), response class, latency.
   Respect the M5 content policy.
5. Implement an operator kill-switch (env var or feature flag) that
   disables the integration cluster-wide and a per-tenant disable in the
   database.
6. Conduct a credential-handling security review before the first
   integration ships. Findings recorded in `NOTICE`.

## Provenance and licensing checkpoints

- Adapt *concepts* only; do not vendor upstream integration code that
  carries the historical issues called out in `02-review-summary.md`.
- Review each integration's SDK for license and dependency footprint.
- Record provenance and credential model per integration in `NOTICE`.

## Security checkpoints

- All credential handling through AWS Secrets Manager with IRSA — never
  plaintext.
- All outbound URLs validated through the hardened M3 layer; cloud
  metadata service explicitly blocked.
- Per-tenant credential isolation enforced at the secret-fetch layer.
- Audit log captures attempt + result for every outbound call without
  capturing credentials, content, or PII beyond the integration target.
- Operator kill-switch is functional. Per-tenant disable is functional.

## Testing and validation

- Integration passes a security review with no plaintext credential
  storage and validated outbound URLs (findings recorded).
- An attempt to call a private IP or the cloud metadata service through
  the integration is rejected and audit-logged.
- A tenant whose integration is disabled cannot drive calls even with
  a valid token.
- Credential rotation in Secrets Manager propagates without restart.

## Dev deployment validation

Per the standing Phase 2 rule in `docs/milestones/README.md`:

- Dev integration target uses a synthetic loopback fixture (a fake
  calendar/contacts/mail server running inside the cluster, not the
  real third-party service). Real third-party endpoints are out of
  scope for dev deployment to keep credential exposure bounded.
- Run a dev-deployment smoke test that:
  - Stores fixture credentials in dev Secrets Manager via the IRSA path
    (no plaintext fallback).
  - Drives one successful call through the M3 hardened URL-validation
    layer.
  - Drives one denied call (private IP target) and confirms the audit
    log records the rejection.
  - Exercises the per-tenant kill-switch.
- The smoke test runs through the M11 sandbox (when M11 is deployed)
  and therefore exercises the M1-adapted-from-Odysseus surfaces that
  the agent loop uses.
- Record the run in the milestone PR; failures block merge. The
  underlying upstream weakness this milestone remediates (CardDAV-class
  URL/credential handling) makes this validation non-optional.

## Exit criteria

- At least one integration ships with credentials in Secrets Manager,
  URL validation through the M3 layer, and per-tenant scoping.
- The credential-handling security review is signed off and recorded.
- Operator and per-tenant kill-switches are functional.
- Dev-deployment smoke test passes against a freshly-deployed dev
  cluster using a synthetic loopback fixture (not the real third-party
  service).

## Escalation triggers

- adoption of any individual integration (per-integration review)
- any integration that cannot route through the M3 hardened URL layer
- any integration whose credential model is per-cluster instead of
  per-tenant
- any finding that suggests the upstream URL-validation gap has
  reappeared
