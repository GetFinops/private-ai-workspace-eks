"""Tests for the M13 integration harness (app/control_plane/integrations.py).

No real network: the success path patches DNS to a public IP and stubs the
guarded send; the SSRF-block path patches DNS to a private IP so the real URL
guard refuses it (proving egress routes through the guard). Covers gating,
deny-by-default allow-list, cross-tenant denial, per-tenant operator disable,
rate limiting, credential resolution, outcome→HTTP mapping, and audit
content-safety.
"""
import json
import socket
import unittest
from http import HTTPStatus
from unittest import mock

from app.control_plane import integrations as integ
from app.control_plane import outbound
from app.control_plane.agent_tools import RateLimiter
from app.control_plane.integrations import (
    InMemoryTenantIntegrationState,
    IntegrationExecutor,
    OutboundRequest,
    UnknownOperation,
    build_integrations_invoke_response,
    build_integrations_list_response,
    parse_integration_allowlist,
)
from app.control_plane.outbound import OutboundResponse
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


class _Verifier:
    def __init__(self, email):
        self._claims = TokenClaims(subject="user-x", email=email)

    def verify(self, raw):
        if raw != "valid":
            raise TokenVerificationError("bad")
        return self._claims


_ALICE = _Verifier("alice@tenant-a.test")   # tenant-a.test
_BOB = _Verifier("bob@tenant-b.test")        # tenant-b.test
_ALLOW = parse_integration_allowlist(json.dumps({"tenant-a.test": ["calendar"]}))


class _FakeIntegration:
    def __init__(self, *, requires_secret=False, url="https://api.example.com/cal", raise_op=False):
        self.name = "calendar"
        self.allowed_hosts = frozenset({"api.example.com"})
        self.requires_secret = requires_secret
        self._url = url
        self._raise_op = raise_op

    def build_request(self, operation, params, creds):
        if self._raise_op:
            raise UnknownOperation()
        return OutboundRequest(method="GET", url=self._url, headers={})


def _registry(**kw):
    return {"calendar": _FakeIntegration(**kw)}


def _invoke(body, *, verifier=_ALICE, enabled=True, allowlist=_ALLOW, executor=None,
            rate_limiter=None, tenant_state=None, store=None):
    return build_integrations_invoke_response(
        authorization="Bearer valid",
        body=json.dumps(body),
        token_verifier=verifier,
        enabled=enabled,
        allowlist=allowlist,
        executor=executor or IntegrationExecutor(integrations=_registry()),
        rate_limiter=rate_limiter or RateLimiter(),
        tenant_state=tenant_state or InMemoryTenantIntegrationState(),
        notification_store=store,
    )


_GOOD = {"integration": "calendar", "operation": "list_events", "params": {}}


class TestAllowlistParse(unittest.TestCase):
    def test_deny_by_default_on_garbage(self):
        self.assertEqual(parse_integration_allowlist("not json"), {})
        self.assertEqual(parse_integration_allowlist(None), {})

    def test_parses_tenants(self):
        out = parse_integration_allowlist('{"t": ["calendar", "mail"]}')
        self.assertEqual(out["t"], frozenset({"calendar", "mail"}))


class TestGating(unittest.TestCase):
    def test_anonymous_unauthorized(self):
        status, _ = build_integrations_list_response(
            authorization=None, body="", token_verifier=_ALICE, enabled=True,
            allowlist=_ALLOW, executor=IntegrationExecutor(integrations=_registry()))
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)

    def test_kill_switch_off_returns_503(self):
        status, payload = _invoke(_GOOD, enabled=False)
        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(payload["error"], "integrations_disabled")

    def test_list_returns_allowlisted_and_registered(self):
        status, payload = build_integrations_list_response(
            authorization="Bearer valid", body="", token_verifier=_ALICE, enabled=True,
            allowlist=_ALLOW, executor=IntegrationExecutor(integrations=_registry()))
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["integrations"], ["calendar"])

    def test_list_empty_for_unlisted_tenant(self):
        status, payload = build_integrations_list_response(
            authorization="Bearer valid", body="", token_verifier=_BOB, enabled=True,
            allowlist=_ALLOW, executor=IntegrationExecutor(integrations=_registry()))
        self.assertEqual(payload["integrations"], [])


class TestAuthorization(unittest.TestCase):
    def test_not_allowlisted_denied(self):
        status, payload = _invoke({"integration": "mail", "operation": "x", "params": {}})
        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertEqual(payload["error"], "integration_not_allowed")

    def test_cross_tenant_denied(self):
        # tenant-b token, integration allow-listed only for tenant-a.
        status, payload = _invoke(_GOOD, verifier=_BOB)
        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertEqual(payload["error"], "integration_not_allowed")

    def test_tenant_disabled(self):
        state = InMemoryTenantIntegrationState()
        state.disable("tenant-a.test", "calendar")
        status, payload = _invoke(_GOOD, tenant_state=state)
        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertEqual(payload["error"], "tenant_disabled")

    def test_rate_limited(self):
        rl = RateLimiter(per_minute=1, max_concurrency=4)
        with _public_dns(), mock.patch.object(integ, "guarded_open", return_value=_resp()):
            first, _ = _invoke(_GOOD, rate_limiter=rl)
        self.assertEqual(first, HTTPStatus.OK)
        second, payload = _invoke(_GOOD, rate_limiter=rl)
        self.assertEqual(second, HTTPStatus.TOO_MANY_REQUESTS)
        self.assertEqual(payload["error"], "rate_limited")


