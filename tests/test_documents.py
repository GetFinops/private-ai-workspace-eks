"""Tests for the Documents editor AI-edit endpoint."""
import json
import unittest
from http import HTTPStatus

from app.control_plane.agent_tools import RateLimiter
from app.control_plane.documents import build_document_edit_response
from app.control_plane.routing import InferenceUnavailableError
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


class _Verifier:
    def __init__(self, email="alice@tenant-a.test"):
        self._claims = TokenClaims(subject="user-x", email=email)

    def verify(self, raw):
        if raw != "valid":
            raise TokenVerificationError("bad")
        return self._claims


_ALICE = _Verifier()


class _Inference:
    def __init__(self, reply="EDITED", raises=None):
        self.reply = reply
        self.raises = raises
        self.last = None

    def chat_completions(self, request):
        self.last = request
        if self.raises:
            raise self.raises
        return {"choices": [{"message": {"content": self.reply}}], "usage": {"total_tokens": 3}}


def _edit(body, *, verifier=_ALICE, enabled=True, inference=None, rl=None):
    return build_document_edit_response(
        authorization="Bearer valid", body=json.dumps(body).encode(), token_verifier=verifier,
        enabled=enabled, inference_client=inference if inference is not None else _Inference(),
        rate_limiter=rl or RateLimiter())


class TestDocumentEdit(unittest.TestCase):
    def test_happy_path_returns_revision(self):
        status, payload = _edit({"content": "hello wrld", "instruction": "fix typos"})
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["result"], "EDITED")

    def test_prompt_carries_instruction_and_document(self):
        inf = _Inference()
        _edit({"content": "the doc", "instruction": "shorten"}, inference=inf)
        user_msg = inf.last.messages[-1].content
        self.assertIn("shorten", user_msg)
        self.assertIn("the doc", user_msg)

    def test_missing_content(self):
        self.assertEqual(_edit({"instruction": "x"})[0], HTTPStatus.BAD_REQUEST)

    def test_missing_instruction(self):
        self.assertEqual(_edit({"content": "x"})[0], HTTPStatus.BAD_REQUEST)

    def test_content_too_long(self):
        self.assertEqual(_edit({"content": "x" * 20001, "instruction": "y"})[0],
                         HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

    def test_requires_auth(self):
        status, _ = build_document_edit_response(
            authorization=None, body=b"{}", token_verifier=_ALICE, enabled=True,
            inference_client=_Inference(), rate_limiter=RateLimiter())
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)

    def test_degraded_when_inference_cold(self):
        status, payload = build_document_edit_response(
            authorization="Bearer valid", body=json.dumps({"content": "a", "instruction": "b"}).encode(),
            token_verifier=_ALICE, enabled=True, inference_client=None, rate_limiter=RateLimiter())
        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(payload["error"], "documents_unavailable")

    def test_inference_failure_degrades(self):
        inf = _Inference(raises=InferenceUnavailableError("down"))
        status, payload = _edit({"content": "a", "instruction": "b"}, inference=inf)
        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)

    def test_rate_limited_and_releases(self):
        rl = RateLimiter(per_minute=1, max_concurrency=4)
        self.assertEqual(_edit({"content": "a", "instruction": "b"}, rl=rl)[0], HTTPStatus.OK)
        self.assertEqual(_edit({"content": "a", "instruction": "b"}, rl=rl)[0], HTTPStatus.TOO_MANY_REQUESTS)


if __name__ == "__main__":
    unittest.main()
