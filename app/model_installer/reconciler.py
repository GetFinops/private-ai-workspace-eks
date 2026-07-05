"""Model-installer reconciler — the automated apply loop (design Phase 3).

A SEPARATE, tightly-scoped component (NOT the control plane). It picks up
tenant-scoped install *requests* the control plane recorded and makes the model
actually servable:

    requested -> installing -> (scale GPU nodegroup 0->1) -> (point vLLM at the
    model + restart) -> (wait /health ready) -> applied | failed

Least-privilege blast radius (enforced by its IRSA + RBAC, not this code):
  - AWS: ``eks:UpdateNodegroupConfig`` on ONE GPU nodegroup only.
  - K8s: patch ONE ConfigMap + ONE Deployment in the ``inference`` namespace only.
The control plane keeps NO infra rights; the reconciler never reaches user data.

This module is the pure orchestration: the AWS/K8s/control-plane calls are behind
injected clients so the decision logic is unit-tested with fakes. Deny-by-default:
a repo not on the installer's own allow-list is failed, never installed.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger("model_installer")

# Defense-in-depth: re-validate the repo id shape before it reaches the ConfigMap
# / vLLM args, even though the control plane validated it at request time.
_HF_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")

# Reasons surfaced to the user as request.error_class (content-safe enums only).
ERR_NOT_ALLOWED = "repo_not_allowed"
ERR_INVALID = "repo_invalid"
ERR_SCALE = "gpu_provision_failed"
ERR_APPLY = "vllm_apply_failed"
ERR_TIMEOUT = "load_timeout"
ERR_UNKNOWN = "install_failed"


@dataclass(frozen=True)
class InstallerConfig:
    nodegroup: str                 # the GPU managed node group name
    cluster: str                   # EKS cluster name
    vllm_namespace: str            # e.g. "inference"
    vllm_deployment: str           # e.g. "vllm-inference"
    vllm_configmap: str            # ConfigMap holding model_id (env-backed)
    allowlist: frozenset           # deny-by-default HF orgs / exact repo ids
    ready_timeout_s: float = 900.0 # max wait for the model to load
    enabled: bool = True           # kill-switch


def is_repo_allowed(allowlist, repo_id: str) -> bool:
    """Deny-by-default: empty ⇒ deny all; '*' ⇒ any; else exact repo or its org."""
    if not allowlist:
        return False
    if "*" in allowlist:
        return True
    if repo_id in allowlist:
        return True
    return repo_id.split("/", 1)[0] in allowlist


class Reconciler:
    """One-install-at-a-time reconciler (a global concurrency cap of 1 by design:
    a single shared GPU nodegroup + single vLLM release can serve one model)."""

    def __init__(self, *, cp_client, eks_client, k8s_client, config: InstallerConfig,
                 sleep=time.sleep):
        self.cp = cp_client
        self.eks = eks_client
        self.k8s = k8s_client
        self.cfg = config
        self._sleep = sleep

    def run_once(self) -> bool:
        """Process at most one pending request. Returns True if it did work."""
        if not self.cfg.enabled:
            return False
        pending = self.cp.list_pending()
        if not pending:
            return False
        req = pending[0]                       # FIFO; one at a time
        rid, repo = req["id"], req["hf_repo_id"]

        # Defense-in-depth: re-validate shape before it reaches the ConfigMap/args
        # (the control plane validated at creation; never trust a stored value blindly).
        if not isinstance(repo, str) or not _HF_REPO_RE.match(repo):
            logger.warning("install %s denied: repo id failed format re-check", rid)
            self.cp.set_status(rid, "failed", error_class=ERR_INVALID)
            return True

        if not is_repo_allowed(self.cfg.allowlist, repo):
            logger.warning("install %s denied: repo not on installer allow-list", rid)
            self.cp.set_status(rid, "failed", error_class=ERR_NOT_ALLOWED)
            return True

        logger.info("install %s starting for repo (allow-listed)", rid)
        self.cp.set_status(rid, "installing")
        try:
            self.eks.scale_nodegroup(self.cfg.cluster, self.cfg.nodegroup, desired=1)
        except Exception:
            logger.exception("install %s: GPU scale failed", rid)
            self.cp.set_status(rid, "failed", error_class=ERR_SCALE)
            return True

        # Capture the currently-served model so a failed load can be rolled back
        # (the vLLM release is shared — never leave it pointing at a broken model).
        try:
            previous = self.k8s.get_model(self.cfg.vllm_namespace, self.cfg.vllm_configmap)
        except Exception:
            previous = ""

        try:
            self.k8s.set_model(
                self.cfg.vllm_namespace, self.cfg.vllm_configmap,
                self.cfg.vllm_deployment, repo,
            )
        except Exception:
            logger.exception("install %s: vLLM apply failed", rid)
            self.cp.set_status(rid, "failed", error_class=ERR_APPLY)
            return True

        try:
            ready = self.k8s.wait_ready(
                self.cfg.vllm_namespace, self.cfg.vllm_deployment,
                timeout_s=self.cfg.ready_timeout_s, sleep=self._sleep,
            )
        except Exception:
            logger.exception("install %s: readiness wait errored", rid)
            self._rollback(previous, repo, rid)
            self.cp.set_status(rid, "failed", error_class=ERR_UNKNOWN)
            return True
        if not ready:
            self._rollback(previous, repo, rid)
            self.cp.set_status(rid, "failed", error_class=ERR_TIMEOUT)
            return True
        self.cp.set_status(rid, "applied")
        logger.info("install %s applied — model is serving", rid)
        return True

    def _rollback(self, previous: str, attempted: str, rid: str) -> None:
        """Restore the previously-served model after a failed load, so shared
        inference is not left broken. Best-effort — a rollback error is logged,
        never raised (the request is failed regardless)."""
        if not previous or previous == attempted:
            return
        try:
            self.k8s.set_model(self.cfg.vllm_namespace, self.cfg.vllm_configmap,
                               self.cfg.vllm_deployment, previous)
            logger.warning("install %s failed; rolled vLLM back to the previous model", rid)
        except Exception:
            logger.exception("install %s: rollback also failed", rid)

    def run_forever(self, poll_interval_s: float = 15.0) -> None:  # pragma: no cover
        logger.info("model-installer reconciler started (nodegroup=%s, vllm=%s/%s)",
                    self.cfg.nodegroup, self.cfg.vllm_namespace, self.cfg.vllm_deployment)
        while True:
            try:
                did = self.run_once()
            except Exception:
                logger.exception("reconcile loop error")
                did = False
            self._sleep(1.0 if did else poll_interval_s)
