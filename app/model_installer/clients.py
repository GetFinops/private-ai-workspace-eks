"""Clients for the model-installer reconciler.

- ControlPlaneClient: stdlib-only HTTP to the control plane's internal reconciler
  API (shared-token bearer). Pure/urllib so the reconciler needs no control-plane
  imports and this stays unit-testable.
- EksNodegroupClient / K8sVllmClient: the privileged infra actions, behind lazy
  boto3 / kubernetes imports so the reconciler's decision logic can be tested
  without those deps or a cluster. Each does exactly ONE narrow thing.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

logger = logging.getLogger("model_installer")


class ControlPlaneClient:
    """Talks to /v1/internal/model-installer/* with the shared reconciler token."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 15.0) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def list_pending(self) -> list:
        req = urllib.request.Request(
            f"{self._base}/v1/internal/model-installer/pending",
            headers=self._headers(), method="GET",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        return data.get("requests", [])

    def set_status(self, request_id: str, status: str, error_class: str = "") -> None:
        body = json.dumps({"status": status, "error_class": error_class}).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base}/v1/internal/model-installer/requests/{request_id}/status",
            data=body, headers=self._headers(), method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout):
                pass
        except urllib.error.HTTPError as exc:  # pragma: no cover - network
            logger.error("set_status %s -> %s failed: HTTP %s", request_id, status, exc.code)
            raise


class EksNodegroupClient:
    """Scales exactly one GPU managed node group (eks:UpdateNodegroupConfig)."""

    def __init__(self, region: str) -> None:
        self._region = region
        self._eks = None

    def _client(self):  # pragma: no cover - requires boto3 + AWS
        if self._eks is None:
            import boto3  # lazy: only the reconciler image ships boto3
            self._eks = boto3.client("eks", region_name=self._region)
        return self._eks

    def scale_nodegroup(self, cluster: str, nodegroup: str, *, desired: int,
                        wait_s: float = 600.0, sleep=time.sleep) -> None:  # pragma: no cover
        eks = self._client()
        ng = eks.describe_nodegroup(clusterName=cluster, nodegroupName=nodegroup)["nodegroup"]
        cur = ng["scalingConfig"]["desiredSize"]
        if cur >= desired and ng.get("status") == "ACTIVE":
            logger.info("nodegroup %s already at desired>=%s (ACTIVE)", nodegroup, desired)
            return
        # Keep min/max as configured; only raise the desired count.
        sc = ng["scalingConfig"]
        eks.update_nodegroup_config(
            clusterName=cluster, nodegroupName=nodegroup,
            scalingConfig={"minSize": sc["minSize"],
                           "maxSize": max(sc["maxSize"], desired),
                           "desiredSize": desired},
        )
        deadline = time.time() + wait_s
        while time.time() < deadline:
            st = eks.describe_nodegroup(clusterName=cluster, nodegroupName=nodegroup)["nodegroup"]["status"]
            if st == "ACTIVE":
                return
            if st in ("CREATE_FAILED", "DEGRADED"):
                raise RuntimeError(f"nodegroup {nodegroup} entered {st}")
            sleep(15)
        raise TimeoutError(f"nodegroup {nodegroup} not ACTIVE within {wait_s}s")


class K8sVllmClient:
    """Points the vLLM release at a model: patch ONE ConfigMap + restart ONE
    Deployment in the inference namespace, then wait for it to become ready.

    The vLLM chart reads --model from $(MODEL_ID), sourced from the ConfigMap, and
    serves under the stable alias 'default' — so the control plane's model id is
    unchanged and chat keeps working across a model swap.
    """

    def __init__(self) -> None:
        self._core = None
        self._apps = None

    def _load(self):  # pragma: no cover - requires kubernetes + cluster
        if self._core is None:
            from kubernetes import client, config  # lazy: reconciler image only
            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()
            self._core = client.CoreV1Api()
            self._apps = client.AppsV1Api()
        return self._core, self._apps

    def get_model(self, namespace: str, configmap: str) -> str:  # pragma: no cover
        """Current model_id in the ConfigMap (for rollback), or '' if absent."""
        core, _apps = self._load()
        cm = core.read_namespaced_config_map(configmap, namespace)
        return (cm.data or {}).get("model_id", "")

    def set_model(self, namespace: str, configmap: str, deployment: str,
                  repo_id: str) -> None:  # pragma: no cover - requires cluster
        core, apps = self._load()
        core.patch_namespaced_config_map(
            configmap, namespace, {"data": {"model_id": repo_id}},
        )
        # Trigger a rollout so vLLM re-reads MODEL_ID (restart-timestamp annotation).
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        apps.patch_namespaced_deployment(
            deployment, namespace,
            {"spec": {"template": {"metadata": {"annotations": {
                "modelinstaller.private-ai/restartedAt": stamp}}}}},
        )

    def wait_ready(self, namespace: str, deployment: str, *, timeout_s: float,
                   sleep=time.sleep) -> bool:  # pragma: no cover - requires cluster
        _core, apps = self._load()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            dep = apps.read_namespaced_deployment_status(deployment, namespace)
            spec = dep.spec.replicas or 0
            avail = (dep.status.available_replicas or 0)
            updated = (dep.status.updated_replicas or 0)
            if spec > 0 and avail >= spec and updated >= spec:
                return True
            sleep(10)
        return False
