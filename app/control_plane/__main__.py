"""Run the control-plane development server."""

from __future__ import annotations

from os import environ

from app.control_plane.config import ControlPlaneConfig
from app.control_plane.logging_config import configure_logging
from app.control_plane.server import run_server
from app.control_plane.tracing import init_tracing


def main() -> None:
    host = environ.get("HOST", "0.0.0.0")
    port = int(environ.get("PORT", "8080"))
    config = ControlPlaneConfig.from_env()

    # Structured logging — JSON in all environments by default (M5).
    configure_logging(
        log_level=config.log_level,
        json_format=(config.log_format != "text"),
    )

    # OTel tracing — no-op when OTEL_EXPORTER_OTLP_ENDPOINT is unset (M5).
    init_tracing(
        service_name=config.service_name,
        endpoint=config.otel_endpoint,
        environment=config.environment,
    )

    run_server(host=host, port=port, config=config)


if __name__ == "__main__":
    main()
