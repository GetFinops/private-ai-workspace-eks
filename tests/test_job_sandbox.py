"""Tests for the M11 Job-sandbox (tool-runner dispatcher + Job executor).

Covers the security-critical surface against fakes (no real cluster): the
locked-down Job manifest, the dispatcher's auth + tool re-validation, the
create/poll/read/reap orchestration, the runner env-input path, and the
control-plane per-tool executor selection. The control plane never touches
Kubernetes here — Job execution is delegated to the dispatcher.
"""
import json
import os
import subprocess
import sys
import unittest

from app.control_plane.agent_tools import (
    RateLimiter,
    SandboxExecutor,
    SandboxOutcome,
    build_tool_invoke_response,
    parse_allowlist,
)
from app.control_plane.job_executor import DispatcherJobExecutor
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError
from app.dispatcher.jobrunner import JobConfig, JobOutcome, run_tool_job
from app.dispatcher.jobspec import build_job_manifest, job_name
from app.dispatcher.k8sclient import K8sError
from app.dispatcher.server import DispatcherConfig, build_run_response

_CFG = JobConfig(image="ctrl:latest", namespace="agent-jobs", runner_service_account="agent-tool-runner")


def _manifest(**over):
    base = dict(
        run_id="RUN-123", tenant_id="tenant-a.test", tool="text_stats_job",
        arguments={"text": "hi"}, image="ctrl:latest", namespace="agent-jobs",
        runner_service_account="agent-tool-runner",
    )
    base.update(over)
    return build_job_manifest(**base)


class TestJobManifestIsolation(unittest.TestCase):
    """The manifest is the security boundary — assert every control is present."""

    def setUp(self):
        self.m = _manifest()
        self.pod = self.m["spec"]["template"]["spec"]
        self.container = self.pod["containers"][0]

    def test_no_credentials(self):
        self.assertFalse(self.pod["automountServiceAccountToken"])
        self.assertEqual(self.pod["serviceAccountName"], "agent-tool-runner")
        # No IRSA / role-arn anywhere in the manifest.
        self.assertNotIn("eks.amazonaws.com/role-arn", json.dumps(self.m))

    def test_container_hardening(self):
        sc = self.container["securityContext"]
        self.assertFalse(sc["allowPrivilegeEscalation"])
        self.assertTrue(sc["readOnlyRootFilesystem"])
        self.assertTrue(sc["runAsNonRoot"])
        self.assertEqual(sc["capabilities"]["drop"], ["ALL"])
        self.assertEqual(sc["seccompProfile"]["type"], "RuntimeDefault")

    def test_pod_hardening_and_no_host_paths(self):
        self.assertTrue(self.pod["securityContext"]["runAsNonRoot"])
        self.assertEqual(self.pod["restartPolicy"], "Never")
        for v in self.pod["volumes"]:
            self.assertIn("emptyDir", v)
            self.assertNotIn("hostPath", v)

    def test_bounds_and_reaping(self):
        spec = self.m["spec"]
        self.assertEqual(spec["backoffLimit"], 0)
        self.assertIn("activeDeadlineSeconds", spec)
        self.assertIn("ttlSecondsAfterFinished", spec)
        self.assertIn("limits", self.container["resources"])

    def test_not_scheduled_on_gpu(self):
        # The runner must not tolerate the GPU taint (stays on CPU nodes).
        self.assertNotIn("tolerations", self.pod)

    def test_runs_the_pure_runner_with_payload(self):
        self.assertEqual(self.container["command"], ["python3", "-m", "app.sandbox.runner"])
        env = {e["name"]: e["value"] for e in self.container["env"]}
        payload = json.loads(env["AGENT_TOOL_INPUT"])
        self.assertEqual(payload, {"tool": "text_stats_job", "arguments": {"text": "hi"}})

    def test_names_are_dns_safe(self):
        m = _manifest(run_id="Weird_ID/with.bad:chars")
        name = m["metadata"]["name"]
        self.assertTrue(name.startswith("tool-"))
        self.assertRegex(name, r"^[a-z0-9-]+$")
        self.assertEqual(job_name("RUN-123"), "tool-run-123")


