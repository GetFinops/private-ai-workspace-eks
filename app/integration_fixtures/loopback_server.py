"""A fake in-cluster calendar provider for M13 dev validation.

Stdlib ``http.server`` only. It stands in for a real third-party calendar so the
shared harness can be exercised end-to-end in dev WITHOUT touching a real
provider or real credentials. It checks a bearer token (proving the per-tenant
credential path works) and serves a tiny synthetic event list.

Run in-cluster (dev only):
    LOOPBACK_TOKEN=<token> PORT=8099 python3 -m app.integration_fixtures.loopback_server

Never deploy this outside dev. It is not part of the default build.
"""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer

_DEFAULT_PORT = 8099
_DEFAULT_TOKEN = "fixture-token"  # noqa: S105 - dev fixture default, overridden by env


class _Handler(BaseHTTPRequestHandler):
    # expected_token is injected onto the server instance by make_server().
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server contract
        if self.path == "/healthz":
            self._send(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/calendar/events":
            expected = getattr(self.server, "expected_token", _DEFAULT_TOKEN)
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {expected}":
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            self._send(HTTPStatus.OK, {"events": [
                {"id": "evt-1", "title": "Standup"},
                {"id": "evt-2", "title": "Review"},
            ]})
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def log_message(self, *args) -> None:  # noqa: D401 - silence access logs (no content leakage)
        return


def make_server(host: str, port: int, token: str) -> HTTPServer:
    """Build (but do not start) the fixture server bound to ``host:port``."""
    server = HTTPServer((host, port), _Handler)
    server.expected_token = token  # type: ignore[attr-defined]
    return server


def main() -> None:  # pragma: no cover - process entrypoint
    port = int(os.environ.get("PORT", str(_DEFAULT_PORT)))
    token = os.environ.get("LOOPBACK_TOKEN", _DEFAULT_TOKEN)
    server = make_server("0.0.0.0", port, token)
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
