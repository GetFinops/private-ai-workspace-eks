# M9 — Web Security Baseline Review

This document records the M9 security checkpoints (per
`docs/milestones/m9-product-surface.md`) audited against the actual
implementation on the `feat/m9-product-surface` branch.

It is the written deliverable for M9 exit criterion 3:

> A web-security baseline review has been performed and findings triaged.

## Scope of review

| Surface | Files reviewed |
|---|---|
| Browser SPA | `app/ui/static/{index,login}.html`, `app/ui/static/app.js`, `app/ui/static/style.css`, `app/ui/static/sw.js` |
| Container web tier | `app/ui/nginx.conf`, `app/ui/docker-entrypoint.sh`, `app/ui/Dockerfile` |
| API endpoints introduced by M9 | `app/control_plane/notifications.py`, `app/control_plane/server.py` (`/v1/notifications*` routes) |
| Helm chart | `deploy/helm/private-ai-ui/`, `deploy/values/dev/ui.yaml` |

The pre-existing M1–M6 control-plane surface (`routing.py`, `inference.py`,
`token_verifier.py`, etc.) is **not** re-reviewed here — M7a covered that
surface. This review covers only what M9 adds.

## OWASP top-10 audit

### A01 — Broken Access Control

| Concern | Implementation | Status |
|---|---|---|
| Anonymous access to APIs | `_verify_and_extract()` returns 401 on missing or invalid Bearer token before any data access | Pass |
| Cross-tenant data leak via list endpoint | `NotificationStore.list_for_user()` keyed on `(tenant_id, user_id)` tuple; tenant derived from token email domain | Pass |
| Cross-user data leak via mark-read | `PostgresNotificationStore.mark_read()` `WHERE id = %s AND tenant_id = %s AND user_id = %s`; cross-owner attempts return None → 404 | Pass |
| Forced browsing / IDOR enumeration | 404 returned for unknown **or** non-owned IDs — does not distinguish; prevents enumeration | Pass |
| Cross-user publish through `POST /v1/notifications` | Publisher's `tenant_id`/`user_id` derived from their own verified token claims, never from request body | Pass |

**Tests proving the invariants**:
- `tests/test_notifications.py::TestInMemoryStore::test_isolation_cross_user`
- `tests/test_notifications.py::TestInMemoryStore::test_isolation_cross_tenant`
- `tests/test_notifications.py::TestInMemoryStore::test_mark_read_wrong_owner`
- `tests/test_notifications.py::TestInMemoryStore::test_mark_read_wrong_tenant`
- `tests/test_notifications.py::TestMarkReadResponse::test_not_found_for_wrong_user`
- `tests/test_notifications.py::TestPublishResponse::test_cross_user_publish_is_scoped`

### A02 — Cryptographic Failures

| Concern | Implementation | Status |
|---|---|---|
| Token transport in clear | All public ingress terminates TLS at ALB (per `06-cloud-architecture.md`); HSTS deferred to ingress annotation rather than nginx to avoid breaking non-TLS dev | Pass with note |
| Token storage in browser | Access token stored in `sessionStorage` (cleared on tab close), **not** `localStorage` (persisted) | Pass |
| PKCE code-verifier exposure | Generated with `crypto.getRandomValues()`; stored in `sessionStorage`; deleted from `sessionStorage` immediately after successful token exchange (`app.js` `handleCallback()`) | Pass |
| Provider TLS verification | Browser native fetch enforces TLS verification against the OS trust store | Pass |

### A03 — Injection

| Concern | Implementation | Status |
|---|---|---|
| XSS via message content | All user-facing text set via `textContent` (helper `setText`); never `innerHTML` | Pass |
| XSS via notification fields | `event_class` is validated server-side against a fixed allowlist; `resource_id` is rendered via `textContent` | Pass |
| XSS via avatar / labels | Hardcoded string constants ("U", "AI"); no user input | Pass |
| Inline `<script>` injection through CSP bypass | CSP `script-src 'self'` (no `'unsafe-inline'`); all JS lives in `/static/app.js` external file | Pass |
| SQL injection via notification IDs | psycopg parameterised queries (`%s` placeholders); no string concatenation | Pass |
| Path traversal via API path parsing | Notification ID is URL-encoded by the client; server extracts only the segment between `/v1/notifications/` and `/read`; no filesystem touch | Pass |