class TestRunnerEnvInput(unittest.TestCase):
    def test_env_input_used_and_preferred_over_stdin(self):
        env = {**os.environ, "AGENT_TOOL_INPUT":
               json.dumps({"tool": "text_stats_job", "arguments": {"text": "a b\nc"}})}
        out = subprocess.run(
            [sys.executable, "-m", "app.sandbox.runner"],
            input=b'{"tool":"text_stats","arguments":{"text":"IGNORED-STDIN"}}',
            capture_output=True, env=env, cwd=os.getcwd(),
        )
        data = json.loads(out.stdout)
        self.assertTrue(data["ok"])
        # "a b\nc" → 5 chars, 3 words, 2 lines (env input, not the stdin payload)
        self.assertEqual(data["result"], {"characters": 5, "words": 3, "lines": 2})


class _FakeK8s:
    """Scripted Kubernetes client for the orchestration tests."""

    def __init__(self, *, job_states, pods=None, log="", create_error=False):
        self._job_states = list(job_states)   # successive get_job() returns
        self._pods = pods or []
        self._log = log
        self._create_error = create_error
        self.created = None
        self.deleted = []

    def create_job(self, manifest):
        if self._create_error:
            raise K8sError("boom")
        self.created = manifest
        return manifest

    def get_job(self, name):
        return self._job_states.pop(0) if self._job_states else {}

    def list_pods(self, selector):
        return self._pods

    def read_pod_log(self, pod_name, limit_bytes=65536):
        return self._log

    def delete_job(self, name):
        self.deleted.append(name)


def _run(k8s, **over):
    kw = dict(k8s=k8s, cfg=_CFG, run_id="RUN-1", tenant_id="t.test",
              tool="text_stats_job", arguments={"text": "x"}, sleep=lambda *_: None)
    kw.update(over)
    return run_tool_job(**kw)


class TestJobOrchestration(unittest.TestCase):
    def test_success_reads_log_and_reaps(self):
        pods = [{"metadata": {"name": "tool-run-1-abc"}}]
        k8s = _FakeK8s(job_states=[{"status": {"succeeded": 1}}], pods=pods,
                       log='{"ok": true, "result": {"characters": 1, "words": 1, "lines": 1}}')
        out = _run(k8s)
        self.assertEqual(out.result_class, "success")
        self.assertEqual(out.result, {"characters": 1, "words": 1, "lines": 1})
        self.assertEqual(k8s.deleted, [job_name("RUN-1")])   # always reaped

    def test_deadline_exceeded_is_timeout(self):
        k8s = _FakeK8s(job_states=[{"status": {"failed": 1, "conditions": [
            {"type": "Failed", "reason": "DeadlineExceeded"}]}}])
        out = _run(k8s)
        self.assertEqual(out.result_class, "tool_timeout")
        self.assertEqual(k8s.deleted, [job_name("RUN-1")])

    def test_other_failure_is_tool_error_with_exit_code(self):
        pods = [{"metadata": {"name": "p"}, "status": {"containerStatuses": [
            {"state": {"terminated": {"exitCode": 7}}}]}}]
        k8s = _FakeK8s(job_states=[{"status": {"failed": 1, "conditions": [
            {"type": "Failed", "reason": "BackoffLimitExceeded"}]}}], pods=pods)
        out = _run(k8s)
        self.assertEqual(out.result_class, "tool_error")
        self.assertEqual(out.exit_code, 7)

    def test_wait_timeout(self):
        # Never succeeds/fails; monotonic jumps past the deadline.
        clock = iter([0.0, 100.0, 200.0])
        k8s = _FakeK8s(job_states=[{}, {}, {}])
        out = _run(k8s, monotonic=lambda: next(clock))
        self.assertEqual(out.result_class, "tool_timeout")
        self.assertEqual(k8s.deleted, [job_name("RUN-1")])

    def test_bad_pod_log_is_tool_error(self):
        k8s = _FakeK8s(job_states=[{"status": {"succeeded": 1}}],
                       pods=[{"metadata": {"name": "p"}}], log="not json")
        self.assertEqual(_run(k8s).result_class, "tool_error")

    def test_create_failure_is_tool_error(self):
        k8s = _FakeK8s(job_states=[], create_error=True)
        self.assertEqual(_run(k8s).result_class, "tool_error")


_DCFG = DispatcherConfig(token="secret-token", image="ctrl:latest", namespace="agent-jobs")


def _disp(authorization="Bearer secret-token", body=None, cfg=_DCFG, runner=None):
    if body is None:
        body = json.dumps({"tenant_id": "t.test", "run_id": "r1",
                           "tool": "text_stats_job", "arguments": {"text": "x"}}).encode()
    fake_runner = runner or (lambda **kw: JobOutcome("success", result={"characters": 1}))
    return build_run_response(authorization=authorization, body=body, config=cfg,
                              k8s=object(), runner=fake_runner)


