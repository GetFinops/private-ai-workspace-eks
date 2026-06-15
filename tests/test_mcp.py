"""Tests for the M12 MCP integration.

The stub MCP server is spawned OUT-OF-PROCESS over stdio (real JSON-RPC), so the
success paths prove the sandboxed connection end-to-end. Covers: allow-list
parsing, the stub server's protocol, the executor (list/call/unknown/timeout/
crash), and the handlers (auth, kill-switch, deny-by-default + cross-tenant,
validation, rate limit, success with result, unknown tool, notification).
"""
import json
import sys
import unittest

from app.control_plane.mcp import (
    MCPExecutor,
    build_mcp_invoke_response,
    build_mcp_list_response,
    parse_mcp_allowlist,
)
from app.control_plane.agent_tools import RateLimiter
from app.control_plane.notifications import InMemoryNotificationStore
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError
from app.mcp_servers import stub_server


class _Verifier:
    def __init__(self, email="alice@tenant-a.test"):
        self._claims = TokenClaims(subject="user-a", email=email)

    def verify(self, raw):
        if raw != "valid":
            raise TokenVerificationError("bad")
        return self._claims


_ALICE = _Verifier("alice@tenant-a.test")
_BOB = _Verifier("bob@tenant-b.test")
_ALLOW = parse_mcp_allowlist(json.dumps({"tenant-a.test": ["stub"]}))


class TestStubServer(unittest.TestCase):
    def test_initialize_and_list(self):
        init = stub_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init["result"]["serverInfo"]["name"], "stub-mcp")
        tools = stub_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(tools["result"]["tools"][0]["name"], "echo")

    def test_call_echo(self):
        r = stub_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                "params": {"name": "echo", "arguments": {"message": "hi"}}})
        self.assertEqual(r["result"]["content"][0]["text"], "hi")

    def test_unknown_tool_and_notification(self):
        r = stub_server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                "params": {"name": "nope", "arguments": {}}})
        self.assertEqual(r["error"]["code"], -32601)
        # Notifications (no id) produce no response.
        self.assertIsNone(stub_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))


class TestExecutor(unittest.TestCase):
    def test_list_tools_out_of_process(self):
        out = MCPExecutor().list_tools("stub", "tenant-a.test")
        self.assertEqual(out.result_class, "success")
        self.assertEqual(out.result["tools"][0]["name"], "echo")

    def test_call_tool_out_of_process(self):
        out = MCPExecutor().call_tool("stub", "echo", {"message": "round-trip"}, "tenant-a.test")
        self.assertEqual(out.result_class, "success")
        self.assertEqual(out.result["content"][0]["text"], "round-trip")

    def test_unknown_tool(self):
        out = MCPExecutor().call_tool("stub", "does_not_exist", {}, "tenant-a.test")
        self.assertEqual(out.result_class, "unknown_tool")

    def test_unknown_server(self):
        self.assertEqual(MCPExecutor().call_tool("ghost", "x", {}, "t").result_class, "unknown_tool")

    def test_timeout(self):
        ex = MCPExecutor(
            servers={"slow": {"command": [sys.executable, "-c", "import time; time.sleep(30)"],
                              "requires_secret": None}},
            timeout_seconds=0.5)
        self.assertEqual(ex.call_tool("slow", "x", {}, "t").result_class, "server_timeout")

    def test_server_crash(self):
        ex = MCPExecutor(servers={"boom": {"command": [sys.executable, "-c", "import sys; sys.exit(3)"],
                                           "requires_secret": None}})
        self.assertEqual(ex.call_tool("boom", "x", {}, "t").result_class, "server_error")

    def test_per_tenant_secret_injected_only_when_required(self):
        seen = {}

        def resolver(tenant, key):
            seen["call"] = (tenant, key)
            return {"SECRET_ENV": "value"}

        ex = MCPExecutor(secret_resolver=resolver)
        # The stub requires no secret → resolver must NOT be called.
        ex.call_tool("stub", "echo", {"message": "x"}, "tenant-a.test")
        self.assertNotIn("call", seen)
        # A server that declares a secret → resolver called with that tenant.
        ex2 = MCPExecutor(
            servers={"s": {"command": [sys.executable, "-m", "app.mcp_servers.stub_server"],
                           "requires_secret": "some/key"}},
            secret_resolver=resolver)
        ex2.call_tool("s", "echo", {"message": "x"}, "tenant-a.test")
        self.assertEqual(seen["call"], ("tenant-a.test", "some/key"))