**Static audit**: `tests/test_roadmap_artifacts.py::test_m9_app_js_has_no_innerhtml_writes` enforces no `innerHTML` writes remain in `app.js`.

### A04 — Insecure Design

| Concern | Implementation | Status |
|---|---|---|
| OIDC flow choice | OAuth 2.0 Public Client + PKCE (RFC 8252; OAuth 2.0 for Browser-Based Apps BCP). No client_secret on the SPA. | Pass |
| Token exchange location | Client-side direct call to OIDC provider's `/token` endpoint. **No control-plane auth surface touched** by M9. The existing `OIDCTokenVerifier` validates the access token on every subsequent API call. | Pass |
| State + nonce replay defence | Cryptographically random `state` (CSRF defence on callback) and `nonce` (ID-token replay defence) generated per-login; both verified before token is accepted | Pass |
| Privilege escalation through event class | `event_class` validated against `ALLOWED_EVENT_CLASSES` allowlist before persistence; unknown values rejected with 422 | Pass |

### A05 — Security Misconfiguration

| Concern | Implementation | Status |
|---|---|---|
| Server header leakage | `server_tokens off` in `nginx.conf` | Pass |
| Permissive CORS | None configured. The control plane is reached via same-origin proxy through nginx; OIDC token endpoint is reached cross-origin and relies on the provider's CORS policy (operator must enable for the SPA origin — documented in `values.yaml`) | Pass |
| Container runs as root | `USER nginx` in Dockerfile; pod `securityContext.runAsNonRoot: true`, `runAsUser: 101`; container `capabilities.drop: ["ALL"]`, `allowPrivilegeEscalation: false` | Pass |
| Liveness/readiness probes | `/healthz` configured on both | Pass |
| HSTS header | Deferred to ALB ingress annotation (documented in `nginx.conf` comment); rationale: avoids breaking non-TLS dev clusters | Accept |
| Permissions-Policy | `geolocation=(), camera=(), microphone=()` | Pass |
| X-Frame-Options / frame-ancestors | `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'` (clickjacking defence) | Pass |
| X-Content-Type-Options | `nosniff` always | Pass |
| Referrer-Policy | `strict-origin-when-cross-origin` | Pass |

### A06 — Vulnerable and Outdated Components

| Component | Source | Version pinning |
|---|---|---|
| nginx | `nginx:1.27-alpine` (Dockerfile FROM line) | Pinned to 1.27 line; rebuild promotes patch updates | Pass |
| Fira Code font | Fetched from upstream GitHub at build time | Not pinned to a specific tag — minor concern; downstream image SHA captures the resolved state | Accept; consider pinning |
| Frontend libraries | **None** — vanilla JS, no npm dependencies, no transitive supply chain | Pass |
| Service worker | Local-only cache (no third-party SW frameworks) | Pass |

**Finding F-09 (Accept)**: Fira Code WOFF2 is fetched from `github.com/tonsky/FiraCode/raw/master/...` — `master` is a moving reference. The SHA of the final image captures whatever was current at build time, but a malicious upstream replacement between two image builds would not be detected by us. Mitigation: pin to a specific release tag in a follow-up (low-priority; the font has no execution surface).

### A07 — Identification and Authentication Failures

| Concern | Implementation | Status |
|---|---|---|
| Weak password storage | N/A — no local credentials; identity is delegated to the operator's OIDC provider | Pass |
| Session fixation | No server-side sessions for the SPA; access token is bound to the verified `sub` claim | Pass |
| Brute force / credential stuffing | Handled by the OIDC provider; not in M9 scope | Out of scope |
| Token-rotation strategy | Access-token lifetime is determined by the OIDC provider; UI receives a 401 on next API call and redirects to login | Pass |
| Logout | `signoutBtn` clears `sessionStorage` then redirects to provider's `/logout` endpoint (configurable; defaults to Cognito-style) | Pass |

