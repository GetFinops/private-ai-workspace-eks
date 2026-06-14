"""Tests for the M11 agent loop (plan→act→observe over sandboxed tools).

Covers the security-critical paths against a STUB inference client (no GPU):
auth, kill-switch, cold-inference refusal, validation, rate limiting, a real
end-to-end success through the sandbox, prompt-injection rejection (denied/
unknown tool never spawned), budget enforcement (steps / wall-clock), clean
mid-run inference failure, cross-tenant isolation, and the M9 notifications.
"""
import json
import unittest

from app.control_plane.agent_loop import (
    AgentLoopBudgets,
    _extract_json_object,
    _parse_action,
    build_agent_run_response,
    run_agent_loop,
)
from app.control_plane.agent_tools import RateLimiter, SandboxExecutor, SandboxOutcome, parse_allowlist
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
_ALLOW = parse_allowlist(json.dumps({"tenant-a.test": ["text_stats"]}))
_BUDGETS = AgentLoopBudgets(max_steps=3, wall_clock_seconds=30.0, max_tokens=64, model="test")


def _msg(content):
    return {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 5}}


class _ScriptedInference:
    """Returns scripted assistant turns; last script repeats if over-run."""

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.calls = 0

    def chat_completions(self, request):
        i = min(self.calls, len(self._scripts) - 1)
        self.calls += 1
        return _msg(self._scripts[i])


class _EchoInference:
    """Turn 1: call text_stats. Turn 2: final, echoing the last tool result —
    proves the real sandbox ran and its result flowed back into the loop."""

    def __init__(self):
        self.calls = 0

    def chat_completions(self, request):
        self.calls += 1
        if self.calls == 1:
            content = json.dumps({"action": "call_tool", "tool": "text_stats",
                                  "arguments": {"text": "hello world"}})
        else:
            tool_msgs = [m.content for m in request.messages if m.role == "tool"]
            content = json.dumps({"action": "final", "answer": tool_msgs[-1] if tool_msgs else "{}"})
        return _msg(content)


class _RaisingInference:
    def chat_completions(self, request):
        raise RuntimeError("inference down")


class _RecordingExecutor:
    """Duck-typed executor that records calls without spawning a process."""

    def __init__(self, outcome=None):
        self.calls = []
        self._outcome = outcome or SandboxOutcome("success", {"characters": 1, "words": 1, "lines": 1}, 0)

    def execute(self, tool, arguments):
        self.calls.append((tool, dict(arguments)))
        return self._outcome


def _run(verifier=_ALICE, enabled=True, allowlist=None, body=None,
         executor=None, rate_limiter=None, inference=None, budgets=_BUDGETS, notes=None):
    return build_agent_run_response(
        authorization=_AUTH,
        body=body if body is not None else json.dumps({"task": "count the words"}).encode(),
        token_verifier=verifier,
        enabled=enabled,
        allowlist=_ALLOW if allowlist is None else allowlist,
        executor=executor or _RecordingExecutor(),
        rate_limiter=rate_limiter or RateLimiter(),
        inference_client=inference if inference is not None else _ScriptedInference(
            ['{"action":"final","answer":"done"}']),
        budgets=budgets,
        notification_store=notes,
    )


class TestParsing(unittest.TestCase):
    def test_extract_json_object_tolerates_prose(self):
        self.assertEqual(
            _extract_json_object('sure, here: {"action":"final","answer":"x"} ok'),
            {"action": "final", "answer": "x"})

    def test_extract_handles_nested_and_strings(self):
        obj = _extract_json_object('{"action":"call_tool","arguments":{"text":"a}b{c"}}')
        self.assertEqual(obj["arguments"]["text"], "a}b{c")

    def test_extract_returns_none_for_garbage(self):
        self.assertIsNone(_extract_json_object("no json here"))
        self.assertIsNone(_extract_json_object("{not: valid}"))

    def test_parse_action_requires_action_key(self):
        self.assertIsNone(_parse_action('{"answer":"no action key"}'))
        self.assertEqual(_parse_action('{"action":"final"}'), {"action": "final"})


class TestGatingAndValidation(unittest.TestCase):
    def test_requires_auth(self):
        status, _ = build_agent_run_response(
            authorization=None, body=b"{}", token_verifier=_ALICE, enabled=True,
            allowlist=_ALLOW, executor=_RecordingExecutor(), rate_limiter=RateLimiter(),
            inference_client=_ScriptedInference(["{}"]), budgets=_BUDGETS)
        self.assertEqual(status, 401)

    def test_kill_switch_refuses(self):
        status, payload = _run(enabled=False)
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "tools_disabled")

    def test_cold_inference_refuses_cleanly(self):
        # inference_client=None (inference cold) must refuse, not fake work.
        status, payload = build_agent_run_response(
            authorization=_AUTH, body=json.dumps({"task": "x"}).encode(),
            token_verifier=_ALICE, enabled=True, allowlist=_ALLOW,
            executor=_RecordingExecutor(), rate_limiter=RateLimiter(),
            inference_client=None, budgets=_BUDGETS)
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "agent_runs_unavailable")

    def test_bad_json(self):
        status, _ = _run(body=b"not json")
        self.assertEqual(status, 400)

    def test_missing_task(self):
        status, _ = _run(body=json.dumps({"task": "  "}).encode())
        self.assertEqual(status, 400)

    def test_task_too_large(self):
        status, _ = _run(body=json.dumps({"task": "x" * 9000}).encode())
        self.assertEqual(status, 413)

    def test_rate_limit(self):
        rl = RateLimiter(per_minute=1, max_concurrency=4)
        s1, _ = _run(rate_limiter=rl)
        s2, p2 = _run(rate_limiter=rl)
        self.assertEqual(s1, 200)
        self.assertEqual(s2, 429)
        self.assertEqual(p2["error"], "rate_limited")


