"""Entrypoint: `python3 -m app.dispatcher` runs the tool-runner dispatcher."""
from __future__ import annotations

import logging
import os

from app.dispatcher.server import run_server


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_server(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8090")),
    )


if __name__ == "__main__":
    main()
