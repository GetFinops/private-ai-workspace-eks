"""Tests for Compare — blind A/B across models + synthesis."""
import json
import unittest
from http import HTTPStatus

from app.control_plane.agent_tools import RateLimiter
from app.control_plane.compare import build_compare_response, run_compare
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
    """Returns a scripted answer per model; can be told to fail a given model."""

    def __init__(self, answers, fail=frozenset()):
        self._answers = answers
        self._fail = fail
        self.models_called = []

    def chat_completions(self, request):
        self.models_called.append(request.model)
        if request.model in self._fail:
            raise InferenceUnavailableError("down")
        return {"choices": [{"message": {"content": self._answers.get(request.model, "?")}}],
                "usage": {"total_tokens": 3}}


def _invoke(body, *, verifier=_ALICE, enabled=True, inference=None, rl=None, default_models=None):
    return build_compare_response(
        authorization="Bearer valid", body=json.dumps(body).encode(), token_verifier=verifier,
        enabled=enabled,
        inference_client=inference if inference is not None else _Inference({"a": "A-ans", "b": "B-ans"}),
        rate_limiter=rl or RateLimiter(), default_models=default_models)


class TestCompare(unittest.TestCase):
    def test_two_models_blind_labels(self):
        status, payload = _invoke({"prompt": "hi", "models": ["a", "b"]})
        self.assertEqual(status, HTTPStatus.OK)
        labels = [r["label"] for r in payload["results"]]
        self.assertEqual(labels, ["A", "B"])
        self.assertEqual(payload["results"][0]["content"], "A-ans")
        self.assertIsNone(payload["synthesis"])

    def test_one_model_failing_does_not_sink_compare(self):
        inf = _Inference({"a": "A-ans"}, fail={"b"})
        status, payload = _invoke({"prompt": "hi", "models": ["a", "b"]}, inference=inf)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(payload["results"][0]["content"], "A-ans")
        self.assertEqual(payload["results"][1]["error"], "unavailable")

    def test_synthesis_runs_when_requested(self):
        inf = _Inference({"a": "A-ans", "b": "B-ans"})
        status, payload = _invoke({"prompt": "hi", "models": ["a", "b"], "synthesize": True}, inference=inf)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIsNotNone(payload["synthesis"])  # third call produced a synthesis

    def test_requires_two_models(self):
        self.assertEqual(_invoke({"prompt": "hi", "models": ["a"]})[0], HTTPStatus.BAD_REQUEST)

    def test_falls_back_to_default_models(self):
        status, payload = _invoke({"prompt": "hi"}, default_models=["a", "b"])
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(len(payload["results"]), 2)

    def test_requires_auth(self):
        status, _ = build_compare_response(
            authorization=None, body=b"{}", token_verifier=_ALICE, enabled=True,
            inference_client=_Inference({}), rate_limiter=RateLimiter())
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)

    def test_degraded_when_inference_cold(self):
        status, payload = build_compare_response(
            authorization="Bearer valid", body=json.dumps({"prompt": "x", "models": ["a", "b"]}).encode(),
            token_verifier=_ALICE, enabled=True, inference_client=None, rate_limiter=RateLimiter())
        self.assertEqual(status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(payload["error"], "compare_unavailable")

    def test_missing_prompt(self):
        self.assertEqual(_invoke({"models": ["a", "b"]})[0], HTTPStatus.BAD_REQUEST)

    def test_rate_limited(self):
        rl = RateLimiter(per_minute=1, max_concurrency=4)
        self.assertEqual(_invoke({"prompt": "hi", "models": ["a", "b"]}, rl=rl)[0], HTTPStatus.OK)
        self.assertEqual(_invoke({"prompt": "hi", "models": ["a", "b"]}, rl=rl)[0], HTTPStatus.TOO_MANY_REQUESTS)

    def test_run_compare_orders_and_labels(self):
        inf = _Inference({"m1": "one", "m2": "two"})
        results, synthesis = run_compare("p", ["m1", "m2"], inference_client=inf)
        self.assertEqual([r.model for r in results], ["m1", "m2"])
        self.assertEqual([r.label for r in results], ["A", "B"])
        self.assertIsNone(synthesis)


if __name__ == "__main__":
    unittest.main()
