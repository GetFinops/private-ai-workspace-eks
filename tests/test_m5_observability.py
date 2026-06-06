"""M5 observability tests.

All tests are stdlib-only and run without a live cluster or network access.

Covers:
  1. Prometheus metrics module — counter/histogram presence, path sanitiser.
  2. Structured logging — JSON formatter, request context ContextVars.
  3. Tracing module — no-op mode when OTel is absent.
  4. /metrics endpoint via build_response.
  5. ServiceMonitor Helm template — presence and key fields.
  6. Observability chart — dashboards ConfigMap template, chart structure.
  7. cluster-addons chart — dcgm-exporter dependency present.
  8. deploy.yml — deploy_observability input and step present.
  9. Content-policy assertions — no user content in labels or log records.
"""

from __future__ import annotations

import json
import logging
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    import prometheus_client  # noqa: F401
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False

_SKIP_PROM = "prometheus-client not installed — run: pip install -e ."


# ──────────────────────────────────────────────────────────────────────────────
# 1. Prometheus metrics module
# ──────────────────────────────────────────────────────────────────────────────


class TestMetricsModule(unittest.TestCase):
    @unittest.skipUnless(HAS_PROMETHEUS, _SKIP_PROM)
    def test_http_requests_total_is_counter(self) -> None:
        from app.control_plane.metrics import HTTP_REQUESTS_TOTAL
        from prometheus_client import Counter  # type: ignore[import]
        self.assertIsInstance(HTTP_REQUESTS_TOTAL, Counter)

    @unittest.skipUnless(HAS_PROMETHEUS, _SKIP_PROM)
    def test_http_request_duration_is_histogram(self) -> None:
        from app.control_plane.metrics import HTTP_REQUEST_DURATION_SECONDS
        from prometheus_client import Histogram  # type: ignore[import]
        self.assertIsInstance(HTTP_REQUEST_DURATION_SECONDS, Histogram)

    @unittest.skipUnless(HAS_PROMETHEUS, _SKIP_PROM)
    def test_inference_requests_total_is_counter(self) -> None:
        from app.control_plane.metrics import INFERENCE_REQUESTS_TOTAL
        from prometheus_client import Counter  # type: ignore[import]
        self.assertIsInstance(INFERENCE_REQUESTS_TOTAL, Counter)

    @unittest.skipUnless(HAS_PROMETHEUS, _SKIP_PROM)
    def test_auth_failures_total_is_counter(self) -> None:
        from app.control_plane.metrics import AUTH_FAILURES_TOTAL
        from prometheus_client import Counter  # type: ignore[import]
        self.assertIsInstance(AUTH_FAILURES_TOTAL, Counter)

    def test_public_symbols_importable_without_prometheus(self) -> None:
        """All public metric symbols import cleanly even when prometheus_client
        is not installed (CI runs without pip install per AGENTS.md)."""
        from app.control_plane import metrics
        for name in [
            "HTTP_REQUESTS_TOTAL", "HTTP_REQUEST_ERRORS_TOTAL",
            "HTTP_REQUEST_DURATION_SECONDS", "HTTP_REQUESTS_IN_FLIGHT",
            "AUTH_FAILURES_TOTAL", "INFERENCE_REQUESTS_TOTAL",
            "INFERENCE_LATENCY_SECONDS", "DB_OPERATION_DURATION_SECONDS",
        ]:
            self.assertTrue(hasattr(metrics, name), f"missing symbol: {name}")

    def test_noop_metric_inc_observe_do_not_raise(self) -> None:
        """Counter/Histogram operations must be safe even with no-op stubs."""
        from app.control_plane.metrics import (
            HTTP_REQUESTS_TOTAL, HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_IN_FLIGHT,
        )
        HTTP_REQUESTS_TOTAL.labels(method="GET", path="/healthz", status_code="200").inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(method="GET", path="/healthz").observe(0.001)
        HTTP_REQUESTS_IN_FLIGHT.inc()
        HTTP_REQUESTS_IN_FLIGHT.dec()

    def test_sanitise_known_path(self) -> None:
        from app.control_plane.metrics import sanitise_path
        self.assertEqual(sanitise_path("/healthz"), "/healthz")
        self.assertEqual(sanitise_path("/v1/chat/completions"), "/v1/chat/completions")
        self.assertEqual(sanitise_path("/metrics"), "/metrics")

    def test_sanitise_unknown_path_collapses(self) -> None:
        from app.control_plane.metrics import sanitise_path
        self.assertEqual(sanitise_path("/api/../../etc/passwd"), "/unknown")
        self.assertEqual(sanitise_path("/v1/users/alice"), "/unknown")

    def test_sanitise_strips_query_string(self) -> None:
        from app.control_plane.metrics import sanitise_path
        self.assertEqual(sanitise_path("/healthz?token=secret"), "/healthz")

    def test_metrics_output_returns_bytes_and_content_type(self) -> None:
        from app.control_plane.metrics import metrics_output, CONTENT_TYPE_LATEST
        body, ct = metrics_output()
        self.assertIsInstance(body, bytes)
        self.assertIn("text/plain", ct)

    @unittest.skipUnless(HAS_PROMETHEUS, _SKIP_PROM)
    def test_metrics_body_contains_known_metric(self) -> None:
        from app.control_plane.metrics import metrics_output
        body, _ = metrics_output()
        self.assertIn(b"control_plane_http_requests_total", body)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Structured logging
