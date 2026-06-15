"""Out-of-process sandbox runner.

Reads a single JSON request {"tool": str, "arguments": object}, executes the
named pure tool from app.sandbox.tools, and writes a single JSON result to
stdout. Imports only the standard library and app.sandbox.tools — never the
control plane, config, database, or AWS SDK.

The request is read from stdin by default. When the AGENT_TOOL_INPUT environment
variable is set it is used instead — this is the Kubernetes-Job path (M11
Job-sandbox), where the tool-runner dispatcher passes the payload via env
because Jobs have no convenient stdin. Either way the runner is the same pure
process; only the security envelope around it differs:
  - subprocess path: parent (agent_tools.py) sets scrubbed env, RLIMITs, scoped
    temp cwd, wall-clock timeout with SIGKILL;
  - Job path: the Job pod provides the envelope (unprivileged SA with no creds,
    read-only rootfs, default-deny NetworkPolicy, resource limits, deadline).
This script does no I/O beyond reading the request and writing stdout.
"""
from __future__ import annotations

import json
import os
import sys

from app.sandbox.tools import TOOLS


def run(payload: dict) -> dict:
    tool = payload.get("tool")
    spec = TOOLS.get(tool)
    if spec is None:
        return {"ok": False, "error": "unknown_tool"}
    args = payload.get("arguments")
    if not isinstance(args, dict):
        return {"ok": False, "error": "bad_arguments"}
    try:
        return {"ok": True, "result": spec["run"](args)}
    except Exception as exc:  # noqa: BLE001 - never leak details/content
        return {"ok": False, "error": "tool_exception", "detail": type(exc).__name__}


def _read_request() -> str:
    """Return the raw JSON request: AGENT_TOOL_INPUT (Job path) or stdin."""
    env_input = os.environ.get("AGENT_TOOL_INPUT")
    if env_input is not None:
        return env_input
    return sys.stdin.read()


def main() -> None:
    try:
        payload = json.loads(_read_request() or "{}")
    except (ValueError, json.JSONDecodeError):
        sys.stdout.write(json.dumps({"ok": False, "error": "bad_request"}))
        return
    if not isinstance(payload, dict):
        sys.stdout.write(json.dumps({"ok": False, "error": "bad_request"}))
        return
    sys.stdout.write(json.dumps(run(payload)))


if __name__ == "__main__":
    main()
