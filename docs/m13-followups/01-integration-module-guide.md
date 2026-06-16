# Building an Integration Module (M13)

> Developer guide for adding a **personal-information integration** to the M13
> harness as a standardized, self-contained module. Read
> [`README.md`](README.md) (harness invariants), the milestone
> [`../milestones/m13-personal-info-integrations.md`](../milestones/m13-personal-info-integrations.md),
> and [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md)
> (adoption gating) first.
>
> **Every new integration is a maintainer adoption decision** (an `AGENTS.md`
> escalation trigger): a per-integration `NOTICE` record, a dependency/licensing
> review, and a credential-handling review are required **before** it ships. This
> guide is the *how*; it does not waive the *whether*.

## 1. What an integration module is

The harness ([`app/control_plane/integrations.py`](../../app/control_plane/integrations.py))
owns everything cross-cutting — auth, the deny-by-default per-tenant allow-list,
operator + per-tenant kill-switches, the dedicated rate limiter, per-tenant
credential resolution, the hardened outbound URL guard, shape-only audit, and
notifications. An **integration module** contributes exactly one thing: a small
class that, given an operation and resolved credentials, **builds the HTTP
request to make**. It never opens a socket; the harness validates and sends.

This keeps every integration uniform: the same isolation, SSRF defense, and
audit apply to all of them, and a new one is a single reviewable file.

Reference example: [`app/control_plane/integrations_google.py`](../../app/control_plane/integrations_google.py)
(Google Calendar, the first adopted provider). The dev-only synthetic example is
[`app/integration_fixtures/loopback_integration.py`](../../app/integration_fixtures/loopback_integration.py).

## 2. The contract

Implement this shape (a structural `Integration` protocol — no base class to
inherit):

```python
class MyIntegration:
    name: str                      # registry key + allow-list entry (e.g. "google_calendar")
    requires_secret: bool          # True ⇒ harness resolves per-tenant creds first
    allowed_hosts: frozenset       # exact hostnames the guard will permit
    permit_private_hosts: frozenset = frozenset()  # MUST stay empty for real integrations

    def build_request(self, operation: str, params: dict, creds: dict | None) -> OutboundRequest:
        ...
```

`OutboundRequest(method, url, headers={}, body=None, allow_http=False)` is the
return value. Rules:

- **Pure builder.** No I/O, no sockets, no `urllib`/`requests`/`http.client`/
  `socket` imports — an architecture test
  ([`tests/test_outbound_no_bypass.py`](../../tests/test_outbound_no_bypass.py))
  fails the build if an integration module imports a raw egress primitive. Use
  only `urllib.parse` (e.g. `quote`) for building URLs.
- **`allow_http=False`.** Real integrations speak HTTPS. `allow_http=True` is for
  the in-cluster loopback fixture only.
- **`permit_private_hosts = frozenset()`.** This is the escape hatch that lets
  the dev fixture reach a private cluster IP. A real integration **must leave it
  empty** so the full guard applies. (Even when set, the cloud-metadata block is
  never waived.)
- **Unsupported operation / missing required param ⇒ raise `UnknownOperation`.**
  The harness maps it to `404 unknown_operation`.
- **Validate/clamp `params`** (caps, allow-listed enums) and URL-encode every
  path component (`quote(value, safe="")`).

## 3. Credentials

If `requires_secret = True`, the harness resolves the per-tenant secret **before**
calling `build_request` and passes it in as `creds` (a `dict[str, str]`), or
returns `no_credentials` (→ `502`) if none is configured — your code never sees a
missing secret as a surprise.

- Secrets live in **AWS Secrets Manager**, read at request time via **IRSA**.
  Never env vars, never ConfigMaps.