# ──────────────────────────────────────────────────────────────────────────────


class TestStructuredLogging(unittest.TestCase):
    def setUp(self) -> None:
        from app.control_plane.logging_config import clear_request_context
        clear_request_context()

    def test_set_and_get_request_id(self) -> None:
        from app.control_plane.logging_config import set_request_context, get_request_id
        set_request_context("req-123")
        self.assertEqual(get_request_id(), "req-123")

    def test_set_and_get_correlation_id(self) -> None:
        from app.control_plane.logging_config import set_request_context, get_correlation_id
        set_request_context("req-456", "corr-789")
        self.assertEqual(get_correlation_id(), "corr-789")

    def test_clear_removes_ids(self) -> None:
        from app.control_plane.logging_config import (
            set_request_context, clear_request_context,
            get_request_id, get_correlation_id,
        )
        set_request_context("req-abc", "corr-def")
        clear_request_context()
        self.assertEqual(get_request_id(), "")
        self.assertEqual(get_correlation_id(), "")

    def test_json_formatter_produces_valid_json(self) -> None:
        import io
        from app.control_plane.logging_config import _JsonFormatter, set_request_context

        set_request_context("test-req-id", "test-corr-id")
        formatter = _JsonFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        self.assertEqual(data["level"], "INFO")
        self.assertEqual(data["message"], "hello world")
        self.assertEqual(data["request_id"], "test-req-id")
        self.assertEqual(data["correlation_id"], "test-corr-id")
        self.assertIn("timestamp", data)

    def test_json_formatter_no_user_content_in_record(self) -> None:
        """Content policy: exc_info must only emit type name, never the message."""
        from app.control_plane.logging_config import _JsonFormatter
        formatter = _JsonFormatter()
        try:
            raise ValueError("sensitive DSN: postgres://user:secret@host/db")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="db error", args=(), exc_info=exc_info,
        )
        output = formatter.format(record)
        data = json.loads(output)
        self.assertNotIn("sensitive DSN", output)
        self.assertNotIn("postgres://", output)
        self.assertEqual(data.get("exc_type"), "ValueError")

    def test_configure_logging_sets_json_handler(self) -> None:
        from app.control_plane.logging_config import configure_logging, _JsonFormatter
        configure_logging(log_level="DEBUG", json_format=True)
        root = logging.getLogger()
        self.assertTrue(any(isinstance(h.formatter, _JsonFormatter) for h in root.handlers))


# ──────────────────────────────────────────────────────────────────────────────
# 3. Tracing module (no-op path)
# ──────────────────────────────────────────────────────────────────────────────


class TestTracingModule(unittest.TestCase):
    def test_get_tracer_returns_something(self) -> None:
        from app.control_plane import tracing
        tracing._tracer = None  # reset for test isolation
        tracer = tracing.get_tracer()
        self.assertIsNotNone(tracer)

    def test_noop_tracer_context_manager(self) -> None:
        from app.control_plane.tracing import _NoopTracer
        tracer = _NoopTracer()
        with tracer.start_as_current_span("test-span") as span:
            span.set_attribute("key", "value")
            span.record_exception(Exception("test"))

    def test_get_current_trace_id_returns_string(self) -> None:
        from app.control_plane.tracing import get_current_trace_id
        result = get_current_trace_id()
        self.assertIsInstance(result, str)

    def test_inject_trace_headers_does_not_raise(self) -> None:
        from app.control_plane.tracing import inject_trace_headers
        headers: dict[str, str] = {"Content-Type": "application/json"}
        result = inject_trace_headers(headers)
        self.assertIsInstance(result, dict)


# ──────────────────────────────────────────────────────────────────────────────
# 4. /metrics endpoint via build_response
# ──────────────────────────────────────────────────────────────────────────────


