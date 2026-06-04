from http import HTTPStatus
from unittest import TestCase

from app.control_plane.config import ControlPlaneConfig
from app.control_plane.server import build_response


class ControlPlaneRouteTests(TestCase):
    def test_health_endpoint_is_available_without_gpu_capacity(self) -> None:
        response = build_response("/healthz", ControlPlaneConfig.from_env({}))

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.payload["status"], "ok")

    def test_readiness_reports_missing_external_dependencies(self) -> None:
        response = build_response("/readyz", ControlPlaneConfig.from_env({}))

        self.assertEqual(response.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(response.payload["status"], "not_ready")
        self.assertFalse(response.payload["checks"]["database_configured"])

    def test_inference_status_is_explicitly_internal(self) -> None:
        response = build_response(
            "/v1/inference/status",
            ControlPlaneConfig.from_env(
                {"INFERENCE_BASE_URL": "http://vllm.inference.svc:8000"}
            ),
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.payload["status"], "configured")
        self.assertTrue(response.payload["internal_only"])
