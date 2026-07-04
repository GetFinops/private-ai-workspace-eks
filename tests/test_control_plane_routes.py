import json
from http import HTTPStatus
from unittest import TestCase
from unittest.mock import patch

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

    def test_inference_status_probe_surfaces_gpu_state(self) -> None:
        # ?probe=1 adds warm/cold/loading + the default model so the UI can show
        # GPU readiness. The probe is mocked so the test stays hermetic/offline.
        import app.control_plane.server as srv

        srv._gpu_probe_cache.update({"ts": 0.0, "base": None, "result": None})
        with patch(
            "app.control_plane.server.probe_inference_health",
            return_value={"gpu": "loading", "detail": "health returned 503"},
        ) as mocked:
            response = build_response(
                "/v1/inference/status?probe=1",
                ControlPlaneConfig.from_env(
                    {
                        "INFERENCE_BASE_URL": "http://vllm.secret-internal-host.svc:8123",
                        "MODELS": "llama-3-8b,mistral-7b",
                    }
                ),
            )
        mocked.assert_called_once()
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.payload["status"], "configured")
        self.assertEqual(response.payload["state"], "loading")
        self.assertEqual(response.payload["model"], "llama-3-8b")
        self.assertIn("detail", response.payload)
        self.assertIn("updated_at", response.payload)
        # Even on the probe path the internal host/port must never leak.
        blob = json.dumps(response.payload)
        self.assertNotIn("secret-internal-host", blob)
        self.assertNotIn("8123", blob)

    def test_inference_status_probe_when_unconfigured_reports_unconfigured(self) -> None:
        response = build_response(
            "/v1/inference/status?probe=1", ControlPlaneConfig.from_env({})
        )
        self.assertEqual(response.payload["status"], "not_configured")
        self.assertEqual(response.payload["state"], "unconfigured")

    def test_models_endpoint_exposes_items_and_denies_capabilities_by_default(self) -> None:
        # The Models screen contract: items[] + a deny-by-default capabilities{}
        # block, while models/default stay for chat back-compat.
        response = build_response(
            "/v1/models",
            ControlPlaneConfig.from_env({"MODELS": "llama-3-8b,mistral-7b"}),
        )
        payload = response.payload
        # Back-compat contract preserved.
        self.assertEqual(payload["models"], ["llama-3-8b", "mistral-7b"])
        self.assertEqual(payload["default"], "llama-3-8b")
        # Additive items[].
        self.assertEqual([i["id"] for i in payload["items"]], ["llama-3-8b", "mistral-7b"])
        self.assertTrue(payload["items"][0]["served"])
        self.assertFalse(payload["items"][1]["served"])
        # Every management capability is denied by default (server-computed).
        self.assertTrue(all(v is False for v in payload["capabilities"].values()))

    def test_models_request_install_capability_follows_kill_switch(self) -> None:
        # With MODEL_INSTALL_ENABLED on, only request_install flips true; every
        # other (escalation-gated) capability stays false.
        response = build_response(
            "/v1/models",
            ControlPlaneConfig.from_env({"MODEL_INSTALL_ENABLED": "true"}),
        )
        caps = response.payload["capabilities"]
        self.assertTrue(caps["request_install"])
        self.assertFalse(caps["install"])
        self.assertFalse(caps["token_config"])
        self.assertFalse(caps["search"])

    def test_inference_status_default_path_stays_cheap_and_shape_only(self) -> None:
        # Without ?probe=1 the endpoint must NOT probe and must keep the exact
        # minimal shape (regression guard for the content-safety contract).
        with patch("app.control_plane.server.probe_inference_health") as mocked:
            response = build_response(
                "/v1/inference/status",
                ControlPlaneConfig.from_env(
                    {"INFERENCE_BASE_URL": "http://vllm.inference.svc:8000"}
                ),
            )
        mocked.assert_not_called()
        self.assertEqual(set(response.payload), {"status", "backend", "internal_only"})
