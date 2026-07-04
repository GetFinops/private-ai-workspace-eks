"""Tests for the authenticated chat endpoint and token verification."""
from __future__ import annotations

import json
from http import HTTPStatus
from unittest import TestCase
from unittest.mock import patch

from app.control_plane.config import ControlPlaneConfig
from app.control_plane.server import (
    _extract_bearer_token,
    _parse_chat_request,
    build_chat_response,
)
from app.control_plane.token_verifier import (
    DevTokenVerifier,
    TokenClaims,
    TokenVerificationError,
    TokenVerifier,
)


# ──────────────────────────────────────────────────────────────────────────────
# Test fixtures
# ──────────────────────────────────────────────────────────────────────────────


def _cfg_with_inference() -> ControlPlaneConfig:
    return ControlPlaneConfig.from_env(
        {"INFERENCE_BASE_URL": "http://vllm.svc:8000", "ENVIRONMENT": "development"}
    )


def _cfg_no_inference() -> ControlPlaneConfig:
    return ControlPlaneConfig.from_env({"ENVIRONMENT": "development"})


def _valid_claims() -> TokenClaims:
    return TokenClaims(subject="u1", email="u1@example.com", groups=frozenset({"users"}))


class _AcceptingVerifier:
    def verify(self, _token: str) -> TokenClaims:
        return _valid_claims()


class _RejectingVerifier:
    def verify(self, _token: str) -> TokenClaims:
        raise TokenVerificationError("bad token")


_VALID_BODY = json.dumps(
    {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}
).encode()


# ──────────────────────────────────────────────────────────────────────────────
# Bearer token extraction
# ──────────────────────────────────────────────────────────────────────────────


class BearerExtractionTests(TestCase):
    def test_extracts_token(self) -> None:
        self.assertEqual(_extract_bearer_token("Bearer mytoken"), "mytoken")

    def test_case_insensitive_scheme(self) -> None:
        self.assertEqual(_extract_bearer_token("bearer mytoken"), "mytoken")

    def test_none_on_missing_header(self) -> None:
        self.assertIsNone(_extract_bearer_token(None))

    def test_none_on_empty_string(self) -> None:
        self.assertIsNone(_extract_bearer_token(""))

    def test_none_on_wrong_scheme(self) -> None:
        self.assertIsNone(_extract_bearer_token("Basic dXNlcjpwYXNz"))

    def test_none_on_bearer_without_token(self) -> None:
        self.assertIsNone(_extract_bearer_token("Bearer "))


# ──────────────────────────────────────────────────────────────────────────────
# Chat request parsing
# ──────────────────────────────────────────────────────────────────────────────


class ChatRequestParsingTests(TestCase):
    def _body(self, **overrides) -> bytes:
        base = {"model": "test-model", "messages": [{"role": "user", "content": "hello"}]}
        base.update(overrides)
        return json.dumps(base).encode()

    def test_valid_request_parsed(self) -> None:
        req, err = _parse_chat_request(self._body())
        self.assertIsNone(err)
        self.assertEqual(req.model, "test-model")

    def test_missing_model_returns_error(self) -> None:
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        req, err = _parse_chat_request(body)
        self.assertIsNone(req)
        self.assertIn("model", err)

    def test_empty_messages_returns_error(self) -> None:
        body = json.dumps({"model": "m", "messages": []}).encode()
        req, err = _parse_chat_request(body)
        self.assertIsNone(req)
        self.assertIn("messages", err)

    def test_invalid_role_returns_error(self) -> None:
        body = json.dumps(
            {"model": "m", "messages": [{"role": "invalid", "content": "hi"}]}
        ).encode()
        req, err = _parse_chat_request(body)
        self.assertIsNone(req)
        self.assertIn("role", err)

    def test_invalid_json_returns_error(self) -> None:
        req, err = _parse_chat_request(b"{not json}")
        self.assertIsNone(req)
        self.assertIsNotNone(err)

    def test_temperature_and_max_tokens_forwarded(self) -> None:
        req, err = _parse_chat_request(self._body(temperature=0.7, max_tokens=128))
        self.assertIsNone(err)
        self.assertEqual(req.temperature, 0.7)
        self.assertEqual(req.max_tokens, 128)


