"""Tool-runner dispatcher HTTP service (M11 Job-sandbox).

The ONLY component permitted to create Kubernetes Jobs for agent tools. It has
no user/auth surface of its own: it trusts the control plane (which has already
verified the end user and enforced the per-tenant allow-list) via a shared
bearer token, and is reachable only from the control-plane namespace
(NetworkPolicy). It re-validates that the requested tool is a registered
Job-executor tool, then runs it through the fixed locked-down Job template.

Endpoints:
  GET  /healthz   — liveness (no auth)
  POST /run       — execute one Job-backed tool (requires the shared token)

Body for /run: {"tenant_id", "run_id", "tool", "arguments"}. The response always
carries a result_class the control plane maps to a SandboxOutcome, identical to
the subprocess backend.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.dispatcher.jobrunner import JobConfig, JobOutcome, run_tool_job
from app.sandbox.tools import TOOLS, tool_executor

logger = logging.getLogger(__name__)

_MAX_BODY = 256 * 1024


@dataclass(frozen=True)
class DispatcherConfig:
    token: str | None
    image: str
    namespace: str = "agent-jobs"
    runner_service_account: str = "agent-tool-runner"
    cpu_limit: str = "500m"
    memory_limit: str = "256Mi"
    deadline_seconds: int = 30
    ttl_seconds: int = 120

    @classmethod
    def from_env(cls, env=None) -> "DispatcherConfig":
        e = env if env is not None else os.environ
        return cls(
            token=(e.get("DISPATCHER_TOKEN") or "").strip() or None,
            image=e.get("RUNNER_IMAGE", ""),
            namespace=e.get("JOBS_NAMESPACE", "agent-jobs"),
            runner_service_account=e.get("RUNNER_SERVICE_ACCOUNT", "agent-tool-runner"),
            cpu_limit=e.get("RUNNER_CPU_LIMIT", "500m"),
            memory_limit=e.get("RUNNER_MEMORY_LIMIT", "256Mi"),
            deadline_seconds=int(e.get("RUNNER_DEADLINE_SECONDS", "30") or "30"),
            ttl_seconds=int(e.get("RUNNER_TTL_SECONDS", "120") or "120"),
        )

    def job_config(self) -> JobConfig:
        return JobConfig(
            image=self.image,
            namespace=self.namespace,
            runner_service_account=self.runner_service_account,
            cpu_limit=self.cpu_limit,
            memory_limit=self.memory_limit,
            deadline_seconds=self.deadline_seconds,
            ttl_seconds=self.ttl_seconds,
        )


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def build_run_response(
    *,
    authorization: str | None,
    body: bytes,
    config: DispatcherConfig,
    k8s,
    runner=run_tool_job,
) -> tuple[int, dict]:
    """Pure handler for POST /run. Auth/validation errors are 401/400/503; an
    executed tool (success or failure) returns 200 with a result_class."""
    if not config.token:
        return HTTPStatus.SERVICE_UNAVAILABLE, {"error": "dispatcher_unconfigured"}
    presented = _bearer(authorization)
    if presented is None or not hmac.compare_digest(presented, config.token):
        return HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}

    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "invalid JSON"}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}

    tool = data.get("tool")
    arguments = data.get("arguments", {})
    run_id = data.get("run_id")
    tenant_id = data.get("tenant_id")
    if not isinstance(tool, str) or not tool:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'tool' required"}
    if not isinstance(arguments, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'arguments' must be object"}
    if not isinstance(run_id, str) or not run_id:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'run_id' required"}
    if not isinstance(tenant_id, str) or not tenant_id:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'tenant_id' required"}

    # Defense in depth: the dispatcher only ever runs registered Job-executor
    # tools, regardless of what the (trusted) caller asks for.
    if tool not in TOOLS or tool_executor(tool) != "job":
        return HTTPStatus.BAD_REQUEST, {"error": "not_a_job_tool"}

    outcome: JobOutcome = runner(
        k8s=k8s,
        cfg=config.job_config(),
        run_id=run_id,
        tenant_id=tenant_id,
        tool=tool,
        arguments=arguments,
    )
    return HTTPStatus.OK, {
        "result_class": outcome.result_class,
        "result": outcome.result,
        "exit_code": outcome.exit_code,
    }


class _Handler(BaseHTTPRequestHandler):
    config: DispatcherConfig = DispatcherConfig(token=None, image="")
    k8s = None

    def log_message(self, *args):  # silence default stderr logging
        return

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/healthz":
            self._send(HTTPStatus.OK, {"status": "ok"})
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/run":
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > _MAX_BODY:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too_large"})
            return
        body = self.rfile.read(length) if length > 0 else b""
        status, payload = build_run_response(
            authorization=self.headers.get("Authorization"),
            body=body,
            config=self.__class__.config,
            k8s=self.__class__.k8s,
        )
        self._send(status, payload)


def run_server(host: str = "0.0.0.0", port: int = 8090, config: DispatcherConfig | None = None) -> None:
    from app.dispatcher.k8sclient import K8sClient

    cfg = config or DispatcherConfig.from_env()
    _Handler.config = cfg
    _Handler.k8s = K8sClient.in_cluster(namespace=cfg.namespace)
    logger.info("Tool-runner dispatcher listening on %s:%d (jobs ns=%s)", host, port, cfg.namespace)
    ThreadingHTTPServer((host, port), _Handler).serve_forever()
