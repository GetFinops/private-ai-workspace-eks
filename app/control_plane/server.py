"""Minimal HTTP surface for the control-plane skeleton."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.control_plane.config import ControlPlaneConfig


@dataclass(frozen=True)
class Response:
    status_code: int
    payload: dict[str, Any]


def build_response(path: str, config: ControlPlaneConfig) -> Response:
    """Build a JSON response for a control-plane route."""

    if path == "/healthz":
        return Response(
            HTTPStatus.OK,
            {
                "status": "ok",
                "service": config.service_name,
                "environment": config.environment,
            },
        )

    if path == "/readyz":
        checks = config.readiness_checks()
        ready = config.is_ready()
        return Response(
            HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "status": "ready" if ready else "not_ready",
                "checks": checks,
            },
        )

    if path == "/v1/inference/status":
        return Response(
            HTTPStatus.OK,
            {
                "status": "configured"
                if config.inference_base_url
                else "not_configured",
                "backend": "vllm-openai-compatible",
                "internal_only": True,
            },
        )

    return Response(
        HTTPStatus.NOT_FOUND,
        {
            "error": "not_found",
            "path": path,
        },
    )


class ControlPlaneHandler(BaseHTTPRequestHandler):
    """HTTP handler used by the development server."""

    config = ControlPlaneConfig.from_env()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        response = build_response(self.path, self.config)
        body = json.dumps(response.payload, sort_keys=True).encode("utf-8")

        self.send_response(response.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        if self.config.log_level != "DEBUG":
            return
        super().log_message(format, *args)


def run_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    config: ControlPlaneConfig | None = None,
) -> None:
    """Run the development HTTP server."""

    ControlPlaneHandler.config = config or ControlPlaneConfig.from_env()
    server = ThreadingHTTPServer((host, port), ControlPlaneHandler)
    server.serve_forever()
