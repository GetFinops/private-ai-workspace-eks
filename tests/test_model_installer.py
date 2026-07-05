"""model-installer reconciler orchestration (Phase 3) — decision logic with fakes."""
from unittest import TestCase

from app.model_installer.reconciler import (
    InstallerConfig,
    Reconciler,
    is_repo_allowed,
)


class _FakeCP:
    def __init__(self, pending):
        self._pending = list(pending)
        self.status_calls = []            # [(id, status, error_class)]

    def list_pending(self):
        # a request leaves "pending" once it's no longer status=requested
        return [p for p in self._pending if p.get("status", "requested") == "requested"]

    def set_status(self, request_id, status, error_class=""):
        self.status_calls.append((request_id, status, error_class))
        for p in self._pending:
            if p["id"] == request_id:
                p["status"] = status


class _FakeEks:
    def __init__(self, fail=False):
        self.calls = []
        self._fail = fail

    def scale_nodegroup(self, cluster, nodegroup, *, desired, **_):
        self.calls.append((cluster, nodegroup, desired))
        if self._fail:
            raise RuntimeError("scale boom")


class _FakeK8s:
    def __init__(self, apply_fail=False, ready=True, current="prev/model"):
        self.set_calls = []
        self._apply_fail = apply_fail
        self._ready = ready
        self._current = current

    def get_model(self, ns, cm):
        return self._current

    def set_model(self, ns, cm, deploy, repo):
        self.set_calls.append((ns, cm, deploy, repo))
        self._current = repo
        if self._apply_fail:
            raise RuntimeError("apply boom")

    def wait_ready(self, ns, deploy, *, timeout_s, sleep):
        return self._ready


def _cfg(allow=("Qwen", "*")):
    return InstallerConfig(
        nodegroup="dev-gpu", cluster="dev", vllm_namespace="inference",
        vllm_deployment="vllm-inference", vllm_configmap="vllm-inference-model",
        allowlist=frozenset(allow), ready_timeout_s=5.0, enabled=True,
    )


def _req(rid="r1", repo="Qwen/Qwen2.5-1.5B-Instruct"):
    return {"id": rid, "hf_repo_id": repo, "status": "requested"}


def _reconciler(cp, eks, k8s, cfg=None):
    return Reconciler(cp_client=cp, eks_client=eks, k8s_client=k8s,
                      config=cfg or _cfg(), sleep=lambda *_: None)


class AllowlistTests(TestCase):
    def test_deny_by_default(self):
        self.assertFalse(is_repo_allowed(frozenset(), "Qwen/x"))

    def test_wildcard_and_org(self):
        self.assertTrue(is_repo_allowed(frozenset({"*"}), "any/thing"))
        self.assertTrue(is_repo_allowed(frozenset({"Qwen"}), "Qwen/Qwen2.5-1.5B"))
        self.assertFalse(is_repo_allowed(frozenset({"Qwen"}), "evil/x"))


class ReconcileTests(TestCase):
    def test_happy_path(self):
        cp, eks, k8s = _FakeCP([_req()]), _FakeEks(), _FakeK8s(ready=True)
        did = _reconciler(cp, eks, k8s).run_once()
        self.assertTrue(did)
        # status transitions
        self.assertEqual([s for _, s, _ in cp.status_calls], ["installing", "applied"])
        # scaled the GPU nodegroup to 1
        self.assertEqual(eks.calls, [("dev", "dev-gpu", 1)])
        # pointed vLLM at the requested repo
        self.assertEqual(k8s.set_calls[0][3], "Qwen/Qwen2.5-1.5B-Instruct")

    def test_repo_not_allowed_fails_without_touching_infra(self):
        cp, eks, k8s = _FakeCP([_req(repo="evil/backdoor")]), _FakeEks(), _FakeK8s()
        _reconciler(cp, eks, k8s, _cfg(allow=("Qwen",))).run_once()
        self.assertEqual(cp.status_calls, [("r1", "failed", "repo_not_allowed")])
        self.assertEqual(eks.calls, [])       # never scaled
        self.assertEqual(k8s.set_calls, [])   # never patched vLLM

    def test_empty_allowlist_denies(self):
        cp = _FakeCP([_req()])
        _reconciler(cp, _FakeEks(), _FakeK8s(), _cfg(allow=())).run_once()
        self.assertEqual(cp.status_calls[-1][1:], ("failed", "repo_not_allowed"))

    def test_malformed_repo_id_rejected_before_infra(self):
        # Defense-in-depth: a stored value that isn't "org/name" never reaches
        # the ConfigMap/args, even though "*" would "allow" it.
        cp, eks, k8s = _FakeCP([_req(repo="not-a-repo")]), _FakeEks(), _FakeK8s()
        _reconciler(cp, eks, k8s).run_once()
        self.assertEqual(cp.status_calls, [("r1", "failed", "repo_invalid")])
        self.assertEqual(eks.calls, [])
        self.assertEqual(k8s.set_calls, [])

    def test_scale_failure_marks_failed(self):
        cp, eks = _FakeCP([_req()]), _FakeEks(fail=True)
        _reconciler(cp, eks, _FakeK8s()).run_once()
        self.assertIn(("r1", "installing", ""), cp.status_calls)
        self.assertEqual(cp.status_calls[-1], ("r1", "failed", "gpu_provision_failed"))

    def test_apply_failure_marks_failed(self):
        cp = _FakeCP([_req()])
        _reconciler(cp, _FakeEks(), _FakeK8s(apply_fail=True)).run_once()
        self.assertEqual(cp.status_calls[-1], ("r1", "failed", "vllm_apply_failed"))

    def test_timeout_marks_failed(self):
        cp = _FakeCP([_req()])
        _reconciler(cp, _FakeEks(), _FakeK8s(ready=False)).run_once()
        self.assertEqual(cp.status_calls[-1], ("r1", "failed", "load_timeout"))

    def test_failed_load_rolls_back_to_previous_model(self):
        # A failed (but allow-listed) install must not leave shared vLLM pointing
        # at the broken model — it rolls back to what was serving before.
        cp = _FakeCP([_req(repo="meta-llama/Llama-3.1-8B")])   # allow-listed via "*"
        k8s = _FakeK8s(ready=False, current="Qwen/Qwen2.5-1.5B-Instruct")
        _reconciler(cp, _FakeEks(), k8s).run_once()
        self.assertEqual(cp.status_calls[-1], ("r1", "failed", "load_timeout"))
        self.assertEqual(k8s.set_calls[0][3], "meta-llama/Llama-3.1-8B")       # attempt
        self.assertEqual(k8s.set_calls[-1][3], "Qwen/Qwen2.5-1.5B-Instruct")   # rolled back

    def test_kill_switch_off_does_nothing(self):
        cp = _FakeCP([_req()])
        cfg = InstallerConfig(nodegroup="g", cluster="c", vllm_namespace="inference",
                              vllm_deployment="v", vllm_configmap="m",
                              allowlist=frozenset({"*"}), enabled=False)
        self.assertFalse(_reconciler(cp, _FakeEks(), _FakeK8s(), cfg).run_once())
        self.assertEqual(cp.status_calls, [])

    def test_no_pending_does_nothing(self):
        self.assertFalse(_reconciler(_FakeCP([]), _FakeEks(), _FakeK8s()).run_once())

    def test_processes_one_at_a_time(self):
        cp = _FakeCP([_req("a"), _req("b")])
        _reconciler(cp, _FakeEks(), _FakeK8s()).run_once()
        touched = {rid for rid, _, _ in cp.status_calls}
        self.assertEqual(touched, {"a"})      # only the first request this tick