class TestInstrumentationErrorPath(unittest.TestCase):
    """Defensive: a raising handler must still produce a response (no dropped conn)."""

    def test_unhandled_handler_exception_returns_500(self) -> None:
        from http import HTTPStatus
        from app.control_plane.server import ControlPlaneHandler, Response

        captured: list[tuple[int, bytes]] = []

        class FakeHandler(ControlPlaneHandler):
            def __init__(self) -> None:  # bypass BaseHTTPRequestHandler.__init__
                self.path = "/healthz"
                self.headers = {}  # type: ignore[assignment]

            def _write_json(self, status, payload, extra_headers=None):  # type: ignore[override]
                captured.append((int(status), repr(payload).encode()))

            def _write_raw(self, status, body, extra_headers=None):  # type: ignore[override]
                captured.append((int(status), body))

        h = FakeHandler()

        def boom() -> Response:
            raise RuntimeError("synthetic failure")

        h._instrument("GET", boom)
        self.assertTrue(captured, "handler must always write a response")
        status, _ = captured[0]
        self.assertEqual(status, int(HTTPStatus.INTERNAL_SERVER_ERROR))


class TestMetricsEndpoint(unittest.TestCase):
    def test_metrics_path_returns_200_with_raw_body(self) -> None:
        from app.control_plane.config import ControlPlaneConfig
        from app.control_plane.server import build_response
        from http import HTTPStatus

        config = ControlPlaneConfig()
        response = build_response("/metrics", config)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertIsNotNone(response.raw_body)
        assert response.raw_body is not None
        # When prometheus_client is installed, the body contains real metrics.
        # When it is not, the body is the documented no-op placeholder.
        if HAS_PROMETHEUS:
            self.assertIn(b"control_plane_http_requests_total", response.raw_body)
        else:
            self.assertIn(b"metrics disabled", response.raw_body)

    def test_metrics_response_has_prometheus_content_type(self) -> None:
        from app.control_plane.config import ControlPlaneConfig
        from app.control_plane.server import build_response

        config = ControlPlaneConfig()
        response = build_response("/metrics", config)
        assert response.headers is not None
        self.assertIn("text/plain", response.headers.get("Content-Type", ""))


# ──────────────────────────────────────────────────────────────────────────────
# 5. ServiceMonitor Helm template
# ──────────────────────────────────────────────────────────────────────────────


class TestServiceMonitorTemplate(unittest.TestCase):
    _template = ROOT / "deploy/helm/private-ai-workspace/templates/servicemonitor.yaml"

    def test_template_exists(self) -> None:
        self.assertTrue(self._template.exists())

    def test_enabled_gate_present(self) -> None:
        text = self._template.read_text()
        self.assertIn("{{- if .Values.metrics.serviceMonitor.enabled }}", text)

    def test_servicemonitor_kind(self) -> None:
        text = self._template.read_text()
        self.assertIn("kind: ServiceMonitor", text)

    def test_api_version(self) -> None:
        text = self._template.read_text()
        self.assertIn("monitoring.coreos.com/v1", text)

    def test_endpoint_path_reference(self) -> None:
        text = self._template.read_text()
        self.assertIn(".Values.metrics.path", text)

    def test_metrics_values_exist(self) -> None:
        values_text = (ROOT / "deploy/helm/private-ai-workspace/values.yaml").read_text()
        self.assertIn("serviceMonitor:", values_text)
        self.assertIn("metrics:", values_text)


# ──────────────────────────────────────────────────────────────────────────────
# 6. Observability chart structure and dashboards
# ──────────────────────────────────────────────────────────────────────────────


class TestObservabilityChart(unittest.TestCase):
    _chart = ROOT / "deploy/helm/observability"

    def test_chart_yaml_exists(self) -> None:
        self.assertTrue((self._chart / "Chart.yaml").exists())

    def test_chart_lock_exists(self) -> None:
        self.assertTrue((self._chart / "Chart.lock").exists())

    def test_values_yaml_exists(self) -> None:
        self.assertTrue((self._chart / "values.yaml").exists())

    def test_dashboards_template_exists(self) -> None:
        self.assertTrue((self._chart / "templates/dashboards.yaml").exists())

    def test_dcgm_dashboard_json_present(self) -> None:
        self.assertTrue((self._chart / "dashboards/dcgm-gpu.json").exists())

    def test_vllm_performance_dashboard_json_present(self) -> None:
        self.assertTrue((self._chart / "dashboards/vllm-performance.json").exists())

    def test_vllm_query_dashboard_json_present(self) -> None:
        self.assertTrue((self._chart / "dashboards/vllm-query.json").exists())

    def test_dashboards_template_has_grafana_label(self) -> None:
        text = (self._chart / "templates/dashboards.yaml").read_text()
        self.assertIn('grafana_dashboard: "1"', text)

    def test_sidecar_config_in_values(self) -> None:
        text = (self._chart / "values.yaml").read_text()
        self.assertIn("sidecar:", text)
        self.assertIn("searchNamespace: ALL", text)

    def test_dcgm_dashboard_is_valid_json(self) -> None:
        data = json.loads((self._chart / "dashboards/dcgm-gpu.json").read_text())
        self.assertIn("panels", data)

    def test_vllm_dashboard_is_valid_json(self) -> None:
        data = json.loads((self._chart / "dashboards/vllm-performance.json").read_text())
        self.assertIn("panels", data)

    def test_dcgm_dashboard_has_nvidia_copyright(self) -> None:
        text = (self._chart / "dashboards/dcgm-gpu.json").read_text()
        self.assertIn("NVIDIA CORPORATION", text)