class TestLoopBehaviour(unittest.TestCase):
    def test_end_to_end_success_through_real_sandbox(self):
        # Real SandboxExecutor runs text_stats out-of-process; the echoed final
        # answer must contain the real result (hello world → 11 chars, 2 words).
        status, payload = _run(executor=SandboxExecutor(), inference=_EchoInference())
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertIn("characters", payload["answer"])
        self.assertIn("11", payload["answer"])

    def test_completed_simple_final(self):
        status, payload = _run(inference=_ScriptedInference(['{"action":"final","answer":"hi"}']))
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["answer"], "hi")

    def test_injection_denied_tool_never_spawned(self):
        # The model insists on a tool not in the allow-list every turn.
        spy = _RecordingExecutor()
        inf = _ScriptedInference(['{"action":"call_tool","tool":"shell","arguments":{"cmd":"id"}}'])
        status, payload = _run(executor=spy, inference=inf)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "budget_exhausted")
        self.assertEqual(spy.calls, [])  # never spawned

    def test_injection_unknown_tool_never_spawned(self):
        spy = _RecordingExecutor()
        inf = _ScriptedInference(['{"action":"call_tool","tool":"does_not_exist","arguments":{}}'])
        status, payload = _run(executor=spy, inference=inf)
        self.assertEqual(payload["status"], "budget_exhausted")
        self.assertEqual(spy.calls, [])

    def test_step_budget_terminates(self):
        # Always calls the allowed tool, never finishes → stops at max_steps.
        spy = _RecordingExecutor()
        inf = _ScriptedInference(['{"action":"call_tool","tool":"text_stats","arguments":{"text":"x"}}'])
        status, payload = _run(executor=spy, inference=inf, budgets=_BUDGETS)
        self.assertEqual(payload["status"], "budget_exhausted")
        self.assertEqual(payload["detail"], "max_steps")
        self.assertEqual(len(spy.calls), _BUDGETS.max_steps)

    def test_wall_clock_budget_terminates_before_work(self):
        spy = _RecordingExecutor()
        budgets = AgentLoopBudgets(max_steps=5, wall_clock_seconds=0.0, max_tokens=64, model="test")
        status, payload = _run(executor=spy, inference=_RaisingInference(), budgets=budgets)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "budget_exhausted")
        self.assertEqual(payload["detail"], "wall_clock")
        self.assertEqual(spy.calls, [])  # no inference call, no tool spawn

    def test_inference_failure_fails_run(self):
        status, payload = _run(inference=_RaisingInference())
        self.assertEqual(status, 502)
        self.assertEqual(payload["status"], "failed")

    def test_malformed_model_output_is_tolerated(self):
        # Garbage, then a valid final — the loop nudges and recovers.
        inf = _ScriptedInference(["i refuse to emit json", '{"action":"final","answer":"ok"}'])
        status, payload = _run(inference=inf)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["answer"], "ok")


class TestIsolationAndNotifications(unittest.TestCase):
    def test_cross_tenant_tool_denied(self):
        # Bob's tenant is not allow-listed for text_stats; his loop cannot run it.
        spy = _RecordingExecutor()
        inf = _ScriptedInference(['{"action":"call_tool","tool":"text_stats","arguments":{"text":"x"}}'])
        status, payload = _run(verifier=_BOB, executor=spy, inference=inf)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "budget_exhausted")
        self.assertEqual(spy.calls, [])  # denied for tenant-b

    def test_emits_progress_and_completed_notifications(self):
        notes = InMemoryNotificationStore()
        status, _ = _run(inference=_ScriptedInference(['{"action":"final","answer":"hi"}']), notes=notes)
        self.assertEqual(status, 200)
        feed = notes.list_for_user(tenant_id="tenant-a.test", user_id="user-a")
        classes = sorted(n.event_class for n in feed)
        self.assertEqual(classes, ["agent_task_completed", "agent_task_progress"])

    def test_notification_resource_id_is_not_the_task(self):
        # Content policy: the notification carries a run id, never the task text.
        notes = InMemoryNotificationStore()
        secret = "TOP-SECRET-TASK-TEXT"
        _run(body=json.dumps({"task": secret}).encode(),
             inference=_ScriptedInference(['{"action":"final","answer":"x"}']), notes=notes)
        feed = notes.list_for_user(tenant_id="tenant-a.test", user_id="user-a")
        for n in feed:
            self.assertNotIn(secret, n.resource_id)


if __name__ == "__main__":
    unittest.main()