class TestDispatcherHandler(unittest.TestCase):
    def test_unconfigured_returns_503(self):
        status, _ = _disp(cfg=DispatcherConfig(token=None, image="x"))
        self.assertEqual(status, 503)

    def test_missing_token_401(self):
        self.assertEqual(_disp(authorization=None)[0], 401)

    def test_wrong_token_401(self):
        self.assertEqual(_disp(authorization="Bearer nope")[0], 401)

    def test_happy_path_200(self):
        status, payload = _disp()
        self.assertEqual(status, 200)
        self.assertEqual(payload["result_class"], "success")

    def test_non_job_tool_rejected(self):
        body = json.dumps({"tenant_id": "t", "run_id": "r", "tool": "text_stats",
                           "arguments": {"text": "x"}}).encode()
        status, payload = _disp(body=body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "not_a_job_tool")

    def test_unknown_tool_rejected(self):
        body = json.dumps({"tenant_id": "t", "run_id": "r", "tool": "nope",
                           "arguments": {}}).encode()
        self.assertEqual(_disp(body=body)[0], 400)

    def test_missing_identifiers_400(self):
        body = json.dumps({"tool": "text_stats_job", "arguments": {"text": "x"}}).encode()
        self.assertEqual(_disp(body=body)[0], 400)

    def test_bad_json_400(self):
        self.assertEqual(_disp(body=b"not json")[0], 400)


# ── Control-plane per-tool executor selection ─────────────────────────────────

class _Verifier:
    def __init__(self, email="alice@tenant-a.test"):
        self._claims = TokenClaims(subject="user-a", email=email)

    def verify(self, raw):
        if raw != "valid":
            raise TokenVerificationError("bad")
        return self._claims


class _SpyJobExecutor:
    def __init__(self, outcome):
        self.calls = []
        self._outcome = outcome

    def execute(self, tool, arguments, *, tenant_id, run_id):
        self.calls.append((tool, tenant_id, run_id))
        return self._outcome


class _SpySubprocess:
    def __init__(self):
        self.calls = []

    def execute(self, tool, arguments):
        self.calls.append(tool)
        return SandboxOutcome("success", {"characters": 1, "words": 1, "lines": 1}, 0)


_ALLOW_JOB = parse_allowlist(json.dumps({"tenant-a.test": ["text_stats_job", "text_stats"]}))


def _invoke(tool, job_executor=None, subproc=None):
    return build_tool_invoke_response(
        authorization="Bearer valid",
        body=json.dumps({"tool": tool, "arguments": {"text": "hi"}}).encode(),
        token_verifier=_Verifier(), enabled=True, allowlist=_ALLOW_JOB,
        executor=subproc or _SpySubprocess(), rate_limiter=RateLimiter(),
        job_executor=job_executor,
    )


class TestExecutorSelection(unittest.TestCase):
    def test_job_tool_routes_to_dispatcher(self):
        spy = _SpyJobExecutor(SandboxOutcome("success", {"characters": 2, "words": 1, "lines": 1}, 0))
        sub = _SpySubprocess()
        status, payload = _invoke("text_stats_job", job_executor=spy, subproc=sub)
        self.assertEqual(status, 200)
        self.assertEqual(len(spy.calls), 1)         # went to the Job dispatcher
        self.assertEqual(sub.calls, [])             # NOT the subprocess sandbox
        self.assertEqual(spy.calls[0][1], "tenant-a.test")

    def test_job_tool_without_dispatcher_is_tool_error(self):
        status, payload = _invoke("text_stats_job", job_executor=None)
        self.assertEqual(status, 502)               # tool_error maps to 502

    def test_subprocess_tool_still_uses_subprocess(self):
        spy = _SpyJobExecutor(SandboxOutcome("success", {}, 0))
        sub = _SpySubprocess()
        status, _ = _invoke("text_stats", job_executor=spy, subproc=sub)
        self.assertEqual(status, 200)
        self.assertEqual(sub.calls, ["text_stats"])  # subprocess
        self.assertEqual(spy.calls, [])              # not the dispatcher


class TestDispatcherJobExecutorClient(unittest.TestCase):
    def test_unconfigured_is_tool_error(self):
        ex = DispatcherJobExecutor(base_url=None, token=None)
        self.assertFalse(ex.configured)
        out = ex.execute("text_stats_job", {"text": "x"}, tenant_id="t", run_id="r")
        self.assertEqual(out.result_class, "tool_error")


if __name__ == "__main__":
    unittest.main()
