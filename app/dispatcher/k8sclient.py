"""Minimal in-cluster Kubernetes API client (stdlib only).

Talks to the API server over HTTPS using the pod's mounted ServiceAccount token
and CA certificate — no `kubernetes` client dependency, keeping the dispatcher
standard-library-only. All operations are scoped to a single namespace. Only the
verbs the tool-runner needs are implemented: create/get/delete Job, list Pods,
read Pod log.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

_SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


class K8sError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class K8sClient:
    """Namespace-scoped client over the in-cluster API."""

    def __init__(
        self,
        *,
        namespace: str,
        api_server: str,
        token: str,
        ca_cert_path: str | None,
        timeout: float = 10.0,
    ) -> None:
        self._ns = namespace
        self._base = api_server.rstrip("/")
        self._token = token
        self._timeout = timeout
        if ca_cert_path:
            self._ctx = ssl.create_default_context(cafile=ca_cert_path)
        else:  # pragma: no cover - only for out-of-cluster/testing
            self._ctx = ssl.create_default_context()

    @classmethod
    def in_cluster(cls, *, namespace: str | None = None, timeout: float = 10.0) -> "K8sClient":
        import os

        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        with open(f"{_SA_DIR}/token", encoding="utf-8") as fh:
            token = fh.read().strip()
        ns = namespace
        if ns is None:
            with open(f"{_SA_DIR}/namespace", encoding="utf-8") as fh:
                ns = fh.read().strip()
        return cls(
            namespace=ns,
            api_server=f"https://{host}:{port}",
            token=token,
            ca_cert_path=f"{_SA_DIR}/ca.crt",
            timeout=timeout,
        )

    # ── low-level request ─────────────────────────────────────────────────────
    def _request(self, method: str, path: str, body: dict | None = None, *, raw: bool = False):
        url = self._base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout, context=self._ctx) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            raise K8sError(f"{method} {path} -> HTTP {exc.code}", status=exc.code) from exc
        except urllib.error.URLError as exc:
            raise K8sError(f"{method} {path} failed: {type(exc).__name__}") from exc
        if raw:
            return payload.decode("utf-8", "replace")
        return json.loads(payload or b"{}")

    # ── Jobs ──────────────────────────────────────────────────────────────────
    def create_job(self, manifest: dict) -> dict:
        return self._request(
            "POST", f"/apis/batch/v1/namespaces/{self._ns}/jobs", body=manifest
        )

    def get_job(self, name: str) -> dict:
        return self._request("GET", f"/apis/batch/v1/namespaces/{self._ns}/jobs/{name}")

    def delete_job(self, name: str) -> None:
        # Background propagation also deletes the Job's pods.
        self._request(
            "DELETE",
            f"/apis/batch/v1/namespaces/{self._ns}/jobs/{name}?propagationPolicy=Background",
        )

    # ── Pods ──────────────────────────────────────────────────────────────────
    def list_pods(self, label_selector: str) -> list[dict]:
        q = urllib.parse.urlencode({"labelSelector": label_selector})
        out = self._request("GET", f"/api/v1/namespaces/{self._ns}/pods?{q}")
        return out.get("items", [])

    def read_pod_log(self, pod_name: str, *, limit_bytes: int = 65536) -> str:
        q = urllib.parse.urlencode({"limitBytes": limit_bytes})
        return self._request(
            "GET", f"/api/v1/namespaces/{self._ns}/pods/{pod_name}/log?{q}", raw=True
        )