def _public_dns():
    info = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))]
    return mock.patch.object(outbound.socket, "getaddrinfo", return_value=info)


def _private_dns():
    info = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.5", 443))]
    return mock.patch.object(outbound.socket, "getaddrinfo", return_value=info)


def _resp(status=200, body=b'{"events": []}'):
    return OutboundResponse(status=status, headers={"content-type": "application/json"}, body=body)


class TestInvokeOutcomes(unittest.TestCase):
    def test_success_round_trip(self):
        with _public_dns(), mock.patch.object(integ, "guarded_open", return_value=_resp()) as go:
            status, payload = _invoke(_GOOD)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["status"], 200)
        self.assertEqual(payload["result"]["data"], {"events": []})
        go.assert_called_once()  # egress went through the guarded sender

    def test_ssrf_target_blocked_through_guard(self):
        # build_request points at an allowed host that resolves to a private IP;
        # the real URL guard must refuse it — no guarded_open call.
        with _private_dns(), mock.patch.object(integ, "guarded_open") as go:
            status, payload = _invoke(_GOOD)
        self.assertEqual(status, HTTPStatus.BAD_GATEWAY)
        self.assertEqual(payload["error"], "outbound_blocked")
        self.assertEqual(payload["reason"], "private_ip")
        go.assert_not_called()

    def test_no_credentials(self):
        ex = IntegrationExecutor(integrations=_registry(requires_secret=True), secret_resolver=lambda t, i: None)
        status, payload = _invoke(_GOOD, executor=ex)
        self.assertEqual(status, HTTPStatus.BAD_GATEWAY)
        self.assertEqual(payload["error"], "no_credentials")

    def test_credentials_resolved_and_passed(self):
        seen = {}

        class _CredIntegration(_FakeIntegration):
            def __init__(self):
                super().__init__(requires_secret=True)

            def build_request(self, operation, params, creds):
                seen["creds"] = creds
                return OutboundRequest(method="GET", url=self._url, headers={})

        ex = IntegrationExecutor(
            integrations={"calendar": _CredIntegration()},
            secret_resolver=lambda t, i: {"TOKEN": "abc"} if (t, i) == ("tenant-a.test", "calendar") else None,
        )
        with _public_dns(), mock.patch.object(integ, "guarded_open", return_value=_resp()):
            status, _ = _invoke(_GOOD, executor=ex)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(seen["creds"], {"TOKEN": "abc"})

    def test_unknown_integration_when_allowlisted_but_unregistered(self):
        allow = parse_integration_allowlist(json.dumps({"tenant-a.test": ["ghost"]}))
        status, payload = _invoke(
            {"integration": "ghost", "operation": "x", "params": {}},
            allowlist=allow, executor=IntegrationExecutor(integrations=_registry()))
        self.assertEqual(status, HTTPStatus.NOT_FOUND)
        self.assertEqual(payload["error"], "unknown_integration")

    def test_unknown_operation(self):
        ex = IntegrationExecutor(integrations=_registry(raise_op=True))
        status, payload = _invoke(_GOOD, executor=ex)
        self.assertEqual(status, HTTPStatus.NOT_FOUND)
        self.assertEqual(payload["error"], "unknown_operation")

    def test_upstream_timeout(self):
        with _public_dns(), mock.patch.object(integ, "guarded_open", side_effect=TimeoutError):
            status, payload = _invoke(_GOOD)
        self.assertEqual(status, HTTPStatus.GATEWAY_TIMEOUT)
        self.assertEqual(payload["error"], "upstream_timeout")


class TestValidation(unittest.TestCase):
    def test_missing_fields(self):
        for bad in ({"operation": "x"}, {"integration": "calendar"}, {"integration": "calendar", "operation": "x", "params": []}):
            status, _ = _invoke(bad)
            self.assertEqual(status, HTTPStatus.BAD_REQUEST)

    def test_oversized_params(self):
        big = {"integration": "calendar", "operation": "x", "params": {"k": "v" * 200_000}}
        status, _ = _invoke(big)
        self.assertEqual(status, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)


class TestAuditContentSafety(unittest.TestCase):
    def test_audit_records_shape_not_values(self):
        params = {"calendar_id": "SUPER-SECRET-CALENDAR-ID"}
        body = {"integration": "calendar", "operation": "list_events", "params": params}
        with self.assertLogs("app.control_plane.agent_tools", level="INFO") as cm:
            with _public_dns(), mock.patch.object(integ, "guarded_open", return_value=_resp()):
                _invoke(body)
        # Inspect the structured audit payload directly (the whitelisted `audit`
        # envelope), not the rendered message.
        records = [r for r in cm.records if hasattr(r, "audit")]
        self.assertTrue(records)
        audit = records[-1].audit
        dumped = json.dumps(audit)
        # The secret value must never appear anywhere in the audit payload.
        self.assertNotIn("SUPER-SECRET-CALENDAR-ID", dumped)
        # Only its shape (key name + type/size) is recorded.
        self.assertIn("calendar_id", audit["arg_shape"])
        self.assertEqual(audit["arg_shape"]["calendar_id"]["type"], "str")
        self.assertEqual(audit["decision"], "allowed")
        self.assertEqual(audit["result_class"], "success")


if __name__ == "__main__":
    unittest.main()
