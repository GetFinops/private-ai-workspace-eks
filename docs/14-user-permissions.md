# User permissions & access model

How access is decided in the control plane: who a caller is, what they can see,
and what they are allowed to do. This is the authoritative reference for the
permission checks in `app/control_plane/`.

## 1. Principle: authenticated, tenant-isolated, deny-by-default

Every `/v1/*` route (except the unauthenticated ops endpoints below) requires a
**verified OIDC token**. Trust decisions are always made **server-side** from the
verified token — never from anything the client asserts. New capabilities are
**off until explicitly granted** (kill-switches default off; allow-lists empty ⇒
deny all).

**Unauthenticated ops endpoints** (no token; shape-only, no user data):
`GET /healthz`, `GET /readyz`, `GET /v1/inference/status` (`?probe=1` adds
GPU state), `GET /v1/models` (the selectable-model list + a deny-by-default
`capabilities` block). Everything else requires a bearer token.

## 2. Identity — who the caller is

The token verifier (`app/control_plane/token_verifier.py`) validates the token
and yields `TokenClaims`:

| Field | Source | Used for |
| --- | --- | --- |
| `subject` (`sub`) | OIDC `sub` | the **user** identity (`user_id`) |
| `email` | OIDC `email` | derives the **tenant** |
| `groups` | `cognito:groups` / `groups` / `roles` | **roles / permissions** |

**Tenant = the email domain** (`_extract_tenant_id`): `alice@acme.com` → tenant
`acme.com`. A private-org deployment is one tenant; multi-org is many. Falls back
to `default` when no email domain is present.

## 3. Isolation — what the caller can see

User-owned data (conversations, memory, notes/tasks, documents, model-install
requests) is keyed by **`(tenant_id, user_id)`** and isolation is enforced **at
the storage layer** (an in-memory bucket key or a SQL `WHERE tenant_id = %s AND
user_id = %s`) **and re-checked on every request**. A caller can never read,
update, or delete another user's — or another tenant's — data. Every
isolation-sensitive feature ships a cross-tenant regression test.

Content policy (M5) is orthogonal to permissions but always applies: prompts,
completions, tokens, secrets, and user content are never logged or put in
telemetry — audit records carry only shape (ids, enums, counts).

## 4. Roles & permissions

### 4.1 Standard user
Any caller with a valid token. Can use the core surface (chat, and any feature
their tenant is allow-listed for) and manage **their own** data.

### 4.2 Admin / operator — `AUTH_ADMIN_GROUP`
A caller in the admin group (`claims.has_group(AUTH_ADMIN_GROUP)`, default group
name `admin`). Grants elevated **read** across a tenant boundary where a feature
needs an operator view — e.g. listing **all** model-install requests to action
them. Admin is a token-group claim; it cannot be self-asserted.

### 4.3 Per-tenant feature capabilities (operator-granted)
Several inference-amplifying / IO-capable features are gated by an **operator
kill-switch + a per-tenant allow-list**, not a user role. The allow-list is JSON
`{"<tenant>": ["<capability>", ...]}` and is **deny-by-default** (a tenant not
listed is denied; an empty/invalid list denies all):

| Feature | Kill-switch | Allow-list |
| --- | --- | --- |
| Agent tools | `AGENT_TOOLS_ENABLED` | `AGENT_TOOLS_ALLOWLIST` |
| MCP | `MCP_ENABLED` | `MCP_ALLOWLIST` |
| Integrations | `INTEGRATIONS_ENABLED` | `INTEGRATIONS_ALLOWLIST` |
| Media | `MEDIA_ENABLED` | `MEDIA_ALLOWLIST` |

These grant a **tenant** access to a capability; every call is still tenant/user
isolated and rate-limited.

### 4.4 Model-install permission
The permission to **request a model install** (model management Phase 1a — see
[`m11-followups/04-model-management.md`](m11-followups/04-model-management.md)).
There is **no in-app approval step**: holding the permission *is* the
authorization. A request is recorded and applied out-of-band by an operator /
the deploy pipeline.

Gated by the kill-switch **`MODEL_INSTALL_ENABLED`** (default off) **and** a
per-user permission. `user_can_request_install(claims, config)` grants it when
**any** of these hold (deny-by-default otherwise):

1. **`MODEL_INSTALL_ALLOW_ALL_USERS`** is true — every authenticated user is
   permitted (a **dev convenience**; see §6).
2. the caller is in **`MODEL_INSTALL_GROUP`** (an OIDC group/role claim), or
3. the caller is an **admin** (`AUTH_ADMIN_GROUP`).

`GET /v1/models/install-requests` returns `can_request` (this exact evaluation)
so the UI enables the request action only for permitted users. Requests are
further constrained by a deny-by-default **HF repo/org allow-list**
(`MODEL_INSTALL_ALLOWLIST`), a **per-tenant rate limit**, and a **per-tenant
open-request cap** (`MODEL_INSTALL_MAX_OPEN_PER_TENANT`).

## 5. Decision order (for a permission-gated action)

```
1. Authenticated?            no  → 401
2. Feature kill-switch on?   no  → 403 (feature disabled)
3. Caller has permission?    no  → 403 (permission denied)
4. Tenant/user isolation + input validation + allow-list + rate/quota
5. Perform, isolated to (tenant_id, user_id); audit shape only
```

## 6. Environment posture

| | Dev | Production |
| --- | --- | --- |
| Model-install permission | **`MODEL_INSTALL_ALLOW_ALL_USERS=true`** — every authenticated user may request installs (fast iteration; no operator directory yet) | drop allow-all; grant via **`MODEL_INSTALL_GROUP`** (a Cognito group) or admins only |
| Feature allow-lists | seeded for `tenant-a.test`, with `tenant-b.test` intentionally excluded to prove cross-tenant denial | grant per real tenant as adopted |
| Admin group | the dev fixture token verifier marks the local `dev_auth_token` user as admin; **real Cognito tokens on the deployed cluster are not admin** unless in the group | set `AUTH_ADMIN_GROUP` + assign operators |

## 7. Granting a permission

- **A user → admin/operator:** add them to the `AUTH_ADMIN_GROUP` Cognito group.
- **A user → model-install:** add them to `MODEL_INSTALL_GROUP` (prod), or set
  `MODEL_INSTALL_ALLOW_ALL_USERS=true` (dev, everyone).
- **A tenant → a feature (tools/MCP/integrations/media):** add the tenant +
  capability to that feature's allow-list and enable its kill-switch.

Grants take effect on the next deploy (env comes from the Helm ConfigMap /
Secrets Manager). Kill-switches can disable a feature without a code change.

## 8. Escalation

Changes to auth/session semantics, admin-group wiring, isolation boundaries, or
anything that would let one tenant/user reach another's data are **escalation
triggers** (`AGENTS.md`): surface them for maintainer review, with a cross-tenant
test, before merge.
