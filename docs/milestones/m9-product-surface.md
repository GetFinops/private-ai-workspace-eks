# M9 — Product Surface (API Client / Web UI)

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.
>
> This is a Phase 2 milestone. The full Phase 2 governance — adoption gating,
> licensing rules, isolation requirements, and excluded-by-default
> components — is in [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md).
> Read that document before opening any M9 work.

## Status

In progress on branch `feat/m9-product-surface`. Adoption decision recorded
in `NOTICE`. Code-completable items are done; live operator verification
items remain.

**Implemented:**

- Notifications service (`app/control_plane/notifications.py`) with in-memory
  (dev) and PostgreSQL (prod) stores; tenant + user isolation enforced at the
  store layer; content policy enforced at the handler.
- DB schema migration (`app/db/schema.sql` — `notifications` table, migration 0002).
- Control-plane API routes: `GET /v1/notifications`, `POST /v1/notifications`,
  `POST /v1/notifications/{id}/read`.
- Vanilla JS + HTML SPA under `app/ui/static/`: adapted from Odysseus design
  system (MIT, attributed in `NOTICE`). OAuth 2.0 Public Client + PKCE flow
  (RFC 8252; OAuth Browser-Based Apps BCP) — client-side token exchange against
  the OIDC provider's `/token` endpoint, **no control-plane auth-surface
  change**. Access token verified on every API call by the existing
  `OIDCTokenVerifier`.
- Chat path end-to-end through `POST /v1/chat/completions`.
- Notification bell + feed with three distinct controls: list, mark-read,
  dismiss (client-side hide + best-effort mark-read).
- nginx container (`app/ui/Dockerfile`) serving static assets + proxying to
  the control plane; strict CSP including a dynamically-derived `connect-src`
  / `form-action` allowlist for the configured OIDC origin (rendered by the
  entrypoint script from env vars).
- Helm chart: `deploy/helm/private-ai-ui/`; dev values: `deploy/values/dev/ui.yaml`.
  `helm lint` and `helm template` both clean.
- Unit tests: 37 in `tests/test_notifications.py` (isolation, content policy,
  auth, dismiss/mark-read semantics).
- Artifact tests: 14 in `tests/test_roadmap_artifacts.py` covering file
  existence, CSS variable names, no-innerHTML invariant, CSP allowlist
  rendering, notification routes wired, NOTICE records, and the
  security-review document.
- Web-security baseline review document: `docs/m9-security-review.md`
  (OWASP top-10 audit; CSRF posture; findings F-08 + F-09 accepted; no
  high or critical findings).

**Pending — operator/maintainer execution required:**

- Dev-cluster deployment smoke-test record: sign-in, chat round-trip,
  notification publish + mark-read + dismiss, and a cross-tenant retrieval
  probe. To be appended to the PR before merge per the standing Phase 2
  rule in `docs/milestones/README.md`. Produce it with
  `scripts/smoke-test.sh --base <control-plane> --token "$TOKEN_A" --token-b "$TOKEN_B"`
  (the `--token-b` argument runs the cross-tenant retrieval probe) and paste
  the output into the PR.

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
- **notifications delivery surface** — the user-facing in-app notification
  feed and a basic server-side notifications service that other Phase 2
  milestones (M10 indexing-complete, M11 long-running agent tasks, M14
  media-generation-complete) emit events into. This replaces the
  `NotificationService` node referenced in `../03-implementation-plan.md`'s
  target topology that was previously unowned across M0–M14.

## Non-goals

- retrieval/RAG features (M10)
- agent or tool execution (M11)
- MCP integrations (M12)
- any backend redesign — M9 consumes the existing public API contract
- external delivery channels beyond basic in-app feed plus optional
  per-tenant webhook (email/SMS/push are out of scope for the first cut
  and are escalation triggers if proposed later)

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
8. Implement a server-side notifications service in the control plane
   that other Phase 2 milestones can publish to: a tenant-scoped event
   queue and a per-user read/unread feed exposed through the public API.
   Storage uses the existing M3 RDS instance; no new managed dependency
   unless explicitly justified. Events carry no prompt/completion content
   per the M5 content policy — only event class, related-resource id,
   and timestamps.
9. Implement the in-app notification feed UI on top of the API from
   step 8. Provide list, mark-read, and dismiss controls.

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
- A test publisher can emit a synthetic notification event scoped to one
  tenant + user; only that user sees it in the feed; cross-tenant and
  cross-user retrieval attempts return zero results.

## Dev deployment validation

Per the standing Phase 2 rule in `docs/milestones/README.md` ("Dev
deployment validation for Phase 2"), M9 must be exercised end-to-end
in the dev deployment, not just in unit tests:

- Enable the UI chart and the notifications service in
  `deploy/values/dev/` once they exist.
- Run a dev-deployment smoke test that signs in a dev user, drives the
  chat path through the UI, and publishes + consumes one synthetic
  notification event. The smoke test exercises the M1-adapted-from-
  Odysseus control-plane surfaces (`routing.py`, `inference.py`,
  `token_verifier.py`) end-to-end through the new client.
  `scripts/smoke-test.sh` automates this round trip: in `--base` mode it
  drives the same notification + chat API the UI calls with a real OIDC
  bearer token, and a second `--token-b` identity exercises the
  cross-tenant retrieval probe.
- Record the run in the milestone PR; failures block merge.

## Exit criteria

- An authenticated user can drive the existing API (including the chat
  path) through the new surface.
- The UI is deployed as a separately-versioned image and chart.
- A web-security baseline review has been performed and findings triaged.
- The notifications service is operational and produces no cross-tenant
  or cross-user leakage in scripted tests.
- The dev-deployment smoke test passes against a freshly-deployed dev
  cluster.

## Escalation triggers

- adopting a frontend framework whose default dependencies include
  AGPL-sensitive components
- any client-side trust decision that would weaken control-plane auth
- any new public ingress path beyond what `06-cloud-architecture.md`
  already permits
- adding any external delivery channel (email, SMS, push) to the
  notifications service beyond the in-app feed and optional webhook
