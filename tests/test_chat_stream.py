"""Tests for streaming chat (SSE) — the validation helper + inference stream open."""
import json
import unittest
from http import HTTPStatus
from unittest import mock

from app.control_plane import inference as inf
from app.control_plane.config import ControlPlaneConfig
from app.control_plane.inference import VLLMInferenceClient
from app.control_plane.routing import InferenceUnavailableError
from app.control_plane.server import prepare_chat_stream
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


class _Verifier:
    def verify(self, raw):
        if raw != "valid":
            raise TokenVerificationError("bad")
        return TokenClaims(subject="u", email="alice@tenant-a.test")


_CFG = ControlPlaneConfig(inference_base_url="http://vllm.inference.svc:8000")
_CFG_NO_INF = ControlPlaneConfig(inference_base_url=None)
_BODY = json.dumps({"model": "default", "messages": [{"role": "user", "content": "hi"}]}).encode()


class TestPrepareChatStream(unittest.TestCase):
    def test_missing_token(self):
        err, req = prepare_chat_stream(authorization=None, body=_BODY, config=_CFG, token_verifier=_Verifier())
        self.assertEqual(err.status_code, HTTPStatus.UNAUTHORIZED)
        self.assertIsNone(req)

    def test_invalid_token(self):
        err, req = prepare_chat_stream(authorization="Bearer nope", body=_BODY, config=_CFG, token_verifier=_Verifier())
        self.assertEqual(err.status_code, HTTPStatus.UNAUTHORIZED)

    def test_inference_not_configured(self):
        err, req = prepare_chat_stream(authorization="Bearer valid", body=_BODY, config=_CFG_NO_INF, token_verifier=_Verifier())
        self.assertEqual(err.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(err.payload["error"], "inference_not_configured")

    def test_bad_body(self):
        err, req = prepare_chat_stream(authorization="Bearer valid", body=b"not json", config=_CFG, token_verifier=_Verifier())
        self.assertEqual(err.status_code, HTTPStatus.BAD_REQUEST)

    def test_ok_returns_request(self):
        err, req = prepare_chat_stream(authorization="Bearer valid", body=_BODY, config=_CFG, token_verifier=_Verifier())
        self.assertIsNone(err)
        self.assertIsNotNone(req)


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


class TestOpenChatStream(unittest.TestCase):
    def _request(self):
        # Reuse the parser to build a real ChatCompletionRequest.
        from app.control_plane.server import _parse_chat_request
        req, err = _parse_chat_request(_BODY)
        assert err is None
        return req

    def test_sets_stream_true_and_returns_response(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = req.data
            return _FakeResp([b'data: {"choices":[]}\n\n', b"data: [DONE]\n\n"])

        client = VLLMInferenceClient(base_url="http://vllm.inference.svc:8000")
        with mock.patch.object(inf.urllib.request, "urlopen", fake_urlopen):
            resp = client.open_chat_stream(self._request())
        lines = list(resp)
        self.assertIn(b"[DONE]", lines[-1])
        self.assertTrue(json.loads(captured["body"]).get("stream"))

    def test_http_error_raises_unavailable(self):
        import urllib.error

        def boom(req, timeout=None):
            raise urllib.error.HTTPError("u", 503, "x", {}, None)

        client = VLLMInferenceClient(base_url="http://vllm.inference.svc:8000")
        with mock.patch.object(inf.urllib.request, "urlopen", boom):
            with self.assertRaises(InferenceUnavailableError):
                client.open_chat_stream(self._request())


if __name__ == "__main__":
    unittest.main()