- Secret id layout (built by the harness from the **verified token's** tenant):
  `<project>/<env>/integrations/<tenant>/<integration>[/<user>]`. A tenant can
  never name another tenant's secret.
- The `<env>` component uses the infra/Terraform token (e.g. `dev`), set via
  `INTEGRATIONS_SECRET_ENV` when it differs from the app `ENVIRONMENT`. The IRSA
  grant is prefix-scoped to `.../integrations/*` (see `modules/irsa-app`).
- Put the credential keys your builder needs into the secret JSON (e.g. an
  access-token integration reads `creds["ACCESS_TOKEN"]`).
- **OAuth2 refresh (standardized).** If your provider issues short-lived access
  tokens, expose a `token_refresh` (the `TokenRefresh` protocol in
  `integrations.py`) instead of storing an access token. The harness mints a
  fresh token before the call — itself a **guard-routed** outbound request
  against the refresher's own `allowed_hosts` (e.g. `oauth2.googleapis.com`) —
  caches it per `(tenant, integration)` for the lifetime the provider reports
  (refreshing before expiry), and injects it as `creds[token_key]`. Your
  `build_request` stays a pure builder and just reads the injected token. The
  secret then holds the refresh material (e.g. `CLIENT_ID` / `CLIENT_SECRET` /
  `REFRESH_TOKEN`), never a long-lived access token. See `GoogleOAuthRefresh` in
  [`integrations_google.py`](../../app/control_plane/integrations_google.py).

## 4. Register it

Add your integration to the real registry in
[`app/control_plane/server.py`](../../app/control_plane/server.py) (the block that
builds `integrations_executor`):

```python
from app.control_plane.integrations_myprovider import register as register_myprovider
_registry = register_google({})          # existing real integrations
register_myprovider(_registry)           # add yours
```

Registration only makes the integration *available*; access is still
**deny-by-default** — a tenant reaches it only when its `name` is in that tenant's
`INTEGRATIONS_ALLOWLIST` entry.

## 5. Configuration & operational controls (free, inherited)

Once registered + allow-listed, your integration automatically gets:

| Control | Mechanism |
| --- | --- |
| Operator kill-switch | `INTEGRATIONS_ENABLED` (cluster-wide) |
| Per-tenant allow-list | `INTEGRATIONS_ALLOWLIST` = `{"<tenant>": ["<name>"]}` (deny by default) |
| Per-tenant disable | `integration_tenant_state` DB row (operator kill, default-enabled) |
| Rate / concurrency | `INTEGRATIONS_RATE_PER_MINUTE`, `INTEGRATIONS_MAX_CONCURRENCY` (dedicated limiter) |
| Outbound timeout | `INTEGRATIONS_OUTBOUND_TIMEOUT_S` |
| Secret cache TTL | `INTEGRATIONS_SECRET_TTL_S` (rotation propagates within this, no restart) |
| Audit | shape-only (host, method, response class, latency, decision, reject-reason) |

You do not write any of this per integration.

## 6. Required tests

Mirror [`tests/test_google_calendar.py`](../../tests/test_google_calendar.py):

1. **Request building** — URL, method, headers, bearer, `allow_http=False`.
2. **URL-encoding** of path components (e.g. `team@example.com` → `team%40...`).
3. **Param validation/clamping** (caps, missing-required → `UnknownOperation`).
4. **Full-guard enforcement** — declares the public host, `permit_private_hosts`
   empty; an executor round-trip where the host resolves to a **private IP** is
   refused (`blocked:private_ip`) — defense against DNS rebinding.
5. **Executor round-trip** — success path with `getaddrinfo` stubbed to a public
   IP and `integrations.guarded_open` patched (no real network / no real
   provider call); assert the guarded sender got the right host.
6. **`no_credentials`** when the resolver returns `None`.
7. **If you use `token_refresh`:** the refresh happens before the call (assert
   two guarded calls — token endpoint then API), the minted token is injected,
   it is cached/reused within its lifetime and re-minted after, and a non-200
   token response yields `refresh_failed`. Generic refresh-cache behavior (clock
   expiry, per-tenant) is covered in `tests/test_integrations.py`.
8. Add the module path to `_M13_INTEGRATION_SOURCES` in
   `tests/test_outbound_no_bypass.py` so the no-egress guard covers it.

Cross-tenant denial and kill-switch behavior are covered once at the harness
level ([`tests/test_integrations.py`](../../tests/test_integrations.py)) and apply
to every integration — no need to re-test per module.

## 7. NOTICE, licensing, escalation

Before merge:

- **Per-integration `NOTICE` record** (mirror "M13 Google Calendar integration"):
  the adoption decision, licensing (vendored deps? prefer raw HTTPS, no SDK),
  credential model, security posture, and what live validation requires.
- **Dependency/licensing review** of any SDK you pull in. Prefer none — plain
  HTTPS against a public API has no footprint to review. No AGPL-sensitive deps.
- **Credential-handling review** (the milestone's build-task #6) — must confirm
  no plaintext credentials and validated outbound URLs.
- Do **not** vendor upstream CardDAV/CalDAV-class URL+credential code (the
  upstream-flagged area); adapt concepts only.

## 8. Dev validation

Real third-party endpoints are out of scope for the dev cluster. Validate the
*harness path* with the synthetic **loopback fixture** (which is itself an
integration module, with `permit_private_hosts` set — the only place that's
allowed). The live dev recipe (real Secrets Manager/IRSA, rotation, kill-switch)
is in [`../../scripts/smoke-test.sh`](../../scripts/smoke-test.sh) `--integrations`
and was exercised end-to-end for the harness. Live validation against your real
provider needs per-tenant credentials provisioned in Secrets Manager — a
maintainer step.

## 9. Checklist

- [ ] Maintainer adoption decision recorded; escalation triggers cleared.
- [ ] One module file: pure builder, no egress imports, `permit_private_hosts` empty.
- [ ] HTTPS only; exact `allowed_hosts`; URL-encoded, validated params.
- [ ] `requires_secret` set; secret keys documented; creds read from `creds`.
- [ ] Registered in `server.py`; **not** added to any default allow-list.
- [ ] Tests per §6 incl. the rebinding/full-guard case; added to the no-bypass scan.
- [ ] `NOTICE` per-integration record; licensing + credential reviews done.
- [ ] Cross-tenant + audit behavior confirmed inherited from the harness.
