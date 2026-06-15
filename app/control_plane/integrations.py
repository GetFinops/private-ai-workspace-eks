"""M13 personal-information integration harness — wiring, not a provider.

PR 3 of the M13 shared harness. This is the request path every calendar/contacts/
mail integration plugs into; it ships with **no real provider** (the loopback
test fixture arrives in PR 4, real providers are separate per-integration
adoption decisions). See docs/m13-followups/00-build-plan.md and the Decision
A–C sign-off in docs/m13-shared-harness-escalation.md.

Security model (mirrors M11/M12, reuses their primitives):
  - operator kill-switch (INTEGRATIONS_ENABLED);
  - per-tenant operator disable (TenantIntegrationState) — an allow-listed tenant
    can still be switched off cluster-side without touching its allow-list;
  - deny-by-default per-tenant allow-list (INTEGRATIONS_ALLOWLIST);
  - per-tenant rate/concurrency limit (a DEDICATED limiter, not the agent-tools
    budget);
  - per-tenant credentials resolved at request time and passed only to the one
    request being built (never a shared/ambient env var);
  - **every** outbound call routes through the hardened URL guard
    (app.control_plane.outbound) — no integration may open a socket itself;
  - audit is shape-only (tenant, integration, operation, arg shape, result class,
    latency); a block reason rides inside result_class so no new structured
    field is introduced.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Callable, Protocol, runtime_checkable

from app.control_plane.agent_tools import RateLimiter, _audit, is_allowed
from app.control_plane.notifications import (
    NotificationStore,
    _extract_tenant_id,
    _verify_and_extract,
)
from app.control_plane.outbound import (
    OutboundReject,
    guarded_open,
    validate_outbound_url,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0
_MAX_PARAMS_BYTES = 100_000
_MAX_RESULT_BYTES = 256 * 1024


# ── Integration contract ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class OutboundRequest:
    """A request an integration wants to make, before the guard validates it."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    # http is permitted only for the in-cluster loopback fixture; real
    # integrations leave this False so the guard enforces https.
    allow_http: bool = False


class Integration(Protocol):
    """A personal-information integration the harness can drive.

    Implementations are pure request *builders*: given an operation, params, and
    (optionally) resolved credentials, they return the OutboundRequest to make.
    They never open sockets — the harness validates and sends through the guard.
    """

    name: str
    allowed_hosts: frozenset  # hosts the guard will permit for this integration
    requires_secret: bool

    def build_request(
        self, operation: str, params: dict, creds: dict | None
    ) -> OutboundRequest:
        ...


# Default registry is EMPTY — deny by default. The loopback fixture (PR 4) and
# any real integration register here behind its own adoption decision.
INTEGRATIONS: dict[str, Integration] = {}


class UnknownOperation(Exception):
    """Raised by an integration's build_request for an unsupported operation."""


# ── Per-tenant operator disable ───────────────────────────────────────────────


@runtime_checkable
class TenantIntegrationState(Protocol):
    """Operator per-tenant switch. Default is enabled; a record disables.

    This is an operator kill, distinct from the allow-list: it lets an operator
    switch a tenant off cluster-side (incident response, abuse) without editing
    the deny-by-default allow-list. Production backs this with a small RDS table
    (integration_tenant_state); the in-memory implementation below serves dev and
    the unit suite.
    """

    def is_enabled(self, tenant_id: str, integration: str) -> bool:
        ...


class InMemoryTenantIntegrationState:
    """Default-enabled tenant state; explicit disables are held in a set."""

    def __init__(self, disabled: "set[tuple[str, str]] | None" = None) -> None:
        self._disabled = set(disabled or set())

    def disable(self, tenant_id: str, integration: str) -> None:
        self._disabled.add((tenant_id, integration))

    def enable(self, tenant_id: str, integration: str) -> None:
        self._disabled.discard((tenant_id, integration))

    def is_enabled(self, tenant_id: str, integration: str) -> bool:
        return (tenant_id, integration) not in self._disabled


