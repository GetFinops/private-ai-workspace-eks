"""Control-plane client for the tool-runner dispatcher (M11 Job-sandbox).

Job-backed tools are NOT executed by the control plane — it has no Kubernetes
privileges. Instead this client forwards the (already authorized, allow-listed)
tool call to the tool-runner dispatcher over HTTP with the shared bearer token.
The dispatcher runs the tool in an isolated Job and returns a result_class, which
this client maps back to the same SandboxOutcome the subprocess backend produces,
so callers handle both executors identically.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from app.control_plane.agent_tools import SandboxOutcome

logger = logging.getLogger(__name__)


class DispatcherJobExecutor:
    """Forwards Job-backed tool calls to the tool-runner dispatcher."""

    def __init__(self, *, base_url: str | None, token: str | None, timeout: float = 60.0) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._token = token or None
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._token)

    def execute(self, tool: str, arguments: dict, *, tenant_id: str, run_id: str) -> SandboxOutcome:
        if not self.configured:
            # Job backend not wired → the tool is unavailable, not a crash.
            return SandboxOutcome("tool_error")
        body = json.dumps(
            {"tenant_id": tenant_id, "run_id": run_id, "tool": tool, "arguments": arguments}
        ).encode("utf-8")
        req = urllib.request.Request(
            self._base_url + "/run",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            logger.warning("Job dispatcher returned HTTP %s", exc.code)
            return SandboxOutcome("tool_error")
        except Exception as exc:  # noqa: BLE001 - timeout/conn errors → clean failure
            logger.warning("Job dispatcher call failed: %s", type(exc).__name__)
            return SandboxOutcome("tool_timeout" if isinstance(exc, TimeoutError) else "tool_error")
        if not isinstance(data, dict):
            return SandboxOutcome("tool_error")
        return SandboxOutcome(
            data.get("result_class", "tool_error"),
            result=data.get("result"),
            exit_code=data.get("exit_code"),
        )
