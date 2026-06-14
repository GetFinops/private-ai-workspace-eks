"""Out-of-process sandbox runner.

Reads a single JSON request {"tool": str, "arguments": object} from stdin,
executes the named pure tool from app.sandbox.tools, and writes a single JSON
result to stdout. Imports only the standard library and app.sandbox.tools —
never the control plane, config, database, or AWS SDK.

The parent (app/control_plane/agent_tools.py) is responsible for the security
envelope before exec: scrubbed environment (no credentials), RLIMIT CPU/memory/
file-size caps, a scoped temp cwd, and a wall-clock timeout with SIGKILL. This
script does no I/O beyond stdin/stdout.
"""
from __future__ import annotations

import json
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


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, json.JSONDecodeError):
        sys.stdout.write(json.dumps({"ok": False, "error": "bad_request"}))
        return
    if not isinstance(payload, dict):
        sys.stdout.write(json.dumps({"ok": False, "error": "bad_request"}))
        return
    sys.stdout.write(json.dumps(run(payload)))


if __name__ == "__main__":
    main()
