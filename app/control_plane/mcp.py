"""M12 MCP integration — sandboxed, per-tenant, deny-by-default MCP servers.

MCP servers run as OUT-OF-PROCESS subprocesses (the reviewed M11 sandbox
envelope: scrubbed env, RLIMIT cpu/mem/file-size, timeout with killpg) and speak
newline-delimited JSON-RPC 2.0 over stdio. A connection is established PER CALL —
a fresh process scoped to the caller's tenant — so there is no pooled
cross-tenant session. See docs/m12-mcp-design.md and NOTICE ("M12 MCP adoption
decision").

Security model (mirrors M11):
  - deny-by-default per-tenant allow-list of MCP servers (MCP_ALLOWLIST);
  - operator kill-switch (MCP_ENABLED);
  - per-tenant credential scoping: a server's secret is resolved per tenant and
    injected into ONLY that process's env, never a shared/ambient env var;
  - audit (shape only — tenant, server, tool, arg shape, result class, latency);
  - per-tenant rate/concurrency limit (shared with the M11 limiter).
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
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus

from app.control_plane.agent_tools import RateLimiter, _audit, is_allowed
from app.control_plane.notifications import (
    NotificationStore,
    _extract_tenant_id,
    _verify_and_extract,
)

logger = logging.getLogger(__name__)

_DEFAULT_CPU_SECONDS = 5
_DEFAULT_MEM_BYTES = 256 * 1024 * 1024
_DEFAULT_TIMEOUT = 10.0
_MAX_ARGS_BYTES = 100_000

# Registry of in-repo MCP servers. `command` is the argv to spawn; the server
# must speak JSON-RPC over stdio. `requires_secret`, if set, names a managed
# secret resolved PER TENANT and injected into the server's env. The stub needs
# none. Adding a real server is a separate adoption decision (NOTICE).
MCP_SERVERS: dict[str, dict] = {
    "stub": {
        "description": "Pure stub MCP server (echo); no network, no credentials.",
        "command": [sys.executable, "-m", "app.mcp_servers.stub_server"],
        "requires_secret": None,
    },
}


@dataclass(frozen=True)
class MCPOutcome:
    result_class: str            # success | unknown_tool | server_error | server_timeout
    result: dict | None = None


class _MCPTimeout(Exception):
    pass


class _MCPServerError(Exception):
    pass


def parse_mcp_allowlist(raw: str | None) -> dict[str, frozenset[str]]:
    """Parse MCP_ALLOWLIST JSON: {"<tenant>": ["<server>", ...]} — deny by default."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        logger.warning("MCP_ALLOWLIST is not valid JSON — treating as empty (deny all).")
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(tenant): frozenset(str(s) for s in servers)
        for tenant, servers in data.items()
        if isinstance(servers, list)
    }


