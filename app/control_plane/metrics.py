"""Prometheus metrics for the control plane (M5).

Exposes the four golden signals for the control-plane HTTP layer and
additional inference-path counters.

Content policy (docs/07-observability.md):
  Label values MUST NOT contain prompt text, completion text, user content,
  tokens, or credentials.  Permitted labels: method, path (sanitised),
  status_code, error_class, environment.

Graceful degradation:
  When ``prometheus-client`` is not installed (e.g. the stdlib-only CI
  environment described in AGENTS.md), this module falls back to no-op stubs
  so that the rest of the application still imports cleanly.  All public
  symbols remain available; calls to ``.inc()`` / ``.observe()`` / ``.labels()``
  become no-ops and ``metrics_output()`` returns an empty Prometheus document.
"""

from __future__ import annotations

try:
    from prometheus_client import (  # type: ignore[import]
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in CI without M5 deps
    PROMETHEUS_AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _NoopMetric:
        """No-op stand-in for Counter / Gauge / Histogram when prometheus_client is absent."""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self._labelnames: tuple[str, ...] = tuple(_kwargs.get("labelnames", ()) or ())
            if len(_args) >= 3 and isinstance(_args[2], (list, tuple)):
                self._labelnames = tuple(_args[2])

        def labels(self, *_args: object, **_kwargs: object) -> "_NoopMetric":
            return self

        def inc(self, *_args: object, **_kwargs: object) -> None:
            return None

        def dec(self, *_args: object, **_kwargs: object) -> None:
            return None

        def observe(self, *_args: object, **_kwargs: object) -> None:
            return None

        def set(self, *_args: object, **_kwargs: object) -> None:
            return None

    Counter = Gauge = Histogram = _NoopMetric  # type: ignore[assignment,misc]

    def generate_latest() -> bytes:  # type: ignore[misc]
        return b"# prometheus_client not installed; metrics disabled\n"

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

# ── Retrieval & memory path (M10) ─────────────────────────────────────────────
# Labels are operation names only — never tenant/user ids or content (content
# policy + cardinality). Per-tenant index size is intentionally not a label;
# query it from the database instead.

RETRIEVAL_OPERATION_DURATION_SECONDS = Histogram(
    "control_plane_retrieval_operation_duration_seconds",
    "Latency of retrieval/memory operations (embedding + store).",
    ["operation"],  # index | query | memory_record | memory_recall | memory_list | memory_delete
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

RETRIEVAL_RESULTS_RETURNED = Histogram(
    "control_plane_retrieval_results_returned",
    "Number of results returned by a retrieval query or memory recall (recall proxy).",
    ["operation"],  # query | memory_recall
    buckets=(0, 1, 2, 3, 5, 10, 20),
)

DOCUMENT_CHUNKS_INDEXED_TOTAL = Counter(
    "control_plane_document_chunks_indexed_total",
    "Total document chunks indexed into the vector store (index growth).",
)

EMBEDDINGS_GENERATED_TOTAL = Counter(
    "control_plane_embeddings_generated_total",
    "Total texts embedded (embedding throughput).",
    ["status"],  # success | error
)

EMBEDDING_DURATION_SECONDS = Histogram(
    "control_plane_embedding_duration_seconds",
    "Latency of an embedding batch request.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
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
    "/v1/notifications",
    "/v1/retrieval/documents",
    "/v1/retrieval/query",
    "/v1/memory",
    "/v1/memory/recall",
    "/metrics",
})


def sanitise_path(raw: str) -> str:
    """Return a safe label value for the request path.

    Unknown paths are collapsed to '/unknown' to prevent high-cardinality
    label explosion (e.g. from path-traversal probes or bots). Routes that
    embed an id are collapsed to a templated form so the id never becomes a
    label value.
    """
    path = raw.split("?")[0]  # strip query string
    if path in _KNOWN_PATHS:
        return path
    if path.startswith("/v1/memory/"):
        return "/v1/memory/{id}"
    if path.startswith("/v1/notifications/") and path.endswith("/read"):
        return "/v1/notifications/{id}/read"
    return "/unknown"
