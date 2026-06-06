"""Prometheus metrics for the control plane (M5).

Exposes the four golden signals for the control-plane HTTP layer and
additional inference-path counters.

Content policy (docs/07-observability.md):
  Label values MUST NOT contain prompt text, completion text, user content,
  tokens, or credentials.  Permitted labels: method, path (sanitised),
  status_code, error_class, environment.
"""

from __future__ import annotations

from prometheus_client import (  # type: ignore[import]
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Use the default registry so standard process/platform collectors are included.
# Do NOT import REGISTRY directly — reference via the module attribute so tests
# can swap it.

# ── Request rate and error rate ───────────────────────────────────────────────

HTTP_REQUESTS_TOTAL = Counter(
    "control_plane_http_requests_total",
    "Total HTTP requests received by the control plane.",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_ERRORS_TOTAL = Counter(
    "control_plane_http_request_errors_total",
    "Total HTTP requests that resulted in a 4xx or 5xx response.",
    ["method", "path", "status_code"],
)

# ── Latency (golden signal) ───────────────────────────────────────────────────

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "control_plane_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "path"],
    # Buckets tuned for LLM-backed workloads: short tail for healthz,
    # long tail for inference-forwarding requests.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# ── Saturation ────────────────────────────────────────────────────────────────

HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "control_plane_http_requests_in_flight",
    "Number of HTTP requests currently being processed.",
)

# ── Auth failures ─────────────────────────────────────────────────────────────

AUTH_FAILURES_TOTAL = Counter(
    "control_plane_auth_failures_total",
    "Total authentication failures (missing or invalid bearer token).",
    ["reason"],  # reason: missing_token | invalid_token | auth_not_configured
)

# ── Inference path ────────────────────────────────────────────────────────────

INFERENCE_REQUESTS_TOTAL = Counter(
    "control_plane_inference_requests_total",
    "Total inference requests forwarded to the vLLM backend.",
    ["status"],  # status: success | unavailable | routing_error | timeout | capacity
)

INFERENCE_LATENCY_SECONDS = Histogram(
    "control_plane_inference_latency_seconds",
    "Latency of inference requests forwarded to the vLLM backend.",
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# ── Database / dependency latency ─────────────────────────────────────────────

DB_OPERATION_DURATION_SECONDS = Histogram(
    "control_plane_db_operation_duration_seconds",
    "Latency of database operations.",
    ["operation"],  # operation: session_create | session_get | session_delete
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)


# ── Exposition helper ─────────────────────────────────────────────────────────

def metrics_output() -> tuple[bytes, str]:
    """Return (body_bytes, content_type) for the /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST


# ── Path sanitiser ────────────────────────────────────────────────────────────

_KNOWN_PATHS = frozenset({
    "/healthz",
    "/readyz",
    "/v1/inference/status",
    "/v1/chat/completions",
    "/metrics",
})


def sanitise_path(raw: str) -> str:
    """Return a safe label value for the request path.

    Unknown paths are collapsed to '/unknown' to prevent high-cardinality
    label explosion (e.g. from path-traversal probes or bots).
    """
    path = raw.split("?")[0]  # strip query string
    return path if path in _KNOWN_PATHS else "/unknown"
