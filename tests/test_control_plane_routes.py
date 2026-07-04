import json
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

    def test_models_endpoint_defaults_without_config(self) -> None:
        response = build_response("/v1/models", ControlPlaneConfig.from_env({}))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.payload["models"], ["default"])
        self.assertEqual(response.payload["default"], "default")

    def test_models_endpoint_parses_json_list(self) -> None:
        response = build_response(
            "/v1/models",
            ControlPlaneConfig.from_env({"MODELS": '["mistral-7b", "llama-3-8b"]'}),
        )
        self.assertEqual(response.payload["models"], ["mistral-7b", "llama-3-8b"])
        self.assertEqual(response.payload["default"], "mistral-7b")

    def test_models_endpoint_parses_csv_and_is_gpu_independent(self) -> None:
        # No INFERENCE_BASE_URL configured -> still lists models (config-served).
        response = build_response(
            "/v1/models", ControlPlaneConfig.from_env({"MODELS": "a, b ,a"})
        )
        self.assertEqual(response.payload["models"], ["a", "b"])  # csv + de-duped

    def test_inference_status_discloses_only_shape_never_the_backend_url(self) -> None:
        # M7b content-safety: /v1/inference/status is an unauthenticated ops GET.
        # It must disclose only coarse shape (configured? backend kind) and NEVER
        # the internal backend URL/host or any secret — a regression guard.
        response = build_response(
            "/v1/inference/status",
            ControlPlaneConfig.from_env(
                {"INFERENCE_BASE_URL": "http://vllm.secret-internal-host.svc:8123"}
            ),
        )
        self.assertEqual(set(response.payload), {"status", "backend", "internal_only"})
        blob = json.dumps(response.payload)
        self.assertNotIn("secret-internal-host", blob)   # the internal host must not leak
        self.assertNotIn("8123", blob)                   # nor the port
