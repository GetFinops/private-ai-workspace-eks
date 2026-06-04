"""Minimal HTTP surface for the control-plane skeleton.

GET  /healthz                  — liveness probe (no external deps required)
GET  /readyz                   — readiness probe (503 until dependencies configured)
GET  /v1/inference/status      — inference configuration state
POST /v1/chat/completions      — authenticated chat path; delegates to inference plane

Authentication is enforced on the chat path:
  - Requests must carry an Authorization: Bearer <token> header.
  - Tokens are verified by the configured TokenVerifier (OIDC in production,
    DevTokenVerifier in local development when DEV_AUTH_TOKEN is set).
  - Unauthenticated requests receive 401; tokens that fail verification also
    receive 401.
  - No anonymous or localhost bypasses are permitted.

Inference failures degrade gracefully: the control plane returns 503 with a
structured degraded-mode response rather than propagating the error.

The chat logic lives in build_chat_response() — a pure function that can be
tested directly without HTTP plumbing, following the same pattern as
build_response() for GET routes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

from app.control_plane.config import ControlPlaneConfig
from app.control_plane.inference import (
    ChatCompletionRequest,
    ChatMessage,
    VLLMInferenceClient,
)
from app.control_plane.routing import InferenceRoutingError, InferenceUnavailableError
from app.control_plane.session import InMemorySessionStore, SessionStore
from app.control_plane.token_verifier import TokenVerificationError, TokenVerifier

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_CHAT_PATH = "/v1/chat/completions"
_MAX_REQUEST_BODY = 1 * 1024 * 1024  # 1 MiB


@dataclass(frozen=True)
class Response:
    status_code: int
    payload: dict[str, Any]


# ──────────────────────────────────────────────────────────────────────────────
# GET route builder (unchanged public contract)
# ──────────────────────────────────────────────────────────────────────────────


def build_response(path: str, config: ControlPlaneConfig) -> Response:
    """Build a JSON response for a control-plane GET route."""

    if path == "/healthz":
        return Response(
            HTTPStatus.OK,
            {
                "status": "ok",
                "service": config.service_name,
                "environment": config.environment,
            },
        )

    if path == "/readyz":
        checks = config.readiness_checks()
        ready = config.is_ready()
        return Response(
            HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "status": "ready" if ready else "not_ready",
                "checks": checks,
            },
        )

    if path == "/v1/inference/status":
        return Response(
            HTTPStatus.OK,
            {
                "status": "configured"
                if config.inference_base_url
                else "not_configured",
                "backend": "vllm-openai-compatible",
                "internal_only": True,
            },
        )

    return Response(
        HTTPStatus.NOT_FOUND,
        {
            "error": "not_found",
            "path": path,
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Chat helpers
# ──────────────────────────────────────────────────────────────────────────────


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Extract the raw token from an Authorization: Bearer <token> header."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _parse_chat_request(body: bytes) -> tuple[ChatCompletionRequest | None, str | None]:
    """Parse a JSON chat-completion request body.

    Returns ``(request, None)`` on success or ``(None, error_message)`` on failure.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None, "request body is not valid JSON"

    if not isinstance(data, dict):
        return None, "request body must be a JSON object"

    model = data.get("model", "")
    if not isinstance(model, str) or not model.strip():
        return None, "'model' is required and must be a non-empty string"

    messages_raw = data.get("messages")
    if not isinstance(messages_raw, list) or not messages_raw:
        return None, "'messages' is required and must be a non-empty array"

    messages: list[ChatMessage] = []
    valid_roles = {"system", "user", "assistant", "tool"}
    for i, msg in enumerate(messages_raw):
        if not isinstance(msg, dict):
            return None, f"messages[{i}] must be an object"
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role not in valid_roles:
            return None, f"messages[{i}].role must be one of {sorted(valid_roles)}"
        if not isinstance(content, str):
            return None, f"messages[{i}].content must be a string"
        messages.append(ChatMessage(role=role, content=content))  # type: ignore[arg-type]

    temperature = data.get("temperature", 0.2)
    if not isinstance(temperature, (int, float)) or not (0 <= temperature <= 2):
        return None, "'temperature' must be a number between 0 and 2"

    max_tokens = data.get("max_tokens")
    if max_tokens is not None and (not isinstance(max_tokens, int) or max_tokens < 1):
        return None, "'max_tokens' must be a positive integer"

    return ChatCompletionRequest.build(
        model=model.strip(),
        messages=messages,
        temperature=float(temperature),
        max_tokens=max_tokens,
    ), None


