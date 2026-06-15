# M13 Shared Harness — Build Plan

> **Status: gated. Awaiting the A–C maintainer sign-off in
> [`../m13-shared-harness-escalation.md`](../m13-shared-harness-escalation.md).
> No code written.** PR 1 needs Decision A; PR 2 needs Decision B; PRs 3–4 need
> A+B+C.
>
> Read [`../milestones/m13-personal-info-integrations.md`](../milestones/m13-personal-info-integrations.md)
> and [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md)
> first. The standing Phase 2 rules govern and are not repeated here. This file
> is the execution-ready breakdown of the shared harness (milestone build-tasks
> #2–#6); it does **not** adopt any real calendar/contacts/mail integration.

## Conventions for every PR

- Branch off `main` (protected); one PR per item below; maintainer merge.
- DCO `Signed-off-by` + `Co-Authored-By` for the assistant on every commit.
- CI gate is `python3 -m compileall app tests` + `python3 -m unittest discover -s tests`.
- App logic stays Python 3.11 **stdlib-only**. boto3 lives only inside the
  injected secret resolver (prod image already ships boto3 via
  `app/storage/s3.py`); the unit suite never imports it.

## Critical path

```
Decision A ──> PR1 (outbound URL guard)
Decision B ──> PR2 (secret resolver + IRSA)      # PR1, PR2 parallel once signed
              PR1 + PR2 ──> PR3 (harness wiring) ──> PR4 (fixture + dev smoke)
```

PR4 is the only one needing a live dev cluster, and it is GPU-independent (no
inference dependency), so it validates without the cold vLLM plane.

---

## PR 1 — Outbound URL guard (the M3 layer) · needs Decision A

**New:** `app/control_plane/outbound.py` (stdlib: `urllib`, `http.client`,
`ipaddress`, `socket`)

```
class OutboundReject(Exception): reason: str   # scheme|host_not_allowed|private_ip|metadata|dns
@dataclass(frozen=True) ValidatedTarget: host, ip, port, scheme, path

def validate_outbound_url(url, *, allowed_hosts, allow_http=False) -> ValidatedTarget
def guarded_open(target, *, method, headers, body, timeout) -> (status, headers, body)
```

- `https`-only scheme allow-list (`http` only via explicit `allow_http`, for the
  loopback fixture).
- Deny-by-default host allow-list.
- Resolve every A/AAAA record once; reject if any resolves into
  `is_private | is_loopback | is_link_local | is_reserved` or `169.254.169.254`.
- Connect to the **resolved IP literal** with `Host:` pinned — no re-resolution,
  defeating DNS rebinding.

**New tests:** `tests/test_outbound.py` truth-table — allowed `https` host ✓;
`http` without opt-in ✗; `10.0.0.1` / `127.0.0.1` / `192.168.1.1` /
`169.254.169.254` / `172.16.0.1` ✗; host-not-in-allowlist ✗; rebind (allowed
host → private A record) ✗; IPv6 loopback/ULA ✗.

**New:** `tests/architecture/test_outbound_no_bypass.py` — the integrations
package imports no HTTP primitive except `outbound`.

**Done when:** truth-table + arch test green; module documented as the M3
hardened URL-validation layer of record per Decision A.

## PR 2 — Per-tenant secret resolver + IRSA · needs Decision B

**New:** `app/control_plane/integration_secrets.py` — boto3 SecretsManager
wrapper behind a minimal interface, shaped like `app/storage/s3.py`. Injectable.

```
def make_secrets_manager_resolver(region) -> Callable[[str, str], dict|None]   # boto3, prod only
def build_secret_id(env, tenant, integration, user=None) -> str
# resolver(tenant_id, integration) -> dict[str,str] | None ; per-call + ARN+version TTL cache
```

- Secret id `private-ai-workspace/<env>/integrations/<tenant>/<integration>[/<user_sub>]`,
  built from the **verified token's** tenant — cross-tenant naming is
  structurally impossible.

**Terraform:** one statement added to `modules/irsa-app/main.tf` —
`secretsmanager:GetSecretValue` / `DescribeSecret` scoped to
`arn:…:secret:private-ai-workspace/<env>/integrations/*`. No new module, no
wildcard. New `var` for the prefix; mirror the vLLM HF-token policy block.

**New tests:** `tests/test_integration_secrets.py` (stdlib, fake resolver) — id
construction; tenant-A cannot derive tenant-B's id; TTL cache hit/miss; `None`
on missing. No boto3 in tests.

**Done when:** resolver unit-tested via injection; `terraform validate` / `plan`
clean on the IRSA module; ARN prefix matches the naming scheme.

## PR 3 — Harness wiring · needs A+B+C

**`config.py`** — add to `ControlPlaneConfig` + `from_env`:
`integrations_enabled` (`INTEGRATIONS_ENABLED`), `integrations_allowlist`
(JSON `{"<tenant>":["<integration>"]}`), `integrations_rate_per_minute`,
`integrations_max_concurrency`, `integrations_outbound_timeout_s`.

**New:** `app/control_plane/integrations.py`

- `parse_integration_allowlist(raw)` (clone of `parse_mcp_allowlist`); reuse
  `is_allowed`, `_audit`, `_arg_shape`, `RateLimiter` (dedicated instance —
  **not** the agent-tools budget), `_verify_and_extract`, `_extract_tenant_id`.
- `build_integrations_list_response(...)` and
  `build_integrations_invoke_response(...)` — pure handlers, same signature
  shape as `build_mcp_*`. Invoke flow: verify → kill-switch → **per-tenant DB
  enabled** → allow-list → rate-limit → resolve creds → build URL →
  `validate_outbound_url` → `guarded_open` → audit → emit
  `agent_task_completed`-style notification (reuse M11 store).
- Audit fields ride the existing whitelisted `audit` envelope (host, method,
  response class, latency, decision, reject-reason) → **no `logging_config.py`
  change**; add a test proving no URL path/query/params/creds leak.

**Per-tenant disable:** new table
`integration_tenant_state(tenant_id, integration, enabled, updated_at)` on the
existing RDS instance, default-deny; checked per request. Migration alongside
`session_postgres.py` conventions.

**`server.py`** — path constants `/v1/integrations/list`,
`/v1/integrations/invoke`; `do_POST` dispatch mirroring the MCP block; class-attr
init of enabled / allowlist / resolver / rate-limiter (prod injects
`make_secrets_manager_resolver`).

**New tests:** `tests/test_integrations.py` — kill-switch off → 503;
tenant-disabled → 403 (audited); not-allowlisted → 403; **cross-tenant**
(tenant-A token + tenant-B integration) → 403; rate-limit → 429; happy
path → 200; audit content-safety assertion.

**Done when:** full suite green; cross-tenant + content-safety tests present
(CLAUDE.md mandatory).

## PR 4 — Loopback fixture + dev smoke · milestone PR

**New:** `app/integration_fixtures/loopback_server.py` — fake in-cluster
calendar/mail server (stdlib `http.server`), no real provider, no real creds.
Helm: opt-in dev-only deployment (default `false`).

**`scripts/smoke-test.sh`** — add an `--integrations` block running the
milestone's 5 acceptance steps:

1. Store fixture creds in dev Secrets Manager via the IRSA path (no plaintext
   fallback).
2. One allowed call through `guarded_open` to the fixture.
3. One denied private-IP / `169.254.169.254` call; assert the audit log records
   the rejection.
4. Per-tenant kill-switch blocks a disabled tenant holding a valid token.
5. Rotate the fixture secret in Secrets Manager; assert propagation with no pod
   restart.

**Done when:** smoke passes against a freshly-deployed dev cluster; run recorded
in the PR (failures block merge — non-optional per the milestone); the
`NOTICE` record drafted in the escalation note is signed and committed here.

## Explicitly out of scope

- Adoption of any real calendar/contacts/mail provider (separate per-integration
  decision + credential review, milestone build-task #6).
- Any outbound path that bypasses the URL guard.
- Any per-cluster (non-per-tenant) credential model.
- Any change to the existing ESO → env-var path for the platform's own secrets.
