"""Run the control-plane development server."""

from __future__ import annotations

from os import environ

from app.control_plane.config import ControlPlaneConfig
from app.control_plane.server import run_server


def main() -> None:
    host = environ.get("HOST", "0.0.0.0")
    port = int(environ.get("PORT", "8080"))
    run_server(host=host, port=port, config=ControlPlaneConfig.from_env())


if __name__ == "__main__":
    main()
