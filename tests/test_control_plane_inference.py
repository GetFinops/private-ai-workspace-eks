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