### A08 — Software and Data Integrity Failures

| Concern | Implementation | Status |
|---|---|---|
| Subresource integrity for JS / CSS | N/A — all assets are same-origin from our own image; no third-party CDN | Pass |
| Service worker cache poisoning | SW caches only `/static/style.css`, `/static/app.js`, `/static/manifest.json`; API and config paths are passed straight through | Pass |
| Container image provenance | Built and pushed by the maintainer-controlled CI pipeline into the project's ECR; ImagePullPolicy is `Always` for dev to force pulling fresh tags | Pass |

### A09 — Security Logging and Monitoring Failures

| Concern | Implementation | Status |
|---|---|---|
| Auth failures logged | `AUTH_FAILURES_TOTAL` Prometheus counter incremented with reason label (`missing_token`, `invalid_token`, `auth_not_configured`) in `notifications.py` and `server.py` — uses the same M5 metric established for the chat path | Pass |
| Request logging | nginx access log enabled; control plane emits structured JSON logs with request-id and correlation-id | Pass |
| Content policy in logs | The control plane explicitly forbids prompt/completion in logs (M5 policy). For notifications: `event_class` and `resource_id` may appear in error logs, but **never** content. | Pass |

### A10 — Server-Side Request Forgery

| Concern | Implementation | Status |
|---|---|---|
| URL fetching from user input | The control plane does **not** fetch any user-controlled URL in M9. The only outbound URL it constructs is the inference base URL (validated by `routing.py::normalize_base_url`, scheme allowlist). The SPA fetches the OIDC token endpoint, which is configuration-supplied (not user-supplied). | Pass |

## CSRF posture

CSRF is a non-issue for the M9 API surface:

- All state-changing endpoints (`POST /v1/notifications`, `POST /v1/notifications/{id}/read`) require an `Authorization: Bearer` header.
- The token is held in `sessionStorage`, **not** a cookie. The browser does not automatically attach `sessionStorage` values to cross-origin requests; only same-origin JS in our SPA can read it.
- An attacker landing the user on `evil.com` cannot read the token (Same-Origin Policy on `sessionStorage`) and cannot forge a CORS-allowed request to our API with the token attached.
- Therefore CSRF tokens are not required for the M9 routes. This decision is recorded here so a future reviewer does not flag it as a missing control.

## Findings summary

| ID | Severity | Title | Status |
|---|---|---|---|
| F-08 | Info | HSTS is set by ALB ingress annotation, not by the UI image's nginx | Accepted (documented in `nginx.conf`) |
| F-09 | Low | Fira Code WOFF2 fetched from a moving git reference at build time | Accepted; pin to a tagged release in a follow-up |

No high or critical findings.

## Validation matrix

| Exit criterion | Evidence |
|---|---|
| CSP present | `app/ui/nginx.conf` server + per-location `add_header Content-Security-Policy` |
| CSRF on state-changing endpoints | Bearer-token model + same-origin sessionStorage (see "CSRF posture" above) |
| No obvious XSS sinks | `tests/test_roadmap_artifacts.py::test_m9_app_js_has_no_innerhtml_writes` enforces no `innerHTML` writes in JS; `setText` helper used for every user-derived field |
| Per-tenant data fetching enforced server-side | `tests/test_notifications.py` (6 isolation tests listed in A01) |

## Open items requiring operator/maintainer execution

The following are exit-criterion items that **cannot** be completed from inside this PR and must be recorded by an operator on a dev cluster:

1. **Dev-deployment smoke test record**: deploy the chart with `deploy/values/dev/ui.yaml`, sign in with a dev OIDC user, drive one chat message and one notification round-trip end-to-end. Record the run (logs, exit code) in the PR before merging.
2. **Live cross-tenant retrieval probe**: with two dev users in different tenants (different email domains), confirm that `GET /v1/notifications` from one tenant returns zero events published by the other.

These are tracked as the remaining checkboxes on the M9 PR.