# ──────────────────────────────────────────────────────────────────────────────
# 7. cluster-addons chart — dcgm-exporter dependency
# ──────────────────────────────────────────────────────────────────────────────


class TestClusterAddonsDCGM(unittest.TestCase):
    _chart_yaml = ROOT / "deploy/helm/cluster-addons/Chart.yaml"
    _values_yaml = ROOT / "deploy/helm/cluster-addons/values.yaml"

    def test_dcgm_exporter_in_chart_yaml(self) -> None:
        text = self._chart_yaml.read_text()
        self.assertIn("dcgm-exporter", text)

    def test_dcgm_exporter_condition_present(self) -> None:
        text = self._chart_yaml.read_text()
        self.assertIn("dcgmExporter.enabled", text)

    def test_dcgm_exporter_values_present(self) -> None:
        text = self._values_yaml.read_text()
        self.assertIn("dcgm-exporter:", text)
        self.assertIn("dcgmExporter:", text)

    def test_dcgm_exporter_tolerations_for_gpu(self) -> None:
        text = self._values_yaml.read_text()
        self.assertIn("nvidia.com/gpu", text)


# ──────────────────────────────────────────────────────────────────────────────
# 8. deploy.yml — observability input + step
# ──────────────────────────────────────────────────────────────────────────────


class TestDeployWorkflowObservability(unittest.TestCase):
    _workflow = ROOT / ".github/workflows/deploy.yml"

    def test_deploy_observability_input_present(self) -> None:
        text = self._workflow.read_text()
        self.assertIn("deploy_observability", text)

    def test_observability_helm_step_present(self) -> None:
        text = self._workflow.read_text()
        self.assertIn("deploy/helm/observability", text)

    def test_observability_step_is_conditional(self) -> None:
        text = self._workflow.read_text()
        self.assertIn("deploy_observability == 'true'", text)

    def test_monitoring_namespace_used(self) -> None:
        text = self._workflow.read_text()
        self.assertIn("--namespace monitoring", text)


# ──────────────────────────────────────────────────────────────────────────────
# 9. Content-policy: no user content in metric label names
# ──────────────────────────────────────────────────────────────────────────────


class TestContentPolicy(unittest.TestCase):
    @unittest.skipUnless(HAS_PROMETHEUS, _SKIP_PROM)
    def test_metric_label_names_safe(self) -> None:
        from app.control_plane.metrics import (
            HTTP_REQUESTS_TOTAL,
            HTTP_REQUEST_ERRORS_TOTAL,
            AUTH_FAILURES_TOTAL,
            INFERENCE_REQUESTS_TOTAL,
        )
        forbidden_labels = {"content", "prompt", "message", "token", "user", "body"}
        for metric in [HTTP_REQUESTS_TOTAL, HTTP_REQUEST_ERRORS_TOTAL,
                       AUTH_FAILURES_TOTAL, INFERENCE_REQUESTS_TOTAL]:
            label_names = set(metric._labelnames)  # type: ignore[attr-defined]
            overlap = label_names & forbidden_labels
            self.assertSetEqual(overlap, set(), f"Forbidden label names found: {overlap}")

    def test_logging_config_doc_mentions_content_policy(self) -> None:
        doc = (ROOT / "app/control_plane/logging_config.py").read_text()
        self.assertIn("Content policy", doc)

    def test_tracing_doc_mentions_content_policy(self) -> None:
        doc = (ROOT / "app/control_plane/tracing.py").read_text()
        self.assertIn("content policy", doc.lower())

    def test_observability_doc_has_strict_policy(self) -> None:
        doc = (ROOT / "docs/07-observability.md").read_text()
        self.assertIn("Strict content policy", doc)
        self.assertIn("MUST NOT", doc)


if __name__ == "__main__":
    unittest.main()