# ──────────────────────────────────────────────────────────────────────────────
# build_chat_response — authentication and degradation paths
# ──────────────────────────────────────────────────────────────────────────────


class ChatResponseAuthTests(TestCase):
    def test_missing_token_returns_401(self) -> None:
        resp = build_chat_response(
            authorization=None,
            body=_VALID_BODY,
            config=_cfg_with_inference(),
            token_verifier=_AcceptingVerifier(),
        )
        self.assertEqual(resp.status_code, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(resp.payload["error"], "unauthorized")

    def test_invalid_token_returns_401(self) -> None:
        resp = build_chat_response(
            authorization="Bearer bad",
            body=_VALID_BODY,
            config=_cfg_with_inference(),
            token_verifier=_RejectingVerifier(),
        )
        self.assertEqual(resp.status_code, HTTPStatus.UNAUTHORIZED)

    def test_no_verifier_configured_returns_503(self) -> None:
        resp = build_chat_response(
            authorization="Bearer tok",
            body=_VALID_BODY,
            config=_cfg_with_inference(),
            token_verifier=None,
        )
        self.assertEqual(resp.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(resp.payload["error"], "auth_not_configured")

    def test_no_inference_configured_returns_503_degraded(self) -> None:
        resp = build_chat_response(
            authorization="Bearer tok",
            body=_VALID_BODY,
            config=_cfg_no_inference(),
            token_verifier=_AcceptingVerifier(),
        )
        self.assertEqual(resp.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(resp.payload["error"], "inference_not_configured")
        self.assertEqual(resp.payload["status"], "degraded")

    def test_bad_request_body_returns_400(self) -> None:
        resp = build_chat_response(
            authorization="Bearer tok",
            body=b"{not json}",
            config=_cfg_with_inference(),
            token_verifier=_AcceptingVerifier(),
        )
        self.assertEqual(resp.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(resp.payload["error"], "bad_request")

    def test_inference_unavailable_returns_503_degraded(self) -> None:
        from app.control_plane.routing import InferenceUnavailableError

        with patch(
            "app.control_plane.server.VLLMInferenceClient.chat_completions",
            side_effect=InferenceUnavailableError("down"),
        ):
            resp = build_chat_response(
                authorization="Bearer tok",
                body=_VALID_BODY,
                config=_cfg_with_inference(),
                token_verifier=_AcceptingVerifier(),
            )
        self.assertEqual(resp.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(resp.payload["error"], "inference_unavailable")
        self.assertEqual(resp.payload["status"], "degraded")

    def test_inference_timeout_returns_503_degraded(self) -> None:
        with patch(
            "app.control_plane.server.VLLMInferenceClient.chat_completions",
            side_effect=TimeoutError("timed out"),
        ):
            resp = build_chat_response(
                authorization="Bearer tok",
                body=_VALID_BODY,
                config=_cfg_with_inference(),
                token_verifier=_AcceptingVerifier(),
            )
        self.assertEqual(resp.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertEqual(resp.payload["error"], "inference_timeout")

    def test_successful_inference_returns_200(self) -> None:
        fake = {
            "id": "cmpl-1",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        }
        with patch(
            "app.control_plane.server.VLLMInferenceClient.chat_completions",
            return_value=fake,
        ):
            resp = build_chat_response(
                authorization="Bearer tok",
                body=_VALID_BODY,
                config=_cfg_with_inference(),
                token_verifier=_AcceptingVerifier(),
            )
        self.assertEqual(resp.status_code, HTTPStatus.OK)
        self.assertEqual(resp.payload["object"], "chat.completion")

    def test_rate_limited_returns_429(self) -> None:
        # M7b backpressure: the primary chat path honors a per-tenant limiter.
        from app.control_plane.agent_tools import RateLimiter

        rl = RateLimiter(per_minute=1, max_concurrency=4)
        with patch(
            "app.control_plane.server.VLLMInferenceClient.chat_completions",
            return_value={"object": "chat.completion", "choices": []},
        ):
            first = build_chat_response(
                authorization="Bearer tok", body=_VALID_BODY, config=_cfg_with_inference(),
                token_verifier=_AcceptingVerifier(), rate_limiter=rl)
            second = build_chat_response(
                authorization="Bearer tok", body=_VALID_BODY, config=_cfg_with_inference(),
                token_verifier=_AcceptingVerifier(), rate_limiter=rl)
        self.assertEqual(first.status_code, HTTPStatus.OK)
        self.assertEqual(second.status_code, HTTPStatus.TOO_MANY_REQUESTS)
        self.assertEqual(second.payload["error"], "rate_limited")

    def test_no_rate_limiter_is_unbounded(self) -> None:
        # Backward-compatible: without a limiter (the default), no 429.
        with patch(
            "app.control_plane.server.VLLMInferenceClient.chat_completions",
            return_value={"object": "chat.completion", "choices": []},
        ):
            resp = None
            for _ in range(3):
                resp = build_chat_response(
                    authorization="Bearer tok", body=_VALID_BODY,
                    config=_cfg_with_inference(), token_verifier=_AcceptingVerifier())
        self.assertEqual(resp.status_code, HTTPStatus.OK)


# ──────────────────────────────────────────────────────────────────────────────
# DevTokenVerifier
# ──────────────────────────────────────────────────────────────────────────────


class DevTokenVerifierTests(TestCase):
    def test_accepts_configured_token(self) -> None:
        v = DevTokenVerifier(dev_token="secret", environment="development")
        claims = v.verify("secret")
        self.assertEqual(claims.subject, "dev-user")
        self.assertIn("admin", claims.groups)

    def test_rejects_wrong_token(self) -> None:
        v = DevTokenVerifier(dev_token="secret", environment="development")
        with self.assertRaises(TokenVerificationError):
            v.verify("wrong")

    def test_refuses_production_environment(self) -> None:
        with self.assertRaises(ValueError):
            DevTokenVerifier(dev_token="t", environment="production")

    def test_refuses_staging_environment(self) -> None:
        with self.assertRaises(ValueError):
            DevTokenVerifier(dev_token="t", environment="staging")

    def test_refuses_empty_token(self) -> None:
        with self.assertRaises(ValueError):
            DevTokenVerifier(dev_token="", environment="development")


# ──────────────────────────────────────────────────────────────────────────────
# Config.make_token_verifier factory
# ──────────────────────────────────────────────────────────────────────────────


class MakeTokenVerifierTests(TestCase):
    def test_dev_mode_returns_dev_verifier(self) -> None:
        from app.control_plane.token_verifier import DevTokenVerifier as DTV

        cfg = ControlPlaneConfig.from_env(
            {"ENVIRONMENT": "development", "DEV_AUTH_TOKEN": "tok123"}
        )
        self.assertIsInstance(cfg.make_token_verifier(), DTV)

    def test_no_auth_returns_none(self) -> None:
        cfg = ControlPlaneConfig.from_env({"ENVIRONMENT": "development"})
        self.assertIsNone(cfg.make_token_verifier())

    def test_oidc_mode_returns_oidc_verifier(self) -> None:
        from app.control_plane.token_verifier import OIDCTokenVerifier as OTV

        cfg = ControlPlaneConfig.from_env({
            "ENVIRONMENT": "production",
            "AUTH_ISSUER_URL": "https://auth.example.com",
            "AUTH_AUDIENCE": "api",
            "AUTH_ADMIN_GROUP": "admins",
        })
        self.assertIsInstance(cfg.make_token_verifier(), OTV)