class MCPExecutor:
    """Spawns an allow-listed MCP server in the sandbox and runs one exchange."""

    def __init__(
        self,
        *,
        servers: dict[str, dict] | None = None,
        cpu_seconds: int = _DEFAULT_CPU_SECONDS,
        mem_bytes: int = _DEFAULT_MEM_BYTES,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        secret_resolver=None,
    ) -> None:
        self._servers = servers if servers is not None else MCP_SERVERS
        self._cpu = cpu_seconds
        self._mem = mem_bytes
        self._timeout = timeout_seconds
        # secret_resolver(tenant, secret_key) -> dict[str, str] of env vars.
        # Default: no-op. A real server's creds are resolved per tenant from
        # managed secret storage and returned here; never a shared env var.
        self._secret_resolver = secret_resolver

    def _child_env(self, server_spec: dict, tenant_id: str) -> dict[str, str]:
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": os.environ.get("PYTHONPATH", os.getcwd()),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LC_ALL": "C.UTF-8",
            "LANG": "C.UTF-8",
        }
        secret_key = server_spec.get("requires_secret")
        if secret_key and self._secret_resolver is not None:
            scoped = self._secret_resolver(tenant_id, secret_key) or {}
            env.update({str(k): str(v) for k, v in scoped.items()})
        return env

    def _preexec(self) -> None:  # pragma: no cover - runs in child
        resource.setrlimit(resource.RLIMIT_CPU, (self._cpu, self._cpu))
        resource.setrlimit(resource.RLIMIT_AS, (self._mem, self._mem))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

    def _exchange(self, server_spec: dict, tenant_id: str, requests: list[dict]) -> list[dict]:
        payload = ("\n".join(json.dumps(r) for r in requests) + "\n").encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="mcp-") as tmp:
            proc = subprocess.Popen(
                server_spec["command"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=self._child_env(server_spec, tenant_id), cwd=tmp,
                preexec_fn=self._preexec, start_new_session=True,
            )
            try:
                out, _err = proc.communicate(input=payload, timeout=self._timeout)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                raise _MCPTimeout()
        if proc.returncode != 0:
            raise _MCPServerError()
        responses = []
        for line in (out or b"").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                responses.append(json.loads(line))
            except (ValueError, json.JSONDecodeError):
                continue
        return responses

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

    @staticmethod
    def _handshake() -> list[dict]:
        return [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                        "clientInfo": {"name": "private-ai-workspace", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ]

    @staticmethod
    def _find(responses: list[dict], rid: int) -> dict | None:
        for r in responses:
            if r.get("id") == rid:
                return r
        return None

    def list_tools(self, server: str, tenant_id: str) -> MCPOutcome:
        spec = self._servers.get(server)
        if spec is None:
            return MCPOutcome("unknown_tool")
        reqs = self._handshake() + [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        try:
            responses = self._exchange(spec, tenant_id, reqs)
        except _MCPTimeout:
            return MCPOutcome("server_timeout")
        except Exception:  # noqa: BLE001 - spawn/server failure must not crash the request
            return MCPOutcome("server_error")
        resp = self._find(responses, 2)
        if resp is None or "result" not in resp:
            return MCPOutcome("server_error")
        return MCPOutcome("success", result={"tools": resp["result"].get("tools", [])})

    def call_tool(self, server: str, tool: str, arguments: dict, tenant_id: str) -> MCPOutcome:
        spec = self._servers.get(server)
        if spec is None:
            return MCPOutcome("unknown_tool")
        reqs = self._handshake() + [{
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }]
        try:
            responses = self._exchange(spec, tenant_id, reqs)
        except _MCPTimeout:
            return MCPOutcome("server_timeout")
        except Exception:  # noqa: BLE001
            return MCPOutcome("server_error")
        resp = self._find(responses, 2)
        if resp is None:
            return MCPOutcome("server_error")
        if "error" in resp:
            code = resp["error"].get("code")
            return MCPOutcome("unknown_tool" if code == -32601 else "server_error")
        result = resp.get("result") or {}
        return MCPOutcome("success", result=result)


# ── HTTP handlers ─────────────────────────────────────────────────────────────


def _gate(authorization, token_verifier, enabled):
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return None, None, err
    tenant_id = _extract_tenant_id(claims)
    user_id = claims.subject
    if not enabled:
        return None, None, (HTTPStatus.SERVICE_UNAVAILABLE, {
            "error": "mcp_disabled",
            "detail": "MCP integrations are disabled on this instance.",
            "status": "degraded",
        })
    return tenant_id, user_id, None


def build_mcp_list_response(
    *, authorization, body, token_verifier, enabled, allowlist, executor,
) -> tuple[int, dict]:
    """POST /v1/mcp/tools/list — body {"server": "<name>"}."""
    tenant_id, user_id, err = _gate(authorization, token_verifier, enabled)
    if err is not None:
        return err
    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    server = data.get("server") if isinstance(data, dict) else None
    if not isinstance(server, str) or not server:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'server' is required."}
    if not is_allowed(allowlist, tenant_id, server) or server not in MCP_SERVERS:
        _audit(tenant=tenant_id, user=user_id, tool=f"mcp:{server}", arguments={}, decision="denied")
        return HTTPStatus.FORBIDDEN, {"error": "server_not_allowed"}
    outcome = executor.list_tools(server, tenant_id)
    if outcome.result_class != "success":
        return HTTPStatus.BAD_GATEWAY, {"error": "mcp_error", "status": "degraded"}
    return HTTPStatus.OK, {"server": server, "tools": outcome.result["tools"]}


def build_mcp_invoke_response(
    *, authorization, body, token_verifier, enabled, allowlist, executor,
    rate_limiter: RateLimiter, notification_store: NotificationStore | None = None,
) -> tuple[int, dict]:
    """POST /v1/mcp/invoke — body {"server", "tool", "arguments"}."""
    tenant_id, user_id, err = _gate(authorization, token_verifier, enabled)
    if err is not None:
        return err
    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    server = data.get("server")
    tool = data.get("tool")
    arguments = data.get("arguments", {})
    if not isinstance(server, str) or not server:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'server' is required."}
    if not isinstance(tool, str) or not tool:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'tool' is required."}
    if not isinstance(arguments, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'arguments' must be an object."}
    if len(json.dumps(arguments)) > _MAX_ARGS_BYTES:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "payload_too_large"}

    # Deny by default: the server must be allow-listed for this tenant (or it is
    # unknown). Rejected before any spawn, and audited.
    if not is_allowed(allowlist, tenant_id, server) or server not in MCP_SERVERS:
        _audit(tenant=tenant_id, user=user_id, tool=f"mcp:{server}/{tool}",
               arguments=arguments, decision="denied")
        return HTTPStatus.FORBIDDEN, {"error": "server_not_allowed"}

    now = int(time.time())
    if not rate_limiter.try_acquire(tenant_id, now=now):
        _audit(tenant=tenant_id, user=user_id, tool=f"mcp:{server}/{tool}",
               arguments=arguments, decision="rate_limited")
        return HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate_limited"}

    started = time.monotonic()
    try:
        outcome = executor.call_tool(server, tool, arguments, tenant_id)
    finally:
        rate_limiter.release()
    latency_ms = int((time.monotonic() - started) * 1000)
    _audit(tenant=tenant_id, user=user_id, tool=f"mcp:{server}/{tool}", arguments=arguments,
           decision="allowed", result_class=outcome.result_class, latency_ms=latency_ms)

    if notification_store is not None:
        event = "agent_task_completed" if outcome.result_class == "success" else "agent_task_failed"
        try:
            from app.control_plane.notifications import NotificationEvent, _now_utc, ALLOWED_EVENT_CLASSES
            if event in ALLOWED_EVENT_CLASSES:
                notification_store.publish(NotificationEvent(
                    id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id,
                    event_class=event, resource_id=f"mcp:{server}", created_at=_now_utc()))
        except Exception:  # pragma: no cover - best-effort
            pass

    if outcome.result_class == "success":
        return HTTPStatus.OK, {"server": server, "tool": tool, "result": outcome.result}
    if outcome.result_class == "unknown_tool":
        return HTTPStatus.NOT_FOUND, {"error": "unknown_tool"}
    if outcome.result_class == "server_timeout":
        return HTTPStatus.GATEWAY_TIMEOUT, {"error": "mcp_timeout", "status": "degraded"}
    return HTTPStatus.BAD_GATEWAY, {"error": "mcp_error", "status": "degraded"}
