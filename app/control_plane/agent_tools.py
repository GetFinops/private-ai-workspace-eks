"""Agent tool framework (M11) — allow-listed, sandboxed, audited tool execution.

Tools run OUT OF PROCESS in app/sandbox/runner.py with a scrubbed environment,
RLIMIT CPU/memory/file-size caps, a scoped temp cwd, and a wall-clock timeout
with SIGKILL — never in-process with the control plane. See
docs/m11-sandbox-design.md (the reviewed sandbox design).

Security model (deny by default):
  - A tool runs only if it is in the caller's tenant allow-list. The tenant is
    derived from the verified token, never the request body. Authorization is
    re-checked on every call.
  - AGENT_TOOLS_ENABLED is an operator kill-switch (default off): when off the
    invoke endpoint returns 503 and spawns nothing.
  - Per-tenant rate limit + global concurrency cap, enforced before spawn.
  - Every call (allowed or denied) is audit-logged with the M5 content policy:
    tenant, user, tool, sanitised argument SHAPE (key names + value type/size,
    never values), result class, latency, and sandbox exit code only.

Arbitrary code execution, network-egress tools, filesystem-writing tools, and
cloud-credential access are excluded by default and require a stronger,
separately-reviewed sandbox.
"""
from __future__ import annotations

import json
import logging
import os
import resource
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from typing import Protocol, runtime_checkable

from app.control_plane.notifications import (
    ALLOWED_EVENT_CLASSES,
    NotificationEvent,
    NotificationStore,
    _extract_tenant_id,
    _now_utc,
    _verify_and_extract,
)
from app.control_plane.token_verifier import TokenVerifier
from app.sandbox.tools import TOOLS, tool_executor

logger = logging.getLogger(__name__)

# ── Limits (sandbox + request) ────────────────────────────────────────────────

_DEFAULT_CPU_SECONDS = 5         # RLIMIT_CPU (hard)
_DEFAULT_MEM_BYTES = 512 * 1024 * 1024  # RLIMIT_AS — generous enough for CPython
_DEFAULT_TIMEOUT_SECONDS = 10.0  # wall clock
_MAX_ARGS_BYTES = 200_000        # request argument size cap

_EVENT_COMPLETED = "agent_task_completed"
_EVENT_FAILED = "agent_task_failed"


# ── Result classes ────────────────────────────────────────────────────────────

# success | unknown_tool | tool_error | tool_timeout
@dataclass(frozen=True)
class SandboxOutcome:
    result_class: str
    result: dict | None = None
    exit_code: int | None = None


# ── Out-of-process sandbox executor ───────────────────────────────────────────


