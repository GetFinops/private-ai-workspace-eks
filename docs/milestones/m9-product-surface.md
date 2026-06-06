# M9 — Product Surface (API Client / Web UI)

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.
>
> This is a Phase 2 milestone. The full Phase 2 governance — adoption gating,
> licensing rules, isolation requirements, and excluded-by-default
> components — is in [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md).
> Read that document before opening any M9 work.

## Status

Not started. Scaffolded as part of the Phase 2 kickoff. Requires explicit
maintainer adoption (see the Decision Checklist in the Phase 2 doc) before
implementation begins.

## Objective

A first-party user-facing surface over the control-plane API.

## Primary workstreams

- product-app

## Prerequisites

- M7a complete (platform baseline + minimum pre–Phase-2 hardening pass).

## In scope

- a new client that consumes the public control-plane API
- authenticated, per-tenant views
- standard web hardening (CSP, CSRF, output encoding)
- the chat path driven through the new surface end-to-end

## Non-goals

- retrieval/RAG features (M10)
- agent or tool execution (M11)
- MCP integrations (M12)
- any backend redesign — M9 consumes the existing public API contract

## Build tasks

1. Choose a frontend stack consistent with the project's lightweight,
   maintainer-controlled posture. Avoid frameworks that pull in
   AGPL-sensitive dependencies by default.
2. Define the API surface the UI consumes from the control plane. Do not
   bypass the public contract; new server endpoints belong in the
   control-plane chart, not the UI tier.
3. Implement OIDC bearer-token handling on the UI side that delegates
   verification to the control plane — no client-side trust of tokens.
4. Implement the chat path end-to-end so an authenticated user can drive
   `POST /v1/chat/completions` from the UI and observe streamed responses
   (or polled responses, depending on the contract chosen).
5. Apply standard web hardening: Content Security Policy, CSRF protection
   on any state-changing endpoint, output encoding, no `dangerouslySetInnerHTML`
   on untrusted content.
6. Package the UI as its own image and Helm chart so it can be deployed,
   versioned, and rolled back independently from the control plane.
7. Wire ingress so the UI is reachable through the existing public ALB
   under a path or subdomain consistent with `06-cloud-architecture.md`.

## Provenance and licensing checkpoints

- Review any adopted frontend assets and fonts for license compatibility.
  Reject vendoring of AGPL-sensitive components into the default build.
- Reuse upstream UX and product *concepts* only; do not fork or vendor a
  large upstream UI codebase.
- Record provenance in `NOTICE` for every adapted asset or pattern.

## Security checkpoints

- Authenticated views only — no anonymous surface beyond a sign-in entry
  point.
- Per-tenant data fetching enforced on the control plane, not the UI.
- No privileged client-side trust: tokens, role checks, and tenant scoping
  must be verified server-side on every request.
- Standard web hardening (CSP, CSRF, output encoding) is mandatory, not
  optional.
- The UI must not log or persist prompt or completion content beyond what
  the user explicitly downloads.

## Testing and validation

- An authenticated user can sign in and drive `POST /v1/chat/completions`
  through the UI end-to-end.
- Cross-tenant access attempts (manual or scripted) are rejected by the
  control plane and visible in the M5 audit logs.
- The UI passes a baseline web-security review (CSP present, CSRF
  protection on state-changing endpoints, no obvious XSS sinks).

## Exit criteria

- An authenticated user can drive the existing API (including the chat
  path) through the new surface.
- The UI is deployed as a separately-versioned image and chart.
- A web-security baseline review has been performed and findings triaged.

## Escalation triggers

- adopting a frontend framework whose default dependencies include
  AGPL-sensitive components
- any client-side trust decision that would weaken control-plane auth
- any new public ingress path beyond what `06-cloud-architecture.md`
  already permits
