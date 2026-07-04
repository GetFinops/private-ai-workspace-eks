from unittest import TestCase

from app.control_plane.inference import (
    ChatCompletionRequest,
    ChatMessage,
    VLLMInferenceClient,
)
from app.control_plane.routing import (
    InferenceEndpoint,
    InferenceRoutingError,
    InferenceUnavailableError,
    build_chat_completions_url,
    normalize_base_url,
)


class InferenceContractTests(TestCase):
    def test_chat_completion_request_matches_vllm_payload_shape(self) -> None:
        request = ChatCompletionRequest.build(
            model="example-model",
            messages=[ChatMessage(role="user", content="hello")],
            temperature=0.1,
            max_tokens=64,
        )

        self.assertEqual(
            request.as_vllm_payload(),
            {
                "model": "example-model",
                "messages": [{"role": "user", "content": "hello"}],
                "temperature": 0.1,
                "max_tokens": 64,
            },
        )

    def test_vllm_client_resolves_internal_endpoint_url(self) -> None:
        client = VLLMInferenceClient("http://vllm.inference.svc:8000/")
        self.assertEqual(
            client.chat_completions_url,
            "http://vllm.inference.svc:8000/v1/chat/completions",
        )

    def test_vllm_client_raises_unavailable_on_network_failure(self) -> None:
        client = VLLMInferenceClient(
            "http://vllm.inference.svc:8000/",
            timeout_seconds=1,
        )
        request = ChatCompletionRequest.build(
            model="example-model",
            messages=[ChatMessage(role="user", content="hello")],
        )
        with self.assertRaises(InferenceUnavailableError):
            client.chat_completions(request)

    def test_vllm_client_rejects_empty_base_url(self) -> None:
        with self.assertRaises(InferenceRoutingError):
            VLLMInferenceClient("").chat_completions_url


class RoutingTests(TestCase):
    def test_normalize_strips_trailing_slash(self) -> None:
        self.assertEqual(
            normalize_base_url("http://vllm.inference.svc:8000/"),
            "http://vllm.inference.svc:8000",
        )

    def test_normalize_preserves_v1_path(self) -> None:
        self.assertEqual(
            normalize_base_url("http://vllm.inference.svc:8000/v1"),
            "http://vllm.inference.svc:8000/v1",
        )

    def test_normalize_rejects_non_http_scheme(self) -> None:
        with self.assertRaises(InferenceRoutingError):
            normalize_base_url("ftp://vllm.inference.svc:8000/")

    def test_normalize_rejects_empty(self) -> None:
        with self.assertRaises(InferenceRoutingError):
            normalize_base_url("")

    def test_normalize_rejects_credentials(self) -> None:
        with self.assertRaises(InferenceRoutingError):
            normalize_base_url("http://user:pass@vllm.inference.svc:8000/")

    def test_build_chat_completions_url_appends_v1_path(self) -> None:
        self.assertEqual(
            build_chat_completions_url("http://vllm.inference.svc:8000"),
            "http://vllm.inference.svc:8000/v1/chat/completions",
        )

    def test_build_chat_completions_url_does_not_double_v1(self) -> None:
        self.assertEqual(
            build_chat_completions_url("http://vllm.inference.svc:8000/v1"),
            "http://vllm.inference.svc:8000/v1/chat/completions",
        )

    def test_inference_endpoint_from_base_url(self) -> None:
        ep = InferenceEndpoint.from_base_url("http://vllm.inference.svc:8000/")
        self.assertEqual(ep.base_url, "http://vllm.inference.svc:8000")
        self.assertEqual(
            ep.chat_completions_url,
            "http://vllm.inference.svc:8000/v1/chat/completions",
        )
        self.assertEqual(
            ep.models_url,
            "http://vllm.inference.svc:8000/v1/models",
        )


class GpuHealthProbeTests(TestCase):
    """The /v1/inference/status warm/cold probe (GPU cold-start UX support)."""

    def test_health_url_is_built_from_server_root_not_v1(self) -> None:
        from app.control_plane.routing import build_health_url

        self.assertEqual(
            build_health_url("http://vllm.inference.svc:8000/v1"),
            "http://vllm.inference.svc:8000/health",
        )
        self.assertEqual(
            build_health_url("http://vllm.inference.svc:8000"),
            "http://vllm.inference.svc:8000/health",
        )

    def test_probe_unconfigured_when_base_url_empty(self) -> None:
        from app.control_plane.inference import probe_inference_health

        self.assertEqual(
            probe_inference_health("", timeout=0.2)["gpu"], "unconfigured"
        )

    def test_probe_classifies_unreachable_endpoint_as_cold(self) -> None:
        from app.control_plane.inference import probe_inference_health

        # An unused localhost port → connection refused → GPU scaled to zero.
        result = probe_inference_health("http://127.0.0.1:59997", timeout=0.5)
        self.assertEqual(result["gpu"], "cold")

    def test_probe_maps_health_status_via_mock(self) -> None:
        from unittest.mock import patch
        from app.control_plane import inference

        class _Resp:
            def __init__(self, status: int) -> None:
                self.status = status

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch.object(inference.urllib.request, "urlopen", return_value=_Resp(200)):
            self.assertEqual(
                inference.probe_inference_health("http://vllm:8000")["gpu"], "warm"
            )
        with patch.object(inference.urllib.request, "urlopen", return_value=_Resp(503)):
            self.assertEqual(
                inference.probe_inference_health("http://vllm:8000")["gpu"], "loading"
            )