class SandboxExecutor:
    """Runs a tool in a separate, resource-limited, credential-free process."""

    def __init__(
        self,
        *,
        runner_cmd: list[str] | None = None,
        cpu_seconds: int = _DEFAULT_CPU_SECONDS,
        mem_bytes: int = _DEFAULT_MEM_BYTES,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._runner_cmd = runner_cmd or [sys.executable, "-m", "app.sandbox.runner"]
        self._cpu = cpu_seconds
        self._mem = mem_bytes
        self._timeout = timeout_seconds

    def _child_env(self) -> dict[str, str]:
        # Scrubbed: no AWS_*, DATABASE_URL, OIDC, or HF secrets reach the tool.
        # Keep only what Python needs to import the runner module.
        return {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": os.environ.get("PYTHONPATH", os.getcwd()),
            "PYTHONHASHSEED": "0",
            # RLIMIT_FSIZE=0 forbids writes; don't let Python try to write .pyc.
            "PYTHONDONTWRITEBYTECODE": "1",
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
        }

    def _preexec(self) -> None:  # pragma: no cover - runs in the child process
        # Hard resource caps; no file writes; small fd budget.
        resource.setrlimit(resource.RLIMIT_CPU, (self._cpu, self._cpu))
        resource.setrlimit(resource.RLIMIT_AS, (self._mem, self._mem))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

    def execute(self, tool: str, arguments: dict) -> SandboxOutcome:
        payload = json.dumps({"tool": tool, "arguments": arguments}).encode("utf-8")
        try:
            with tempfile.TemporaryDirectory(prefix="tool-") as tmp:
                proc = subprocess.Popen(
                    self._runner_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self._child_env(),
                    cwd=tmp,
                    preexec_fn=self._preexec,
                    start_new_session=True,  # own process group, for killpg
                )
                try:
                    out, _err = proc.communicate(input=payload, timeout=self._timeout)
                except subprocess.TimeoutExpired:
                    self._kill(proc)
                    return SandboxOutcome("tool_timeout", exit_code=None)

                if proc.returncode != 0:
                    # Non-zero: CPU/mem kill, crash, or rlimit violation.
                    return SandboxOutcome("tool_error", exit_code=proc.returncode)

                try:
                    data = json.loads(out or b"{}")
                except (ValueError, json.JSONDecodeError):
                    return SandboxOutcome("tool_error", exit_code=proc.returncode)

                if not isinstance(data, dict) or not data.get("ok"):
                    err = data.get("error") if isinstance(data, dict) else None
                    rc = "unknown_tool" if err == "unknown_tool" else "tool_error"
                    return SandboxOutcome(rc, exit_code=proc.returncode)

                return SandboxOutcome("success", result=data.get("result"), exit_code=0)
        except Exception:  # noqa: BLE001 - a spawn failure must not crash the request
            return SandboxOutcome("tool_error")

    @staticmethod
    def _kill(proc: "subprocess.Popen") -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # pragma: no cover
            try:
                proc.kill()
            except ProcessLookupError:
                pass  # process already exited — nothing to kill
        try:
            proc.communicate(timeout=2)
        except Exception:  # noqa: BLE001
            pass


# ── Per-tenant allow-list (deny by default) ───────────────────────────────────


def parse_allowlist(raw: str | None) -> dict[str, frozenset[str]]:
    """Parse AGENT_TOOLS_ALLOWLIST JSON: {"<tenant>": ["tool", ...]}."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        logger.warning("AGENT_TOOLS_ALLOWLIST is not valid JSON — treating as empty (deny all).")
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(tenant): frozenset(str(t) for t in tools)
        for tenant, tools in data.items()
        if isinstance(tools, list)
    }


def is_allowed(allowlist: dict[str, frozenset[str]], tenant: str, tool: str) -> bool:
    return tool in allowlist.get(tenant, frozenset())


# ── Rate + concurrency limits ─────────────────────────────────────────────────


class RateLimiter:
    """Per-tenant fixed-window rate limit + global concurrency cap."""

    def __init__(self, *, per_minute: int = 30, max_concurrency: int = 4) -> None:
        self._per_minute = per_minute
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}  # tenant -> (window, count)
        self._sema = threading.BoundedSemaphore(max(1, max_concurrency))

    def try_acquire(self, tenant: str, *, now: int) -> bool:
        with self._lock:
            window = now // 60
            w, count = self._windows.get(tenant, (window, 0))
            if w != window:
                w, count = window, 0
            if count >= self._per_minute:
                return False
            self._windows[tenant] = (w, count + 1)
        return self._sema.acquire(blocking=False)

    def release(self) -> None:
        try:
            self._sema.release()
        except ValueError:  # pragma: no cover - released more than acquired
            pass


# ── Audit logging (M5 content policy: shape only, never content) ──────────────


def _arg_shape(arguments: dict) -> dict:
    shape = {}
    for key, value in arguments.items():
        size = len(value) if hasattr(value, "__len__") else None
        shape[str(key)] = {"type": type(value).__name__, "size": size}
    return shape


def _audit(
    *,
    tenant: str,
    user: str,
    tool: str,
    arguments: dict,
    decision: str,
    result_class: str | None = None,
    latency_ms: int | None = None,
    exit_code: int | None = None,
) -> None:
    logger.info(
        "tool_invocation",
        extra={
            "audit": {
                "event": "tool_invocation",
                "tenant_id": tenant,
                "user_id": user,
                "tool": tool,
                "decision": decision,  # allowed | denied | disabled | rate_limited
                "arg_shape": _arg_shape(arguments),
                "result_class": result_class,
                "latency_ms": latency_ms,
                "sandbox_exit_code": exit_code,
            }
        },
    )


# ── Store protocol (for symmetry / future persistence) ────────────────────────


@runtime_checkable
class ToolRegistry(Protocol):  # pragma: no cover - structural only
    def __contains__(self, name: str) -> bool: ...


def _validate_arguments(spec: dict, arguments: dict) -> str | None:
    """Minimal declarative validation; returns an error string or None."""
    schema = spec.get("schema", {})
    for name, rule in schema.items():
        required = rule.get("required", False)
        if name not in arguments:
            if required:
                return f"'{name}' is required."
            continue
        value = arguments[name]
        expected = rule.get("type")
        if expected == "string" and not isinstance(value, str):
            return f"'{name}' must be a string."
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            return f"'{name}' must be an integer."
        max_len = rule.get("max_len")
        if max_len is not None and hasattr(value, "__len__") and len(value) > max_len:
            return f"'{name}' exceeds the maximum length of {max_len}."
    return None


# ── Pure handler ──────────────────────────────────────────────────────────────


def build_tool_invoke_response(
    *,
    authorization: str | None,
    body: bytes,
    token_verifier: TokenVerifier | None,
    enabled: bool,
    allowlist: dict[str, frozenset[str]],
    executor: SandboxExecutor,
    rate_limiter: RateLimiter,
    notification_store: NotificationStore | None = None,
    job_executor=None,
) -> tuple[int, dict]:
    """Handle POST /v1/agent/tools/invoke.

    Body: {"tool": "<name>", "arguments": {...}}. The tool runs only if the
    caller's tenant allow-lists it; execution is sandboxed and audited. Tools
    flagged executor="job" run via the tool-runner dispatcher (job_executor);
    all others run in the in-process subprocess sandbox. Authorization, kill-
    switch, rate limit, and audit are identical for both backends.
    """
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err

    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]

    # Kill-switch — operator cluster-wide off switch. Nothing is spawned.
    if not enabled:
        return HTTPStatus.SERVICE_UNAVAILABLE, {
            "error": "tools_disabled",
            "detail": "Tool execution is disabled on this instance.",
            "status": "degraded",
        }

    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "Body is not valid JSON."}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "Body must be a JSON object."}

    tool = data.get("tool")
    if not isinstance(tool, str) or not tool:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'tool' is required."}
    arguments = data.get("arguments", {})
    if not isinstance(arguments, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'arguments' must be an object."}
    if len(json.dumps(arguments)) > _MAX_ARGS_BYTES:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
            "error": "payload_too_large",
            "detail": f"arguments exceed {_MAX_ARGS_BYTES} bytes.",
        }

    # Deny by default + tool-selection injection defence: a tool not in the
    # tenant's allow-list (or unknown) is rejected before any spawn, and the
    # attempt is audit-logged. Identical 403 for "not allowed" and "unknown" so
    # the response does not reveal which tools exist.
    if not is_allowed(allowlist, tenant_id, tool) or tool not in TOOLS:
        _audit(tenant=tenant_id, user=user_id, tool=tool, arguments=arguments, decision="denied")
        return HTTPStatus.FORBIDDEN, {
            "error": "tool_not_allowed",
            "detail": "This tool is not permitted for your organisation.",
        }

    spec = TOOLS[tool]
    arg_err = _validate_arguments(spec, arguments)
    if arg_err is not None:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": arg_err}

    now = int(time.time())
    if not rate_limiter.try_acquire(tenant_id, now=now):
        _audit(tenant=tenant_id, user=user_id, tool=tool, arguments=arguments, decision="rate_limited")
        return HTTPStatus.TOO_MANY_REQUESTS, {
            "error": "rate_limited",
            "detail": "Tool execution rate or concurrency limit exceeded; try again shortly.",
        }

    started = time.monotonic()
    run_id = str(uuid.uuid4())
    try:
        if tool_executor(tool) == "job":
            outcome = (
                job_executor.execute(tool, arguments, tenant_id=tenant_id, run_id=run_id)
                if job_executor is not None
                else SandboxOutcome("tool_error")
            )
        else:
            outcome = executor.execute(tool, arguments)
    finally:
        rate_limiter.release()
    latency_ms = int((time.monotonic() - started) * 1000)

    _audit(
        tenant=tenant_id,
        user=user_id,
        tool=tool,
        arguments=arguments,
        decision="allowed",
        result_class=outcome.result_class,
        latency_ms=latency_ms,
        exit_code=outcome.exit_code,
    )

    # Producer event into the M9 feed (best-effort; never breaks the response).
    if notification_store is not None:
        event_class = _EVENT_COMPLETED if outcome.result_class == "success" else _EVENT_FAILED
        if event_class in ALLOWED_EVENT_CLASSES:
            try:
                notification_store.publish(
                    NotificationEvent(
                        id=str(uuid.uuid4()),
                        tenant_id=tenant_id,
                        user_id=user_id,
                        event_class=event_class,
                        resource_id=tool,
                        created_at=_now_utc(),
                    )
                )
            except Exception:  # pragma: no cover - notification is best-effort
                pass

    if outcome.result_class == "success":
        return HTTPStatus.OK, {
            "tool": tool,
            "result": outcome.result,
            "result_class": "success",
            "latency_ms": latency_ms,
        }
    if outcome.result_class == "tool_timeout":
        return HTTPStatus.GATEWAY_TIMEOUT, {
            "error": "tool_timeout",
            "detail": "The tool exceeded its time budget and was terminated.",
            "status": "degraded",
        }
    # tool_error / unknown_tool (unknown already filtered above)
    return HTTPStatus.BAD_GATEWAY, {
        "error": "tool_error",
        "detail": "The tool failed to execute.",
        "status": "degraded",
    }