# ──────────────────────────────────────────────────────────────────────────────
# POST /v1/chat/completions — pure function, testable without HTTP plumbing
# ──────────────────────────────────────────────────────────────────────────────


def build_chat_response(
    *,
    authorization: str | None,
    body: bytes,
    config: ControlPlaneConfig,
    token_verifier: TokenVerifier | None,
) -> Response:
    """Build a Response for POST /v1/chat/completions.

    Pure function — no HTTP plumbing — so it can be unit-tested the same way
    build_response() is tested for GET routes.
    """
    # 1. Require a bearer token.
    raw_token = _extract_bearer_token(authorization)
    if raw_token is None:
        return Response(
            HTTPStatus.UNAUTHORIZED,
            {"error": "unauthorized", "detail": "Bearer token required."},
        )

    # 2. Verify the token.
    if token_verifier is None:
        return Response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "error": "auth_not_configured",
                "detail": "Authentication is not configured on this instance.",
                "status": "degraded",
            },
        )

    try:
        token_verifier.verify(raw_token)
    except TokenVerificationError:
        return Response(
            HTTPStatus.UNAUTHORIZED,
            {"error": "unauthorized", "detail": "Invalid or expired token."},
        )

    # 3. Require inference to be configured.
    if not config.inference_base_url:
        return Response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "error": "inference_not_configured",
                "detail": "Inference backend is not configured.",
                "status": "degraded",
            },
        )

    # 4. Parse request body.
    chat_request, parse_error = _parse_chat_request(body)
    if parse_error:
        return Response(
            HTTPStatus.BAD_REQUEST,
            {"error": "bad_request", "detail": parse_error},
        )

    # 5. Forward to inference plane; degrade gracefully on failure.
    client = VLLMInferenceClient(base_url=config.inference_base_url)
    try:
        result = client.chat_completions(chat_request)  # type: ignore[arg-type]
        return Response(HTTPStatus.OK, result)  # type: ignore[arg-type]
    except (InferenceUnavailableError, InferenceRoutingError) as exc:
        logger.warning("Inference unavailable: %s", exc)
        return Response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "error": "inference_unavailable",
                "detail": "The inference service is currently unavailable.",
                "status": "degraded",
            },
        )
    except TimeoutError as exc:
        logger.warning("Inference timed out: %s", exc)
        return Response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "error": "inference_timeout",
                "detail": "The inference request timed out.",
                "status": "degraded",
            },
        )


# ──────────────────────────────────────────────────────────────────────────────
# HTTP handler — thin; delegates to the pure-function builders above
# ──────────────────────────────────────────────────────────────────────────────


class ControlPlaneHandler(BaseHTTPRequestHandler):
    """HTTP handler used by the development server."""

    config: ControlPlaneConfig = ControlPlaneConfig.from_env()
    token_verifier: TokenVerifier | None = None
    session_store: SessionStore = InMemorySessionStore()

    def do_GET(self) -> None:  # noqa: N802
        response = build_response(self.path, self.__class__.config)
        self._write_json(response.status_code, response.payload)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == _CHAT_PATH:
            content_length = int(self.headers.get("Content-Length", 0) or 0)
            if content_length > _MAX_REQUEST_BODY:
                self._write_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
                return
            body = self.rfile.read(content_length) if content_length > 0 else b""
            response = build_chat_response(
                authorization=self.headers.get("Authorization"),
                body=body,
                config=self.__class__.config,
                token_verifier=self.__class__.token_verifier,
            )
        else:
            response = Response(HTTPStatus.NOT_FOUND, {"error": "not_found", "path": self.path})
        self._write_json(response.status_code, response.payload)

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        if self.__class__.config.log_level != "DEBUG":
            return
        super().log_message(format, *args)


# ──────────────────────────────────────────────────────────────────────────────
# Server entry point
# ──────────────────────────────────────────────────────────────────────────────


def run_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    config: ControlPlaneConfig | None = None,
    session_store: SessionStore | None = None,
) -> None:
    """Run the development HTTP server."""
    resolved_config = config or ControlPlaneConfig.from_env()
    ControlPlaneHandler.config = resolved_config
    ControlPlaneHandler.token_verifier = resolved_config.make_token_verifier()
    ControlPlaneHandler.session_store = session_store or InMemorySessionStore()

    server = ThreadingHTTPServer((host, port), ControlPlaneHandler)
    server.serve_forever()
