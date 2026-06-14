"""Unit/integration tests for the M11 agent tool framework.

Covers the security-critical paths: kill-switch, deny-by-default allow-list,
per-tenant isolation, argument validation, rate limiting, sandboxed success,
out-of-process timeout/crash containment, env scrubbing, audit shape, and the
M9 notification.
"""
import json
import sys
import unittest

from app.control_plane.agent_tools import (
    RateLimiter,
    SandboxExecutor,
    _arg_shape,
    build_tool_invoke_response,
    is_allowed,
    parse_allowlist,
)
from app.control_plane.notifications import InMemoryNotificationStore
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


class _Verifier:
    def __init__(self, subject="user-a", email="alice@tenant-a.test"):
        self._claims = TokenClaims(subject=subject, email=email)

    def verify(self, raw_token):
        if raw_token != "valid":
            raise TokenVerificationError("bad token")
        return self._claims


_AUTH = "Bearer valid"
_ALICE = _Verifier(subject="user-a", email="alice@tenant-a.test")   # tenant-a.test
_BOB = _Verifier(subject="user-b", email="bob@tenant-b.test")       # tenant-b.test
# Allow-list: only tenant-a.test may run text_stats.
_ALLOW = parse_allowlist(json.dumps({"tenant-a.test": ["text_stats"]}))


def _body(tool="text_stats", **args):
    return json.dumps({"tool": tool, "arguments": args}).encode()


def _invoke(verifier=_ALICE, enabled=True, allowlist=None, body=None,
            executor=None, rate_limiter=None, notes=None):
    return build_tool_invoke_response(
        authorization=_AUTH,
        body=body if body is not None else _body(text="hello world"),
        token_verifier=verifier,
        enabled=enabled,
        allowlist=_ALLOW if allowlist is None else allowlist,
        executor=executor or SandboxExecutor(),
        rate_limiter=rate_limiter or RateLimiter(),
        notification_store=notes,
    )


class TestAuthAndGating(unittest.TestCase):
    def test_requires_auth(self):
        status, _ = build_tool_invoke_response(
            authorization=None, body=_body(text="x"), token_verifier=_ALICE,
            enabled=True, allowlist=_ALLOW, executor=SandboxExecutor(), rate_limiter=RateLimiter(),
        )
        self.assertEqual(status, 401)

    def test_kill_switch_disables(self):
        status, payload = _invoke(enabled=False)
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "tools_disabled")

    def test_deny_by_default(self):
        # Empty allow-list → 403, nothing spawned.
        status, payload = _invoke(allowlist={})
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "tool_not_allowed")

    def test_cross_tenant_isolation(self):
        # Bob (tenant-b) is not allow-listed for text_stats → 403.
        status, _ = _invoke(verifier=_BOB)
        self.assertEqual(status, 403)

    def test_unknown_tool_is_403_not_revealed(self):
        status, payload = _invoke(body=_body(tool="shell", cmd="rm -rf /"))
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "tool_not_allowed")


class TestValidationAndLimits(unittest.TestCase):
    def test_missing_required_arg(self):
        status, _ = _invoke(body=json.dumps({"tool": "text_stats", "arguments": {}}).encode())
        self.assertEqual(status, 400)

    def test_bad_json(self):
        status, _ = _invoke(body=b"not json")
        self.assertEqual(status, 400)

    def test_rate_limit(self):
        rl = RateLimiter(per_minute=1, max_concurrency=4)
        s1, _ = _invoke(rate_limiter=rl)
        s2, p2 = _invoke(rate_limiter=rl)
        self.assertEqual(s1, 200)
        self.assertEqual(s2, 429)
        self.assertEqual(p2["error"], "rate_limited")


class TestSandboxSuccess(unittest.TestCase):
    def test_sandboxed_text_stats(self):
        status, payload = _invoke(body=_body(text="hello world\nsecond line"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["result_class"], "success")
        self.assertEqual(payload["result"], {"characters": 23, "words": 4, "lines": 2})

    def test_success_emits_notification(self):
        notes = InMemoryNotificationStore()
        status, _ = _invoke(body=_body(text="hi"), notes=notes)
        self.assertEqual(status, 200)
        feed = notes.list_for_user(tenant_id="tenant-a.test", user_id="user-a")
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0].event_class, "agent_task_completed")


class TestSandboxIsolationMechanics(unittest.TestCase):
    def test_env_is_scrubbed(self):
        env = SandboxExecutor()._child_env()
        self.assertNotIn("DATABASE_URL", env)
        self.assertFalse(any(k.startswith("AWS_") for k in env))
        # Only the minimal import-enabling vars are present.
        self.assertEqual(env.get("PYTHONDONTWRITEBYTECODE"), "1")

    def test_timeout_kills_child(self):
        # A runner that sleeps forever must be killed and mapped to tool_timeout.
        ex = SandboxExecutor(
            runner_cmd=[sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds=0.5,
        )
        out = ex.execute("text_stats", {"text": "x"})
        self.assertEqual(out.result_class, "tool_timeout")

    def test_crash_is_contained(self):
        # A runner that exits non-zero → tool_error, not an exception.
        ex = SandboxExecutor(
            runner_cmd=[sys.executable, "-c", "import sys; sys.exit(3)"],
        )
        out = ex.execute("text_stats", {"text": "x"})
        self.assertEqual(out.result_class, "tool_error")
        self.assertEqual(out.exit_code, 3)

    def test_bad_output_is_tool_error(self):
        ex = SandboxExecutor(runner_cmd=[sys.executable, "-c", "print('not json')"])
        out = ex.execute("text_stats", {"text": "x"})
        self.assertEqual(out.result_class, "tool_error")


class TestHelpers(unittest.TestCase):
    def test_allowlist_parsing_and_check(self):
        al = parse_allowlist('{"t": ["a", "b"]}')
        self.assertTrue(is_allowed(al, "t", "a"))
        self.assertFalse(is_allowed(al, "t", "z"))
        self.assertFalse(is_allowed(al, "other", "a"))
        self.assertEqual(parse_allowlist("not json"), {})
        self.assertEqual(parse_allowlist(None), {})

    def test_arg_shape_has_no_values(self):
        shape = _arg_shape({"text": "secret content", "n": 5})
        self.assertEqual(shape["text"], {"type": "str", "size": 14})
        self.assertEqual(shape["n"], {"type": "int", "size": None})
        # No raw values anywhere in the shape.
        self.assertNotIn("secret content", json.dumps(shape))


if __name__ == "__main__":
    unittest.main()