class PostgresTenantIntegrationState:
    """Production tenant state backed by the integration_tenant_state table.

    Default-enabled: a tenant is enabled for an integration unless a row exists
    with enabled = FALSE. Safe for multi-replica deployments (state in the DB,
    not process memory). Mirrors the PostgresSessionStore connection model.
    """

    def __init__(self, pool: object) -> None:
        self._pool = pool

    def is_enabled(self, tenant_id: str, integration: str) -> bool:
        with self._pool.connection() as conn:  # type: ignore[union-attr]
            row = conn.execute(
                "SELECT enabled FROM integration_tenant_state "
                "WHERE tenant_id = %s AND integration = %s",
                (tenant_id, integration),
            ).fetchone()
        return True if row is None else bool(row[0])

    def _set(self, tenant_id: str, integration: str, enabled: bool) -> None:
        from app.control_plane.notifications import _now_utc

        with self._pool.connection() as conn:  # type: ignore[union-attr]
            conn.execute(
                "INSERT INTO integration_tenant_state "
                "(tenant_id, integration, enabled, updated_at) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, integration) "
                "DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = EXCLUDED.updated_at",
                (tenant_id, integration, enabled, _now_utc()),
            )
            conn.commit()

    def disable(self, tenant_id: str, integration: str) -> None:
        self._set(tenant_id, integration, False)

    def enable(self, tenant_id: str, integration: str) -> None:
        self._set(tenant_id, integration, True)


# ── Executor ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntegrationOutcome:
    # success | unknown_integration | unknown_operation | no_credentials |
    # blocked:<reason> | upstream_error | upstream_timeout
    result_class: str
    status: int | None = None
    result: dict | None = None


