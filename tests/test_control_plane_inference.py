from unittest import TestCase

from app.control_plane.inference import (
    ChatCompletionRequest,
    ChatMessage,
    VLLMInferenceClient,
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

    def test_vllm_client_keeps_internal_endpoint_explicit(self) -> None:
        client = VLLMInferenceClient("http://vllm.inference.svc:8000/")
        request = ChatCompletionRequest.build(
            model="example-model",
            messages=[ChatMessage(role="user", content="hello")],
        )

        outbound = client.chat_completions(request)

        self.assertEqual(
            outbound["url"],
            "http://vllm.inference.svc:8000/v1/chat/completions",
        )
        self.assertEqual(outbound["method"], "POST")
