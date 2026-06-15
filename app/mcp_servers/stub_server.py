"""A pure stub MCP server (M12 validation case).

Speaks newline-delimited JSON-RPC 2.0 over stdio — the MCP stdio transport —
implementing the minimal method set the connection manager uses:
`initialize`, `notifications/initialized`, `tools/list`, `tools/call`. Exposes
one inert tool, `echo`. NO network, NO credentials, NO filesystem I/O — stdlib
only. It exits on stdin EOF.

This is deliberately the lowest-risk MCP server: there is nothing to
license-review or credential-scope. Any real MCP server is a separate adoption
decision (NOTICE) and runs under the same sandbox.
"""
from __future__ import annotations

import json
import sys

_PROTOCOL_VERSION = "2024-11-05"
_TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the provided message.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    }
]


def _result(rid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(request: dict) -> dict | None:
    method = request.get("method")
    rid = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return _result(rid, {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "stub-mcp", "version": "0.1.0"},
        })
    if method == "tools/list":
        return _result(rid, {"tools": _TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "echo":
            message = arguments.get("message")
            if not isinstance(message, str):
                return _error(rid, -32602, "'message' must be a string")
            return _result(rid, {"content": [{"type": "text", "text": message}], "isError": False})
        return _error(rid, -32601, "unknown tool")

    # Notifications (no id) — e.g. notifications/initialized — get no response.
    if rid is None:
        return None
    return _error(rid, -32601, "method not found")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            sys.stdout.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            sys.stdout.flush()
            continue
        response = handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
