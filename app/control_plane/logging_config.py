"""Structured JSON logging for the control plane (M5).

Configures the root logger to emit JSON lines so that log aggregators
(CloudWatch, Loki, etc.) can parse and query fields without regex.

Each log record includes:
  - timestamp   ISO-8601 UTC
  - level       DEBUG | INFO | WARNING | ERROR | CRITICAL
  - logger      dotted module name
  - message     the formatted log message
  - request_id  opaque per-request UUID (if set via set_request_context)
  - trace_id    W3C trace-id hex (if an active OTel span is present)

Content policy (docs/07-observability.md):
  NEVER include prompt text, completion text, tokens, session payloads,
  user content, or credentials in any log record.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from typing import Any

# ContextVar holds the current request-scoped opaque ID.
# Set via set_request_context(); cleared automatically at request boundary.
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def set_request_context(request_id: str, correlation_id: str = "") -> None:
    """Bind request/correlation IDs for the current async/thread context."""
    _request_id_var.set(request_id)
    _correlation_id_var.set(correlation_id)


def clear_request_context() -> None:
    _request_id_var.set("")
    _correlation_id_var.set("")


def get_request_id() -> str:
    return _request_id_var.get()


def get_correlation_id() -> str:
    return _correlation_id_var.get()


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record, strict content policy enforced."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        # Attempt to read the active OTel trace ID without importing OTel
        # when it is not installed (the field is simply omitted).
        trace_id = ""
        try:
            from opentelemetry import trace as _otel_trace  # type: ignore[import]
            span = _otel_trace.get_current_span()
            ctx = span.get_span_context()
            if ctx and ctx.is_valid:
                trace_id = format(ctx.trace_id, "032x")
        except Exception:
            pass

        entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.{ms}Z").format(
                ms=f"{int(record.msecs):03d}"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = _request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        correlation_id = _correlation_id_var.get()
        if correlation_id:
            entry["correlation_id"] = correlation_id
        if trace_id:
            entry["trace_id"] = trace_id
        if record.exc_info:
            # Log only the exception class name — never the full message
            # which may contain DSNs, tokens, or user data.
            exc_type = record.exc_info[0]
            entry["exc_type"] = exc_type.__name__ if exc_type else "unknown"
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(log_level: str = "INFO", json_format: bool = True) -> None:
    """Configure the root logger.

    Args:
        log_level: Standard Python level name (DEBUG, INFO, WARNING, …).
        json_format: When True, emit JSON lines.  When False, use a human-
            readable format (useful when running locally without a log
            aggregator).
    """
    handler = logging.StreamHandler(sys.stdout)
    if json_format:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