class IntegrationExecutor:
    """Resolves credentials, builds the request, and sends it through the guard."""

    def __init__(
        self,
        *,
        integrations: "dict[str, Integration] | None" = None,
        secret_resolver: "Callable[[str, str], dict | None] | None" = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._integrations = integrations if integrations is not None else INTEGRATIONS
        # secret_resolver(tenant_id, integration) -> env/cred mapping | None.
        self._secret_resolver = secret_resolver
        self._timeout = timeout_seconds

    def available(self, tenant_id: str, allowlist: dict) -> list[str]:
        """Integrations both registered and allow-listed for this tenant."""
        return sorted(
            name
            for name in self._integrations
            if is_allowed(allowlist, tenant_id, name)
        )

    def invoke(
        self, integration: str, operation: str, params: dict, *, tenant_id: str
    ) -> IntegrationOutcome:
        spec = self._integrations.get(integration)
        if spec is None:
            return IntegrationOutcome("unknown_integration")

        creds: dict | None = None
        if spec.requires_secret:
            creds = (
                self._secret_resolver(tenant_id, integration)
                if self._secret_resolver is not None
                else None
            )
            if not creds:
                return IntegrationOutcome("no_credentials")

        try:
            request = spec.build_request(operation, params, creds)
        except UnknownOperation:
            return IntegrationOutcome("unknown_operation")
        except Exception:  # noqa: BLE001 - a builder bug must not crash the request
            return IntegrationOutcome("upstream_error")

        # The single chokepoint: validate + pin before any byte leaves the pod.
        # permit_hosts is empty for every real integration; only the dev loopback
        # fixture sets it (to reach an in-cluster private IP). The metadata block
        # is never waived by it.
        try:
            target = validate_outbound_url(
                request.url,
                allowed_hosts=spec.allowed_hosts,
                allow_http=request.allow_http,
                permit_hosts=getattr(spec, "permit_private_hosts", frozenset()),
            )
        except OutboundReject as rej:
            return IntegrationOutcome(f"blocked:{rej.reason}")

        try:
            resp = guarded_open(
                target,
                method=request.method,
                headers=request.headers,
                body=request.body,
                timeout=self._timeout,
                max_response_bytes=_MAX_RESULT_BYTES,
            )
        except TimeoutError:
            return IntegrationOutcome("upstream_timeout")
        except OSError:
            return IntegrationOutcome("upstream_error")

        return IntegrationOutcome("success", status=resp.status, result=_decode(resp))


def _decode(resp) -> dict:
    """A content-safe-to-return view of an upstream response (caller's own data)."""
    try:
        parsed = json.loads(resp.body)
    except (ValueError, json.JSONDecodeError):
        return {"status": resp.status, "result_class": resp.result_class, "bytes": len(resp.body)}
    return {"status": resp.status, "result_class": resp.result_class, "data": parsed}


# ── Allow-list ────────────────────────────────────────────────────────────────


def parse_integration_allowlist(raw: str | None) -> dict[str, frozenset[str]]:
    """Parse INTEGRATIONS_ALLOWLIST JSON: {"<tenant>": ["<integration>"]} — deny by default."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        logger.warning("INTEGRATIONS_ALLOWLIST is not valid JSON — treating as empty (deny all).")
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(tenant): frozenset(str(i) for i in items)
        for tenant, items in data.items()
        if isinstance(items, list)
    }


# ── HTTP handlers ─────────────────────────────────────────────────────────────


def _gate(authorization, token_verifier, enabled):
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return None, None, err
    tenant_id = _extract_tenant_id(claims)
    user_id = claims.subject
    if not enabled:
        return None, None, (HTTPStatus.SERVICE_UNAVAILABLE, {
            "error": "integrations_disabled",
            "detail": "Personal-information integrations are disabled on this instance.",
            "status": "degraded",
        })
    return tenant_id, user_id, None


def build_integrations_list_response(
    *, authorization, body, token_verifier, enabled, allowlist, executor,
) -> tuple[int, dict]:
    """POST /v1/integrations/list — integrations allow-listed for the caller's tenant."""
    tenant_id, _user_id, err = _gate(authorization, token_verifier, enabled)
    if err is not None:
        return err
    return HTTPStatus.OK, {"integrations": executor.available(tenant_id, allowlist)}


def build_integrations_invoke_response(
    *, authorization, body, token_verifier, enabled, allowlist, executor,
    rate_limiter: RateLimiter, tenant_state: TenantIntegrationState,
    notification_store: NotificationStore | None = None,
) -> tuple[int, dict]:
    """POST /v1/integrations/invoke — body {"integration", "operation", "params"}."""
    tenant_id, user_id, err = _gate(authorization, token_verifier, enabled)
    if err is not None:
        return err
    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    integration = data.get("integration")
    operation = data.get("operation")
    params = data.get("params", {})
    if not isinstance(integration, str) or not integration:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'integration' is required."}
    if not isinstance(operation, str) or not operation:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'operation' is required."}
    if not isinstance(params, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'params' must be an object."}
    if len(json.dumps(params)) > _MAX_PARAMS_BYTES:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "payload_too_large"}

    label = f"integration:{integration}/{operation}"

    # Deny by default: must be allow-listed for this tenant. Rejected before any
    # credential resolution or egress, and audited.
    if not is_allowed(allowlist, tenant_id, integration):
        _audit(tenant=tenant_id, user=user_id, tool=label, arguments=params, decision="denied")
        return HTTPStatus.FORBIDDEN, {"error": "integration_not_allowed"}

    # Operator per-tenant disable overrides an otherwise-allowed tenant.
    if not tenant_state.is_enabled(tenant_id, integration):
        _audit(tenant=tenant_id, user=user_id, tool=label, arguments=params, decision="tenant_disabled")
        return HTTPStatus.FORBIDDEN, {"error": "tenant_disabled"}

    now = int(time.time())
    if not rate_limiter.try_acquire(tenant_id, now=now):
        _audit(tenant=tenant_id, user=user_id, tool=label, arguments=params, decision="rate_limited")
        return HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate_limited"}

    started = time.monotonic()
    try:
        outcome = executor.invoke(integration, operation, params, tenant_id=tenant_id)
    finally:
        rate_limiter.release()
    latency_ms = int((time.monotonic() - started) * 1000)
    _audit(tenant=tenant_id, user=user_id, tool=label, arguments=params,
           decision="allowed", result_class=outcome.result_class, latency_ms=latency_ms)

    _maybe_notify(notification_store, tenant_id, user_id, integration, outcome)

    return _to_http(outcome)


def _to_http(outcome: IntegrationOutcome) -> tuple[int, dict]:
    rc = outcome.result_class
    if rc == "success":
        return HTTPStatus.OK, {"status": outcome.status, "result": outcome.result}
    if rc == "unknown_integration":
        return HTTPStatus.NOT_FOUND, {"error": "unknown_integration"}
    if rc == "unknown_operation":
        return HTTPStatus.NOT_FOUND, {"error": "unknown_operation"}
    if rc == "no_credentials":
        return HTTPStatus.BAD_GATEWAY, {"error": "no_credentials", "status": "degraded"}
    if rc.startswith("blocked:"):
        # The URL guard refused the target (SSRF defense). Do not echo the URL.
        return HTTPStatus.BAD_GATEWAY, {"error": "outbound_blocked", "reason": rc.split(":", 1)[1]}
    if rc == "upstream_timeout":
        return HTTPStatus.GATEWAY_TIMEOUT, {"error": "upstream_timeout", "status": "degraded"}
    return HTTPStatus.BAD_GATEWAY, {"error": "upstream_error", "status": "degraded"}


def _maybe_notify(notification_store, tenant_id, user_id, integration, outcome) -> None:
    if notification_store is None:
        return
    event = "agent_task_completed" if outcome.result_class == "success" else "agent_task_failed"
    try:
        from app.control_plane.notifications import (
            ALLOWED_EVENT_CLASSES,
            NotificationEvent,
            _now_utc,
        )
        if event in ALLOWED_EVENT_CLASSES:
            notification_store.publish(NotificationEvent(
                id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id,
                event_class=event, resource_id=f"integration:{integration}",
                created_at=_now_utc()))
    except Exception:  # pragma: no cover - best-effort
        pass
