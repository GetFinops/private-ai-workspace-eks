"""OpenTelemetry tracing initialisation for the control plane (M5).

Configures the OTel SDK with an OTLP gRPC exporter.  When no OTEL_EXPORTER_OTLP_ENDPOINT
is set the SDK falls back to a no-op tracer so the application starts without a
collector.

Span naming follows the HTTP semantic conventions (method + sanitised path).
Span attributes follow the content policy in docs/07-observability.md:
  - MUST NOT include prompt text, completion text, user content, or tokens.
  - Permitted: HTTP method, sanitised path, status code, error class, request ID.

Usage:
    from app.control_plane.tracing import init_tracing, get_tracer
    init_tracing(service_name="private-ai-workspace", endpoint="http://otel-collector:4317")
    tracer = get_tracer()
    with tracer.start_as_current_span("my-span") as span:
        span.set_attribute("http.status_code", 200)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Module-level tracer — replaced by init_tracing(); falls back to no-op.
_tracer = None


def init_tracing(
    service_name: str = "private-ai-workspace-control-plane",
    endpoint: str | None = None,
    environment: str = "development",
) -> None:
    """Initialise the OTel SDK.  Safe to call multiple times (idempotent after first call).

    Args:
        service_name: OTel resource service.name attribute.
        endpoint: OTLP gRPC endpoint (e.g. ``http://otel-collector:4317``).
            If None, reads OTEL_EXPORTER_OTLP_ENDPOINT from the environment.
            If still unset, uses a no-op tracer.
        environment: deployment.environment resource attribute.
    """
    global _tracer  # noqa: PLW0603
    if _tracer is not None:
        return

    try:
        from opentelemetry import trace  # type: ignore[import]
        from opentelemetry.sdk.resources import Resource  # type: ignore[import]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import]
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[import]
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import]
            OTLPSpanExporter,
        )

        resolved_endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")

        resource = Resource.create({
            "service.name": service_name,
            "deployment.environment": environment,
        })
        provider = TracerProvider(resource=resource)

        if resolved_endpoint:
            exporter = OTLPSpanExporter(endpoint=resolved_endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("OTel tracing initialised; exporting to %s", resolved_endpoint)
        else:
            logger.info(
                "OTEL_EXPORTER_OTLP_ENDPOINT not set — tracing initialised in no-op mode."
            )

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)

    except ImportError:
        logger.warning(
            "opentelemetry-sdk not installed — tracing is disabled. "
            "Install 'opentelemetry-sdk' and 'opentelemetry-exporter-otlp-proto-grpc'."
        )
        _tracer = _NoopTracer()


def get_tracer() -> object:
    """Return the module-level tracer (may be no-op if OTel is not installed)."""
    if _tracer is None:
        init_tracing()
    return _tracer


def get_current_trace_id() -> str:
    """Return the W3C trace-id hex string for the active span, or empty string."""
    try:
        from opentelemetry import trace  # type: ignore[import]
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.is_valid:
            return format(ctx.trace_id, "032x")
    except Exception:
        pass
    return ""


def inject_trace_headers(headers: dict[str, str]) -> dict[str, str]:
    """Inject W3C traceparent/tracestate into an outgoing headers dict.

    Returns the (mutated) dict for convenience.
    """
    try:
        from opentelemetry.propagators.composite import CompositeHTTPPropagator  # type: ignore[import]
        from opentelemetry.propagate import inject  # type: ignore[import]
        inject(headers)
    except Exception:
        pass
    return headers


class _NoopTracer:
    """Minimal no-op tracer used when OTel SDK is not available."""

    class _NoopSpan:
        def __enter__(self) -> "_NoopTracer._NoopSpan":
            return self
        def __exit__(self, *_: object) -> None:
            pass
        def set_attribute(self, *_: object) -> None:
            pass
        def record_exception(self, *_: object) -> None:
            pass
        def set_status(self, *_: object) -> None:
            pass

    def start_as_current_span(self, name: str, **kwargs: object) -> "_NoopTracer._NoopSpan":
        return self._NoopSpan()