def _invoke(verifier=_ALICE, enabled=True, allowlist=None, body=None, rate_limiter=None, notes=None):
    return build_mcp_invoke_response(
        authorization="Bearer valid",
        body=body if body is not None else json.dumps(
            {"server": "stub", "tool": "echo", "arguments": {"message": "hi"}}).encode(),
        token_verifier=verifier, enabled=enabled,
        allowlist=_ALLOW if allowlist is None else allowlist,
        executor=MCPExecutor(), rate_limiter=rate_limiter or RateLimiter(), notification_store=notes)


class TestInvokeHandler(unittest.TestCase):
    def test_requires_auth(self):
        status, _ = build_mcp_invoke_response(
            authorization=None, body=b"{}", token_verifier=_ALICE, enabled=True,
            allowlist=_ALLOW, executor=MCPExecutor(), rate_limiter=RateLimiter())
        self.assertEqual(status, 401)

    def test_kill_switch(self):
        status, payload = _invoke(enabled=False)
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "mcp_disabled")

    def test_deny_by_default(self):
        self.assertEqual(_invoke(allowlist={})[0], 403)

    def test_cross_tenant_denied(self):
        # Bob's tenant is not allow-listed for the stub server.
        status, payload = _invoke(verifier=_BOB)
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "server_not_allowed")

    def test_bad_json(self):
        self.assertEqual(_invoke(body=b"not json")[0], 400)

    def test_missing_fields(self):
        self.assertEqual(_invoke(body=json.dumps({"server": "stub"}).encode())[0], 400)

    def test_rate_limit(self):
        rl = RateLimiter(per_minute=1, max_concurrency=4)
        self.assertEqual(_invoke(rate_limiter=rl)[0], 200)
        self.assertEqual(_invoke(rate_limiter=rl)[0], 429)

    def test_success_with_result(self):
        status, payload = _invoke()
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["content"][0]["text"], "hi")

    def test_unknown_tool_is_404(self):
        body = json.dumps({"server": "stub", "tool": "ghost", "arguments": {}}).encode()
        self.assertEqual(_invoke(body=body)[0], 404)

    def test_success_emits_notification(self):
        notes = InMemoryNotificationStore()
        _invoke(notes=notes)
        feed = notes.list_for_user(tenant_id="tenant-a.test", user_id="user-a")
        self.assertEqual([n.event_class for n in feed], ["agent_task_completed"])


class TestListHandler(unittest.TestCase):
    def test_list_success(self):
        status, payload = build_mcp_list_response(
            authorization="Bearer valid", body=json.dumps({"server": "stub"}).encode(),
            token_verifier=_ALICE, enabled=True, allowlist=_ALLOW, executor=MCPExecutor())
        self.assertEqual(status, 200)
        self.assertEqual(payload["tools"][0]["name"], "echo")

    def test_list_deny_by_default(self):
        status, _ = build_mcp_list_response(
            authorization="Bearer valid", body=json.dumps({"server": "stub"}).encode(),
            token_verifier=_BOB, enabled=True, allowlist=_ALLOW, executor=MCPExecutor())
        self.assertEqual(status, 403)

    def test_list_kill_switch(self):
        status, _ = build_mcp_list_response(
            authorization="Bearer valid", body=json.dumps({"server": "stub"}).encode(),
            token_verifier=_ALICE, enabled=False, allowlist=_ALLOW, executor=MCPExecutor())
        self.assertEqual(status, 503)


class TestAllowlistParsing(unittest.TestCase):
    def test_parse(self):
        al = parse_mcp_allowlist('{"t": ["a", "b"]}')
        self.assertEqual(al["t"], frozenset({"a", "b"}))
        self.assertEqual(parse_mcp_allowlist("bad json"), {})
        self.assertEqual(parse_mcp_allowlist(None), {})


if __name__ == "__main__":
    unittest.main()
