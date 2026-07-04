"""Minimal HTTP surface for the control-plane skeleton.

GET  /healthz                  — liveness probe (no external deps required)
GET  /readyz                   — readiness probe (503 until dependencies configured)
GET  /v1/inference/status      — inference configuration state
GET  /v1/models                — selectable chat models (config-served, GPU-independent)
GET  /metrics                  — Prometheus metrics (golden signals, M5)
POST /v1/chat/completions      — authenticated chat path; delegates to inference plane
POST /v1/retrieval/documents   — index a document into the caller's tenant (M10)
POST /v1/retrieval/query       — tenant-scoped retrieval query (M10)
POST /v1/memory                — record a memory (explicit consent required) (M10)
GET  /v1/memory                — list the caller's memories (M10)
POST /v1/memory/recall         — per-user memory recall (M10)
DELETE /v1/memory/{id}         — authoritative delete of a memory (M10)
POST /v1/agent/tools/invoke    — sandboxed, allow-listed tool execution (M11)
POST /v1/agent/runs            — LLM agent loop over allow-listed tools (M11)
POST /v1/agent/research        — deep-research (plan→retrieve→synthesize) (M11)
POST /v1/compare               — blind A/B of one prompt across N models + synthesis
POST /v1/mcp/tools/list        — list an allow-listed MCP server's tools (M12)
POST /v1/mcp/invoke            — invoke a tool on a sandboxed MCP server (M12)
POST /v1/media/transcribe      — speech-to-text via an allow-listed backend (M14)
POST /v1/media/generate        — image generation via an allow-listed backend (M14)
POST /v1/media/synthesize      — text-to-speech via an allow-listed backend (M14)

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

Observability (M5):
  - /metrics exposes Prometheus golden-signal counters and histograms.
  - Every request generates a UUID request-id injected into the logging context.
  - Incoming X-Correlation-ID headers are forwarded to the logging context.
  - OTel spans wrap the request lifecycle when tracing is configured.
  - Content policy: NEVER include prompt text, user content, or credentials
    in metrics labels, log messages, or trace attributes.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
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
from app.control_plane.logging_config import clear_request_context, set_request_context
from app.control_plane.metrics import (
    AUTH_FAILURES_TOTAL,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUEST_ERRORS_TOTAL,
    HTTP_REQUESTS_IN_FLIGHT,
    HTTP_REQUESTS_TOTAL,
    INFERENCE_LATENCY_SECONDS,
    INFERENCE_REQUESTS_TOTAL,
    metrics_output,
    sanitise_path,
)
from app.control_plane.notifications import (
    InMemoryNotificationStore,
    NotificationStore,
    _extract_tenant_id,
    _verify_and_extract,
    build_notification_publish_response,
    build_notification_read_response,
    build_notifications_list_response,
    stream_notification_frames,
)
from app.control_plane.agent_tools import (
    RateLimiter,
    SandboxExecutor,
    build_tool_invoke_response,
    parse_allowlist,
)
from app.control_plane.agent_loop import (
    AgentLoopBudgets,
    build_agent_run_response,
)
from app.control_plane.job_executor import DispatcherJobExecutor
from app.control_plane.deep_research import (
    DeepResearchBudgets,
    build_deep_research_response,
)
from app.control_plane.mcp import (
    MCPExecutor,
    build_mcp_invoke_response,
    build_mcp_list_response,
    parse_mcp_allowlist,
)
from app.control_plane.integrations import (
    InMemoryTenantIntegrationState,
    IntegrationExecutor,
    TenantIntegrationState,
    build_integrations_invoke_response,
    build_integrations_list_response,
    parse_integration_allowlist,
)
from app.control_plane.integration_secrets import make_secrets_manager_resolver
from app.control_plane.media import (
    MediaExecutor,
    build_media_artifact_content,
    build_media_artifact_response,
    build_media_generate_response,
    build_media_list_response,
    build_media_synthesize_response,
    build_media_transcribe_response,
    parse_media_allowlist,
    parse_media_services,
)
from app.control_plane.web_search import WebSearchClient, parse_web_search_config
from app.control_plane.compare import build_compare_response
from app.control_plane.conversations import (
    ConversationStore,
    InMemoryConversationStore,
    build_conversation_append_response,
    build_conversation_create_response,
    build_conversation_delete_response,
    build_conversation_get_response,
    build_conversations_list_response,
)
from app.control_plane.embeddings import (
    DeterministicEmbeddingClient,
    EmbeddingClient,
    InferenceEmbeddingClient,
)
from app.control_plane.memory import (
    InMemoryMemoryStore,
    MemoryStore,
    build_memory_delete_response,
    build_memory_list_response,
    build_memory_recall_response,
    build_memory_record_response,
)
from app.control_plane.retrieval import (
    InMemoryRetrievalStore,
    RetrievalStore,
    build_index_document_response,
    build_retrieval_upload_response,
    build_retrieval_query_response,
)
from app.control_plane.routing import InferenceRoutingError, InferenceUnavailableError
from app.control_plane.session import InMemorySessionStore, SessionStore
from app.storage.s3 import S3StorageClient, StorageError
from app.control_plane.token_verifier import TokenVerificationError, TokenVerifier

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_CHAT_PATH = "/v1/chat/completions"
_CHAT_STREAM_PATH = "/v1/chat/stream"
_NOTIFICATIONS_PATH = "/v1/notifications"
_NOTIFICATIONS_STREAM_PATH = "/v1/notifications/stream"
# Real-time notification stream bound: max_ticks * interval ≈ connection lifetime
# before the client is asked to reconnect (keeps connections from living forever).
_NOTIF_STREAM_MAX_TICKS = 60
_NOTIF_STREAM_INTERVAL = 5.0
_RETRIEVAL_DOCUMENTS_PATH = "/v1/retrieval/documents"
_RETRIEVAL_UPLOAD_PATH = "/v1/retrieval/upload"
_RETRIEVAL_QUERY_PATH = "/v1/retrieval/query"
_MEMORY_PATH = "/v1/memory"
_MEMORY_RECALL_PATH = "/v1/memory/recall"
_AGENT_TOOLS_INVOKE_PATH = "/v1/agent/tools/invoke"
_AGENT_RUNS_PATH = "/v1/agent/runs"
_AGENT_RESEARCH_PATH = "/v1/agent/research"
_COMPARE_PATH = "/v1/compare"
_MCP_INVOKE_PATH = "/v1/mcp/invoke"
_MCP_LIST_PATH = "/v1/mcp/tools/list"
_INTEGRATIONS_INVOKE_PATH = "/v1/integrations/invoke"
_INTEGRATIONS_LIST_PATH = "/v1/integrations/list"
_MEDIA_LIST_PATH = "/v1/media/list"
_MEDIA_TRANSCRIBE_PATH = "/v1/media/transcribe"
_MEDIA_GENERATE_PATH = "/v1/media/generate"
_MEDIA_SYNTHESIZE_PATH = "/v1/media/synthesize"
_MEDIA_ARTIFACTS_PREFIX = "/v1/media/artifacts/"
_CONVERSATIONS_PATH = "/v1/conversations"
_METRICS_PATH = "/metrics"
_MAX_REQUEST_BODY = 1 * 1024 * 1024  # 1 MiB


@dataclass(frozen=True)
class Response:
    status_code: int
    payload: dict[str, Any]
    # Optional extra HTTP headers (e.g. Retry-After on 503 degraded responses).
    headers: dict[str, str] | None = None
    # When set, the handler writes this raw body instead of JSON-encoding payload.
    # Used for the /metrics endpoint which emits Prometheus text format.
    raw_body: bytes | None = None


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

    if path == "/v1/models":
        # Selectable chat models, from control-plane config (single source of
        # truth). Non-sensitive config data, served GPU-independently.
        models = config.model_list()
        return Response(
            HTTPStatus.OK,
            {"models": models, "default": models[0]},
        )

    if path == _METRICS_PATH:
        body, content_type = metrics_output()
        return Response(
            HTTPStatus.OK,
            {},
            headers={"Content-Type": content_type},
            raw_body=body,
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
        AUTH_FAILURES_TOTAL.labels(reason="missing_token").inc()
        return Response(
            HTTPStatus.UNAUTHORIZED,
            {"error": "unauthorized", "detail": "Bearer token required."},
        )

    # 2. Verify the token.
    if token_verifier is None:
        AUTH_FAILURES_TOTAL.labels(reason="auth_not_configured").inc()
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
        AUTH_FAILURES_TOTAL.labels(reason="invalid_token").inc()
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
    # All degraded 503 responses include a Retry-After header so clients can
    # back off without hammering the control plane during cold-start or outage.
    client = VLLMInferenceClient(base_url=config.inference_base_url)
    _infer_start = time.perf_counter()
    try:
        result = client.chat_completions(chat_request)  # type: ignore[arg-type]
        INFERENCE_LATENCY_SECONDS.observe(time.perf_counter() - _infer_start)
        INFERENCE_REQUESTS_TOTAL.labels(status="success").inc()
        return Response(HTTPStatus.OK, result)  # type: ignore[arg-type]
    except InferenceUnavailableError as exc:
        # HTTP error from vLLM (e.g. 503 when the model is still loading).
        # Suggest a longer back-off to allow the GPU pod to warm up.
        retry_after = "30"
        if exc.status_code is not None and exc.status_code == 429:
            # Capacity exhausted — back off longer.
            retry_after = "60"
        INFERENCE_LATENCY_SECONDS.observe(time.perf_counter() - _infer_start)
        status_label = "capacity" if exc.status_code == 429 else "unavailable"
        INFERENCE_REQUESTS_TOTAL.labels(status=status_label).inc()
        logger.warning("Inference unavailable (HTTP %s): %s", exc.status_code, type(exc).__name__)
        return Response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "error": "inference_unavailable",
                "detail": (
                    "The inference service is at capacity."
                    if exc.status_code == 429
                    else "The inference service is currently unavailable."
                ),
                "status": "degraded",
                "retry_after": int(retry_after),
            },
            headers={"Retry-After": retry_after},
        )
    except InferenceRoutingError as exc:
        INFERENCE_LATENCY_SECONDS.observe(time.perf_counter() - _infer_start)
        INFERENCE_REQUESTS_TOTAL.labels(status="routing_error").inc()
        logger.warning("Inference routing error: %s", type(exc).__name__)
        return Response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "error": "inference_not_reachable",
                "detail": "The inference endpoint could not be reached.",
                "status": "degraded",
                "retry_after": 10,
            },
            headers={"Retry-After": "10"},
        )
    except TimeoutError as exc:
        INFERENCE_LATENCY_SECONDS.observe(time.perf_counter() - _infer_start)
        INFERENCE_REQUESTS_TOTAL.labels(status="timeout").inc()
        logger.warning("Inference timed out: %s", type(exc).__name__)
        return Response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {
                "error": "inference_timeout",
                "detail": "The inference request timed out. The model may be loading — retry shortly.",
                "status": "degraded",
                "retry_after": 30,
            },
            headers={"Retry-After": "30"},
        )


def prepare_chat_stream(
    *, authorization, body, config, token_verifier,
):
    """Validate a streaming chat request before any stream bytes are written.

    Returns (error_response, None) for the auth / config / parse failures (so the
    handler can send a normal JSON response), or (None, ChatCompletionRequest) to
    proceed with the SSE stream. Pure → unit-testable like build_chat_response.
    """
    raw_token = _extract_bearer_token(authorization)
    if raw_token is None:
        AUTH_FAILURES_TOTAL.labels(reason="missing_token").inc()
        return Response(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized", "detail": "Bearer token required."}), None
    if token_verifier is None:
        return Response(HTTPStatus.SERVICE_UNAVAILABLE, {
            "error": "auth_not_configured", "status": "degraded"}), None
    try:
        token_verifier.verify(raw_token)
    except TokenVerificationError:
        AUTH_FAILURES_TOTAL.labels(reason="invalid_token").inc()
        return Response(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized", "detail": "Invalid or expired token."}), None
    if not config.inference_base_url:
        return Response(HTTPStatus.SERVICE_UNAVAILABLE, {
            "error": "inference_not_configured", "status": "degraded"}), None
    chat_request, parse_error = _parse_chat_request(body)
    if parse_error:
        return Response(HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": parse_error}), None
    return None, chat_request


# ──────────────────────────────────────────────────────────────────────────────
# HTTP handler — thin; delegates to the pure-function builders above
# ──────────────────────────────────────────────────────────────────────────────


class ControlPlaneHandler(BaseHTTPRequestHandler):
    """HTTP handler used by the development server."""

    config: ControlPlaneConfig = ControlPlaneConfig.from_env()
    token_verifier: TokenVerifier | None = None
    session_store: SessionStore = InMemorySessionStore()
    storage_client: S3StorageClient | None = None
    notification_store: NotificationStore = InMemoryNotificationStore()  # type: ignore[assignment]
    retrieval_store: RetrievalStore = InMemoryRetrievalStore()  # type: ignore[assignment]
    memory_store: MemoryStore = InMemoryMemoryStore()  # type: ignore[assignment]
    conversation_store: ConversationStore = InMemoryConversationStore()  # type: ignore[assignment]
    embedding_client: EmbeddingClient = DeterministicEmbeddingClient()
    # Agent tool framework (M11). Disabled by default (kill-switch off); the
    # allow-list is empty (deny by default) until configured.
    agent_tools_enabled: bool = False
    agent_tools_allowlist: dict = {}  # type: ignore[type-arg]
    agent_tools_executor: SandboxExecutor = SandboxExecutor()
    agent_tools_rate_limiter: RateLimiter = RateLimiter()
    # Agent loop (M11 follow-up). Shares the kill-switch + allow-list above and
    # needs an inference client (None when inference is unconfigured → 503).
    agent_loop_budgets: AgentLoopBudgets = AgentLoopBudgets()
    agent_loop_inference_client: object | None = None
    deep_research_budgets: DeepResearchBudgets = DeepResearchBudgets()
    web_search_client = None  # set at startup from WEB_SEARCH; None = web mode off
    compare_rate_limiter: RateLimiter = RateLimiter()
    # MCP integration (M12). Disabled by default; allow-list empty (deny by default).
    mcp_enabled: bool = False
    mcp_allowlist: dict = {}  # type: ignore[type-arg]
    mcp_executor: MCPExecutor = MCPExecutor()
    # Personal-info integrations (M13). Disabled by default; allow-list empty
    # (deny by default); dedicated rate limiter; default-enabled tenant state.
    integrations_enabled: bool = False
    integrations_allowlist: dict = {}  # type: ignore[type-arg]
    integrations_executor: IntegrationExecutor = IntegrationExecutor()
    integrations_rate_limiter: RateLimiter = RateLimiter()
    integrations_tenant_state: TenantIntegrationState = InMemoryTenantIntegrationState()
    # Media services (M14). Disabled by default; allow-list empty (deny by
    # default); dedicated rate limiter; per-tenant disable reuses the
    # integrations tenant-state. Registry empty until services are configured.
    media_enabled: bool = False
    media_allowlist: dict = {}  # type: ignore[type-arg]
    media_executor: MediaExecutor = MediaExecutor()
    media_rate_limiter: RateLimiter = RateLimiter()
    media_max_audio_bytes: int = 25 * 1024 * 1024
    media_max_prompt_chars: int = 2000
    # Job-sandbox dispatcher client; None/unconfigured → job-backed tools
    # are unavailable (subprocess tools unaffected).
    agent_tools_job_executor: object | None = None

    # ── Request lifecycle ──────────────────────────────────────────────────────

    def _handle(self, method: str, response: Response) -> None:
        """Record metrics and emit the response, wrapping the full lifecycle."""
        safe_path = sanitise_path(self.path)
        status = int(response.status_code)
        HTTP_REQUESTS_TOTAL.labels(method=method, path=safe_path, status_code=str(status)).inc()
        if status >= 400:
            HTTP_REQUEST_ERRORS_TOTAL.labels(method=method, path=safe_path, status_code=str(status)).inc()
        if response.raw_body is not None:
            self._write_raw(status, response.raw_body, response.headers)
        else:
            self._write_json(status, response.payload, response.headers)

    def _instrument(self, method: str, handler: Any) -> None:
        """Wrap a request handler with metrics, logging context, and tracing.

        Always emits a response (5xx on unexpected handler errors) so the
        client connection is never silently dropped.  Always records
        metrics, even on failure paths.
        """
        request_id = str(uuid.uuid4())
        correlation_id = self.headers.get("X-Correlation-ID", "")
        set_request_context(request_id, correlation_id)

        safe_path = sanitise_path(self.path)
        HTTP_REQUESTS_IN_FLIGHT.inc()
        start = time.perf_counter()
        response: Response | None = None
        try:
            from app.control_plane.tracing import get_tracer
            tracer = get_tracer()
            with tracer.start_as_current_span(f"{method} {safe_path}") as span:
                span.set_attribute("http.method", method)
                span.set_attribute("http.target", safe_path)
                span.set_attribute("request_id", request_id)
                try:
                    response = handler()
                except Exception as exc:  # noqa: BLE001 — defensive fallback
                    logger.exception("Unhandled error in request handler")
                    span.set_attribute("error.type", type(exc).__name__)
                    response = Response(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": "internal_error"},
                    )
                span.set_attribute("http.status_code", int(response.status_code))
                self._handle(method, response)
        finally:
            elapsed = time.perf_counter() - start
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=safe_path).observe(elapsed)
            HTTP_REQUESTS_IN_FLIGHT.dec()
            clear_request_context()

    # ── Verb handlers ──────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]  # strip query string for routing
        # Real-time notification stream writes SSE directly and bypasses the
        # Response/_instrument machinery (like _handle_chat_stream).
        if path == _NOTIFICATIONS_STREAM_PATH:
            self._handle_notifications_stream()
            return
        if path == _NOTIFICATIONS_PATH:
            params = self.path.split("?", 1)[1] if "?" in self.path else ""
            include_read = "include_read=true" in params
            status, payload = build_notifications_list_response(
                authorization=self.headers.get("Authorization"),
                token_verifier=self.__class__.token_verifier,
                store=self.__class__.notification_store,
                include_read=include_read,
            )
            self._instrument("GET", lambda s=status, p=payload: Response(s, p))
            return
        if path == _MEMORY_PATH:
            status, payload = build_memory_list_response(
                authorization=self.headers.get("Authorization"),
                token_verifier=self.__class__.token_verifier,
                store=self.__class__.memory_store,
            )
            self._instrument("GET", lambda s=status, p=payload: Response(s, p))
            return
        if path == _CONVERSATIONS_PATH:
            status, payload = build_conversations_list_response(
                authorization=self.headers.get("Authorization"),
                token_verifier=self.__class__.token_verifier,
                store=self.__class__.conversation_store,
            )
            self._instrument("GET", lambda s=status, p=payload: Response(s, p))
            return
        if path.startswith(_CONVERSATIONS_PATH + "/"):
            cid = path[len(_CONVERSATIONS_PATH) + 1:]
            if cid and "/" not in cid:
                status, payload = build_conversation_get_response(
                    authorization=self.headers.get("Authorization"),
                    conversation_id=cid,
                    token_verifier=self.__class__.token_verifier,
                    store=self.__class__.conversation_store,
                )
                self._instrument("GET", lambda s=status, p=payload: Response(s, p))
                return
        # GET /v1/media/artifacts/{id}/content — same-origin artifact bytes (CSP).
        if path.startswith(_MEDIA_ARTIFACTS_PREFIX) and path.endswith("/content"):
            aid = path[len(_MEDIA_ARTIFACTS_PREFIX): -len("/content")]
            if aid and "/" not in aid:
                status, ctype, raw = build_media_artifact_content(
                    authorization=self.headers.get("Authorization"),
                    artifact_id=aid,
                    token_verifier=self.__class__.token_verifier,
                    executor=self.__class__.media_executor,
                )
                self._instrument("GET", lambda s=status, c=ctype, r=raw: Response(
                    s, {}, headers={"Content-Type": c}, raw_body=r))
                return
        # GET /v1/media/artifacts/{id} — presigned URL for a caller-owned artifact.
        if path.startswith(_MEDIA_ARTIFACTS_PREFIX):
            aid = path[len(_MEDIA_ARTIFACTS_PREFIX):]
            if aid and "/" not in aid:
                status, payload = build_media_artifact_response(
                    authorization=self.headers.get("Authorization"),
                    artifact_id=aid,
                    token_verifier=self.__class__.token_verifier,
                    executor=self.__class__.media_executor,
                )
                self._instrument("GET", lambda s=status, p=payload: Response(s, p))
                return
        self._instrument("GET", lambda: build_response(self.path, self.__class__.config))

    def do_POST(self) -> None:  # noqa: N802
        # Streaming chat writes Server-Sent Events directly and bypasses the
        # Response/_instrument machinery (which assumes a single buffered body).
        if self.path.split("?", 1)[0] == _CHAT_STREAM_PATH:
            self._handle_chat_stream()
            return

        def _post() -> Response:
            path = self.path.split("?", 1)[0]
            content_length = int(self.headers.get("Content-Length", 0) or 0)
            # Media transcription (audio) and RAG file upload need a higher cap
            # than the default 1 MiB JSON-request limit.
            if path == _MEDIA_TRANSCRIBE_PATH:
                max_body = self.__class__.media_max_audio_bytes
            elif path == _RETRIEVAL_UPLOAD_PATH:
                max_body = self.__class__.config.retrieval_max_upload_bytes
            else:
                max_body = _MAX_REQUEST_BODY
            if content_length > max_body:
                return Response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            body = self.rfile.read(content_length) if content_length > 0 else b""

            if path == _CHAT_PATH:
                return build_chat_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    config=self.__class__.config,
                    token_verifier=self.__class__.token_verifier,
                )

            if path == _NOTIFICATIONS_PATH:
                status, payload = build_notification_publish_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    store=self.__class__.notification_store,
                )
                return Response(status, payload)

            if path == _RETRIEVAL_DOCUMENTS_PATH:
                status, payload = build_index_document_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    store=self.__class__.retrieval_store,
                    embedding_client=self.__class__.embedding_client,
                    notification_store=self.__class__.notification_store,
                )
                return Response(status, payload)

            if path == _RETRIEVAL_UPLOAD_PATH:
                from urllib.parse import parse_qs, urlsplit

                filename = parse_qs(urlsplit(self.path).query).get("filename", [None])[0]
                status, payload = build_retrieval_upload_response(
                    authorization=self.headers.get("Authorization"),
                    filename=filename,
                    content_type=self.headers.get("Content-Type"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    store=self.__class__.retrieval_store,
                    embedding_client=self.__class__.embedding_client,
                    storage_client=self.__class__.storage_client,
                    max_upload_bytes=self.__class__.config.retrieval_max_upload_bytes,
                    notification_store=self.__class__.notification_store,
                )
                return Response(status, payload)

            if path == _RETRIEVAL_QUERY_PATH:
                status, payload = build_retrieval_query_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    store=self.__class__.retrieval_store,
                    embedding_client=self.__class__.embedding_client,
                )
                return Response(status, payload)

            if path == _MEMORY_RECALL_PATH:
                status, payload = build_memory_recall_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    store=self.__class__.memory_store,
                    embedding_client=self.__class__.embedding_client,
                )
                return Response(status, payload)

            if path == _MEMORY_PATH:
                status, payload = build_memory_record_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    store=self.__class__.memory_store,
                    embedding_client=self.__class__.embedding_client,
                )
                return Response(status, payload)

            if path == _AGENT_TOOLS_INVOKE_PATH:
                status, payload = build_tool_invoke_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    enabled=self.__class__.agent_tools_enabled,
                    allowlist=self.__class__.agent_tools_allowlist,
                    executor=self.__class__.agent_tools_executor,
                    rate_limiter=self.__class__.agent_tools_rate_limiter,
                    notification_store=self.__class__.notification_store,
                    job_executor=self.__class__.agent_tools_job_executor,
                )
                return Response(status, payload)

            if path == _AGENT_RUNS_PATH:
                status, payload = build_agent_run_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    enabled=self.__class__.agent_tools_enabled,
                    allowlist=self.__class__.agent_tools_allowlist,
                    executor=self.__class__.agent_tools_executor,
                    rate_limiter=self.__class__.agent_tools_rate_limiter,
                    inference_client=self.__class__.agent_loop_inference_client,
                    budgets=self.__class__.agent_loop_budgets,
                    notification_store=self.__class__.notification_store,
                    job_executor=self.__class__.agent_tools_job_executor,
                )
                return Response(status, payload)

            if path == _AGENT_RESEARCH_PATH:
                status, payload = build_deep_research_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    enabled=self.__class__.agent_tools_enabled,
                    allowlist=self.__class__.agent_tools_allowlist,
                    store=self.__class__.retrieval_store,
                    embedding_client=self.__class__.embedding_client,
                    inference_client=self.__class__.agent_loop_inference_client,
                    budgets=self.__class__.deep_research_budgets,
                    rate_limiter=self.__class__.agent_tools_rate_limiter,
                    notification_store=self.__class__.notification_store,
                    web_search_client=self.__class__.web_search_client,
                )
                return Response(status, payload)

            if path == _COMPARE_PATH:
                status, payload = build_compare_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    enabled=True,
                    inference_client=self.__class__.agent_loop_inference_client,
                    rate_limiter=self.__class__.compare_rate_limiter,
                    default_models=self.__class__.config.model_list(),
                )
                return Response(status, payload)

            if path == _MCP_LIST_PATH:
                status, payload = build_mcp_list_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    enabled=self.__class__.mcp_enabled,
                    allowlist=self.__class__.mcp_allowlist,
                    executor=self.__class__.mcp_executor,
                )
                return Response(status, payload)

            if path == _MCP_INVOKE_PATH:
                status, payload = build_mcp_invoke_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    enabled=self.__class__.mcp_enabled,
                    allowlist=self.__class__.mcp_allowlist,
                    executor=self.__class__.mcp_executor,
                    rate_limiter=self.__class__.agent_tools_rate_limiter,
                    notification_store=self.__class__.notification_store,
                )
                return Response(status, payload)

            if path == _INTEGRATIONS_LIST_PATH:
                status, payload = build_integrations_list_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    enabled=self.__class__.integrations_enabled,
                    allowlist=self.__class__.integrations_allowlist,
                    executor=self.__class__.integrations_executor,
                )
                return Response(status, payload)

            if path == _INTEGRATIONS_INVOKE_PATH:
                status, payload = build_integrations_invoke_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    enabled=self.__class__.integrations_enabled,
                    allowlist=self.__class__.integrations_allowlist,
                    executor=self.__class__.integrations_executor,
                    rate_limiter=self.__class__.integrations_rate_limiter,
                    tenant_state=self.__class__.integrations_tenant_state,
                    notification_store=self.__class__.notification_store,
                )
                return Response(status, payload)

            if path == _MEDIA_LIST_PATH:
                status, payload = build_media_list_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    enabled=self.__class__.media_enabled,
                    allowlist=self.__class__.media_allowlist,
                    executor=self.__class__.media_executor,
                )
                return Response(status, payload)

            if path == _MEDIA_TRANSCRIBE_PATH:
                from urllib.parse import parse_qs, urlsplit

                service = parse_qs(urlsplit(self.path).query).get("service", [None])[0]
                status, payload = build_media_transcribe_response(
                    authorization=self.headers.get("Authorization"),
                    service=service,
                    body=body,
                    content_type=self.headers.get("Content-Type"),
                    token_verifier=self.__class__.token_verifier,
                    enabled=self.__class__.media_enabled,
                    allowlist=self.__class__.media_allowlist,
                    executor=self.__class__.media_executor,
                    rate_limiter=self.__class__.media_rate_limiter,
                    tenant_state=self.__class__.integrations_tenant_state,
                    max_audio_bytes=self.__class__.media_max_audio_bytes,
                    notification_store=self.__class__.notification_store,
                )
                return Response(status, payload)

            if path == _MEDIA_GENERATE_PATH:
                status, payload = build_media_generate_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    enabled=self.__class__.media_enabled,
                    allowlist=self.__class__.media_allowlist,
                    executor=self.__class__.media_executor,
                    rate_limiter=self.__class__.media_rate_limiter,
                    tenant_state=self.__class__.integrations_tenant_state,
                    max_prompt_chars=self.__class__.media_max_prompt_chars,
                    notification_store=self.__class__.notification_store,
                )
                return Response(status, payload)

            if path == _MEDIA_SYNTHESIZE_PATH:
                status, payload = build_media_synthesize_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    enabled=self.__class__.media_enabled,
                    allowlist=self.__class__.media_allowlist,
                    executor=self.__class__.media_executor,
                    rate_limiter=self.__class__.media_rate_limiter,
                    tenant_state=self.__class__.integrations_tenant_state,
                    max_text_chars=self.__class__.media_max_prompt_chars,
                    notification_store=self.__class__.notification_store,
                )
                return Response(status, payload)

            if path == _CONVERSATIONS_PATH:
                status, payload = build_conversation_create_response(
                    authorization=self.headers.get("Authorization"),
                    body=body,
                    token_verifier=self.__class__.token_verifier,
                    store=self.__class__.conversation_store,
                )
                return Response(status, payload)

            # POST /v1/conversations/{id}/messages — append a message.
            if path.startswith(_CONVERSATIONS_PATH + "/") and path.endswith("/messages"):
                cid = path[len(_CONVERSATIONS_PATH) + 1: -len("/messages")]
                if cid and "/" not in cid:
                    status, payload = build_conversation_append_response(
                        authorization=self.headers.get("Authorization"),
                        conversation_id=cid,
                        body=body,
                        token_verifier=self.__class__.token_verifier,
                        store=self.__class__.conversation_store,
                    )
                    return Response(status, payload)

            # POST /v1/notifications/{id}/read
            if path.startswith(_NOTIFICATIONS_PATH + "/") and path.endswith("/read"):
                # Extract the notification ID from between the prefix and "/read"
                prefix = _NOTIFICATIONS_PATH + "/"
                suffix = "/read"
                notification_id = path[len(prefix):-len(suffix)]
                if notification_id:
                    status, payload = build_notification_read_response(
                        authorization=self.headers.get("Authorization"),
                        notification_id=notification_id,
                        token_verifier=self.__class__.token_verifier,
                        store=self.__class__.notification_store,
                    )
                    return Response(status, payload)

            return Response(HTTPStatus.NOT_FOUND, {"error": "not_found", "path": self.path})
        self._instrument("POST", _post)

    def _handle_chat_stream(self) -> None:
        """POST /v1/chat/stream — proxy vLLM's OpenAI SSE stream to the client."""
        cl = int(self.headers.get("Content-Length", 0) or 0)
        if cl > _MAX_REQUEST_BODY:
            self._write_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request_too_large"})
            return
        body = self.rfile.read(cl) if cl > 0 else b""
        err, chat_request = prepare_chat_stream(
            authorization=self.headers.get("Authorization"),
            body=body,
            config=self.__class__.config,
            token_verifier=self.__class__.token_verifier,
        )
        if err is not None:
            self._write_json(err.status_code, err.payload, err.headers)
            return
        client = VLLMInferenceClient(base_url=self.__class__.config.inference_base_url)
        try:
            resp = client.open_chat_stream(chat_request)  # type: ignore[arg-type]
        except InferenceUnavailableError:
            INFERENCE_REQUESTS_TOTAL.labels(status="unavailable").inc()
            self._write_json(HTTPStatus.SERVICE_UNAVAILABLE,
                             {"error": "inference_unavailable", "status": "degraded", "retry_after": 30},
                             {"Retry-After": "30"})
            return
        except (TimeoutError, InferenceRoutingError):
            self._write_json(HTTPStatus.GATEWAY_TIMEOUT, {"error": "inference_timeout", "status": "degraded"})
            return
        # Relay the SSE stream verbatim.
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        INFERENCE_REQUESTS_TOTAL.labels(status="success").inc()
        try:
            for line in resp:
                self.wfile.write(line)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                resp.close()
            except Exception:  # pragma: no cover
                pass

    def _handle_notifications_stream(self) -> None:
        """GET /v1/notifications/stream — push unread notifications over SSE.

        Auth is enforced once at stream open (same verifier as every route); the
        connection is bounded and content-safe (frames carry shape only). Runs on
        a per-connection thread (ThreadingHTTPServer), so a slow client can't
        block others.
        """
        claims, err = _verify_and_extract(
            self.headers.get("Authorization"), self.__class__.token_verifier
        )
        if err is not None:
            status, payload = err
            self._write_json(status, payload)
            return
        tenant_id = _extract_tenant_id(claims)
        user_id = claims.subject
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        frames = stream_notification_frames(
            self.__class__.notification_store,
            tenant_id=tenant_id,
            user_id=user_id,
            max_ticks=_NOTIF_STREAM_MAX_TICKS,
            sleep=lambda: time.sleep(_NOTIF_STREAM_INTERVAL),
        )
        try:
            for frame in frames:
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_DELETE(self) -> None:  # noqa: N802
        def _delete() -> Response:
            path = self.path.split("?", 1)[0]
            # DELETE /v1/memory/{id} — authoritative delete of a stored memory.
            prefix = _MEMORY_PATH + "/"
            if path.startswith(prefix):
                memory_id = path[len(prefix):]
                if memory_id and "/" not in memory_id:
                    status, payload = build_memory_delete_response(
                        authorization=self.headers.get("Authorization"),
                        memory_id=memory_id,
                        token_verifier=self.__class__.token_verifier,
                        store=self.__class__.memory_store,
                    )
                    return Response(status, payload)
            # DELETE /v1/conversations/{id}
            cprefix = _CONVERSATIONS_PATH + "/"
            if path.startswith(cprefix):
                cid = path[len(cprefix):]
                if cid and "/" not in cid:
                    status, payload = build_conversation_delete_response(
                        authorization=self.headers.get("Authorization"),
                        conversation_id=cid,
                        token_verifier=self.__class__.token_verifier,
                        store=self.__class__.conversation_store,
                    )
                    return Response(status, payload)
            return Response(HTTPStatus.NOT_FOUND, {"error": "not_found", "path": self.path})
        self._instrument("DELETE", _delete)

    # ── Writers ────────────────────────────────────────────────────────────────

    def _write_json(
        self,
        status: int,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _write_raw(
        self,
        status: int,
        body: bytes,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        content_type = (extra_headers or {}).get("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Type", content_type)
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


def _build_session_store(config: ControlPlaneConfig) -> SessionStore:
    """Return a PostgresSessionStore when DATABASE_URL is configured.

    Behavior matrix:
      DATABASE_URL set + connect OK     → PostgresSessionStore
      DATABASE_URL set + connect fails  → development: warn and fall back to
                                          InMemorySessionStore;
                                          non-development: raise RuntimeError
                                          (fail closed so the pod restarts via
                                          the liveness probe rather than silently
                                          serving inconsistent sessions across
                                          replicas).
      DATABASE_URL unset                → development: warn and fall back;
                                          non-development: raise RuntimeError.
    """
    is_dev = config.environment == "development"

    if not config.database_url:
        if not is_dev:
            raise RuntimeError(
                "DATABASE_URL is required outside of development. "
                "Refusing to start with an in-memory session store in "
                f"environment={config.environment!r}."
            )
        logger.warning(
            "DATABASE_URL not configured — using in-memory session store. "
            "Not safe for multi-replica or production deployments."
        )
        return InMemorySessionStore()

    try:
        from app.db.connection import open_pool
        from app.db.migrations import apply_migrations
        from app.control_plane.session_postgres import PostgresSessionStore

        pool = open_pool(config.database_url)
        apply_migrations(pool)
        logger.info("Using PostgreSQL session store.")
        return PostgresSessionStore(pool)
    except Exception as exc:
        # Scrub exception text: psycopg's OperationalError may echo the
        # conninfo string (which can include host, port, dbname, and in
        # libpq URI form may include credentials).  Log only the exception
        # type name; the full traceback is available via exc_info for
        # operators who need to diagnose.
        exc_type = type(exc).__name__
        if not is_dev:
            logger.error(
                "Failed to initialize database session store (%s). "
                "Refusing to fall back to in-memory store in environment=%r.",
                exc_type, config.environment,
                exc_info=False,
            )
            raise RuntimeError(
                f"Failed to initialize database session store ({exc_type}); "
                f"refusing to fall back to in-memory store in "
                f"environment={config.environment!r}."
            ) from None
        logger.error(
            "Failed to initialize database session store (%s) — "
            "falling back to in-memory session store (development only).",
            exc_type,
            exc_info=False,
        )
        return InMemorySessionStore()


def _build_storage_client(config: ControlPlaneConfig) -> S3StorageClient | None:
    """Return an S3StorageClient when OBJECT_STORAGE_BUCKET is configured."""
    if not config.object_storage_bucket:
        logger.warning("OBJECT_STORAGE_BUCKET not configured — object storage unavailable.")
        return None
    return S3StorageClient(bucket=config.object_storage_bucket)


def _build_integration_secret_resolver(config: ControlPlaneConfig):
    """Return the per-tenant integration secret resolver, or None when disabled.

    Production resolves ONLY through AWS Secrets Manager/IRSA. In development a
    fixture token may be supplied directly (INTEGRATIONS_FIXTURE_TOKEN) so the
    loopback smoke can run without AWS; that path is gated to the development
    environment and only answers for the loopback fixture integration.
    """
    if not config.integrations_enabled:
        return None
    if config.environment == "development" and config.integrations_fixture_token:
        token = config.integrations_fixture_token

        def _dev_fixture_resolver(tenant_id: str, integration: str):
            return {"TOKEN": token} if integration == "loopback" else None

        logger.warning(
            "Using DEV fixture credential for integrations (development only); "
            "production resolves via AWS Secrets Manager/IRSA."
        )
        return _dev_fixture_resolver
    # Secret ids are built under the infra environment token (e.g. "dev"), which
    # may differ from the app's ENVIRONMENT ("development"); honour the override.
    return make_secrets_manager_resolver(
        config.integrations_secret_env or config.environment,
        ttl_seconds=config.integrations_secret_ttl_s,
    )


def _build_conversation_store(config: ControlPlaneConfig) -> ConversationStore:
    """Return a Postgres-backed conversation store when DATABASE_URL is set."""
    if not config.database_url:
        return InMemoryConversationStore()
    try:
        from app.db.connection import open_pool
        from app.control_plane.conversations import PostgresConversationStore

        pool = open_pool(config.database_url)
        logger.info("Using PostgreSQL conversation store.")
        return PostgresConversationStore(pool)
    except Exception as exc:
        exc_type = type(exc).__name__
        if config.environment != "development":
            raise RuntimeError(
                f"Failed to initialize PostgreSQL conversation store ({exc_type}); "
                f"refusing to fall back in environment={config.environment!r}."
            ) from None
        logger.error(
            "Failed to initialize PostgreSQL conversation store (%s) — "
            "falling back to in-memory (development only).", exc_type,
        )
        return InMemoryConversationStore()


def _build_tenant_integration_state(config: ControlPlaneConfig) -> TenantIntegrationState:
    """Return a Postgres-backed tenant state when DATABASE_URL is configured."""
    if not config.database_url:
        return InMemoryTenantIntegrationState()
    try:
        from app.db.connection import open_pool
        from app.control_plane.integrations import PostgresTenantIntegrationState

        pool = open_pool(config.database_url)
        logger.info("Using PostgreSQL integration tenant-state store.")
        return PostgresTenantIntegrationState(pool)
    except Exception as exc:
        exc_type = type(exc).__name__
        if config.environment != "development":
            raise RuntimeError(
                f"Failed to initialize PostgreSQL integration tenant-state ({exc_type}); "
                f"refusing to fall back in environment={config.environment!r}."
            ) from None
        logger.error(
            "Failed to initialize PostgreSQL integration tenant-state (%s) — "
            "falling back to in-memory (development only).",
            exc_type,
        )
        return InMemoryTenantIntegrationState()


def _build_notification_store(config: ControlPlaneConfig) -> NotificationStore:
    """Return a PostgresNotificationStore when DATABASE_URL is configured."""
    if not config.database_url:
        logger.warning(
            "DATABASE_URL not configured — using in-memory notification store. "
            "Not safe for multi-replica or production deployments."
        )
        return InMemoryNotificationStore()  # type: ignore[return-value]

    try:
        from app.db.connection import open_pool
        from app.control_plane.notifications import PostgresNotificationStore

        pool = open_pool(config.database_url)
        logger.info("Using PostgreSQL notification store.")
        return PostgresNotificationStore(pool)  # type: ignore[return-value]
    except Exception as exc:
        exc_type = type(exc).__name__
        is_dev = config.environment == "development"
        if not is_dev:
            raise RuntimeError(
                f"Failed to initialize PostgreSQL notification store ({exc_type}); "
                f"refusing to fall back in environment={config.environment!r}."
            ) from None
        logger.error(
            "Failed to initialize PostgreSQL notification store (%s) — "
            "falling back to in-memory store (development only).",
            exc_type,
            exc_info=False,
        )
        return InMemoryNotificationStore()  # type: ignore[return-value]


def _build_retrieval_store(config: ControlPlaneConfig) -> RetrievalStore:
    """Return a PostgresRetrievalStore when DATABASE_URL is configured."""
    if not config.database_url:
        logger.warning(
            "DATABASE_URL not configured — using in-memory retrieval store. "
            "Not safe for multi-replica or production deployments."
        )
        return InMemoryRetrievalStore()  # type: ignore[return-value]

    try:
        from app.db.connection import open_pool
        from app.control_plane.retrieval import PostgresRetrievalStore

        pool = open_pool(config.database_url)
        logger.info("Using PostgreSQL (pgvector) retrieval store.")
        return PostgresRetrievalStore(pool)  # type: ignore[return-value]
    except Exception as exc:
        exc_type = type(exc).__name__
        if config.environment != "development":
            raise RuntimeError(
                f"Failed to initialize PostgreSQL retrieval store ({exc_type}); "
                f"refusing to fall back in environment={config.environment!r}."
            ) from None
        logger.error(
            "Failed to initialize PostgreSQL retrieval store (%s) — "
            "falling back to in-memory store (development only).",
            exc_type,
            exc_info=False,
        )
        return InMemoryRetrievalStore()  # type: ignore[return-value]


def _build_memory_store(config: ControlPlaneConfig) -> MemoryStore:
    """Return a PostgresMemoryStore when DATABASE_URL is configured."""
    if not config.database_url:
        logger.warning(
            "DATABASE_URL not configured — using in-memory memory store. "
            "Not safe for multi-replica or production deployments."
        )
        return InMemoryMemoryStore()  # type: ignore[return-value]

    try:
        from app.db.connection import open_pool
        from app.control_plane.memory import PostgresMemoryStore

        pool = open_pool(config.database_url)
        logger.info("Using PostgreSQL (pgvector) memory store.")
        return PostgresMemoryStore(pool)  # type: ignore[return-value]
    except Exception as exc:
        exc_type = type(exc).__name__
        if config.environment != "development":
            raise RuntimeError(
                f"Failed to initialize PostgreSQL memory store ({exc_type}); "
                f"refusing to fall back in environment={config.environment!r}."
            ) from None
        logger.error(
            "Failed to initialize PostgreSQL memory store (%s) — "
            "falling back to in-memory store (development only).",
            exc_type,
            exc_info=False,
        )
        return InMemoryMemoryStore()  # type: ignore[return-value]


def _build_embedding_client(config: ControlPlaneConfig) -> EmbeddingClient:
    """Return an in-cluster InferenceEmbeddingClient when EMBEDDING_BASE_URL is set."""
    if config.embedding_base_url:
        logger.info(
            "Using in-cluster embedding backend at %s (model=%s).",
            config.embedding_base_url,
            config.embedding_model,
        )
        return InferenceEmbeddingClient(
            base_url=config.embedding_base_url,
            model=config.embedding_model,
        )
    logger.warning(
        "EMBEDDING_BASE_URL not configured — using the deterministic dev "
        "embedding (not a real model; suitable for development and tests only)."
    )
    return DeterministicEmbeddingClient()


def run_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    config: ControlPlaneConfig | None = None,
    session_store: SessionStore | None = None,
    storage_client: S3StorageClient | None = None,
    notification_store: NotificationStore | None = None,
    retrieval_store: RetrievalStore | None = None,
    memory_store: MemoryStore | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> None:
    """Run the development HTTP server."""
    resolved_config = config or ControlPlaneConfig.from_env()
    ControlPlaneHandler.config = resolved_config
    ControlPlaneHandler.token_verifier = resolved_config.make_token_verifier()
    ControlPlaneHandler.session_store = session_store or _build_session_store(resolved_config)
    ControlPlaneHandler.storage_client = storage_client or _build_storage_client(resolved_config)
    ControlPlaneHandler.notification_store = (  # type: ignore[assignment]
        notification_store or _build_notification_store(resolved_config)
    )
    ControlPlaneHandler.retrieval_store = (  # type: ignore[assignment]
        retrieval_store or _build_retrieval_store(resolved_config)
    )
    ControlPlaneHandler.memory_store = (  # type: ignore[assignment]
        memory_store or _build_memory_store(resolved_config)
    )
    ControlPlaneHandler.conversation_store = _build_conversation_store(resolved_config)  # type: ignore[assignment]
    ControlPlaneHandler.embedding_client = (
        embedding_client or _build_embedding_client(resolved_config)
    )
    ControlPlaneHandler.agent_tools_enabled = resolved_config.agent_tools_enabled
    ControlPlaneHandler.agent_tools_allowlist = parse_allowlist(
        resolved_config.agent_tools_allowlist
    )
    ControlPlaneHandler.agent_tools_rate_limiter = RateLimiter(
        per_minute=resolved_config.agent_tools_rate_per_minute,
        max_concurrency=resolved_config.agent_tools_max_concurrency,
    )
    ControlPlaneHandler.agent_loop_budgets = AgentLoopBudgets(
        max_steps=resolved_config.agent_loop_max_steps,
        wall_clock_seconds=resolved_config.agent_loop_wall_clock_seconds,
        max_tokens=resolved_config.agent_loop_max_tokens,
        model=resolved_config.agent_loop_model,
    )
    # The agent loop needs an inference client; None when inference is cold so
    # the run endpoint refuses cleanly (503) instead of faking work in-process.
    ControlPlaneHandler.agent_loop_inference_client = (
        VLLMInferenceClient(
            base_url=resolved_config.inference_base_url,
            timeout_seconds=resolved_config.agent_loop_wall_clock_seconds,
        )
        if resolved_config.inference_base_url
        else None
    )
    ControlPlaneHandler.agent_tools_job_executor = DispatcherJobExecutor(
        base_url=resolved_config.agent_tools_dispatcher_url,
        token=resolved_config.agent_tools_dispatcher_token,
    )
    ControlPlaneHandler.mcp_enabled = resolved_config.mcp_enabled
    ControlPlaneHandler.mcp_allowlist = parse_mcp_allowlist(resolved_config.mcp_allowlist)
    ControlPlaneHandler.mcp_executor = MCPExecutor()
    # M13 integrations. The per-tenant Secrets Manager resolver is wired only when
    # integrations are enabled; boto3 is imported lazily on first fetch. The
    # registry is empty until an integration is adopted, so the harness is inert
    # (deny-by-default) even when enabled.
    ControlPlaneHandler.integrations_enabled = resolved_config.integrations_enabled
    ControlPlaneHandler.integrations_allowlist = parse_integration_allowlist(
        resolved_config.integrations_allowlist
    )
    # Adopted real integrations are registered here; access is still deny-by-
    # default via the per-tenant allow-list, so an unconfigured tenant reaches
    # nothing. Google Calendar is the first adopted integration (NOTICE).
    from app.control_plane.integrations_google import register as register_google

    _registry = register_google({})
    # Dev-only: also register the synthetic loopback fixture when its URL is set.
    if resolved_config.integrations_fixture_url:
        from app.integration_fixtures.loopback_integration import build_fixture_registry

        _registry.update(build_fixture_registry(resolved_config.integrations_fixture_url))
    ControlPlaneHandler.integrations_executor = IntegrationExecutor(
        integrations=_registry,
        secret_resolver=_build_integration_secret_resolver(resolved_config),
        timeout_seconds=resolved_config.integrations_outbound_timeout_s,
    )
    ControlPlaneHandler.integrations_rate_limiter = RateLimiter(
        per_minute=resolved_config.integrations_rate_per_minute,
        max_concurrency=resolved_config.integrations_max_concurrency,
    )
    ControlPlaneHandler.integrations_tenant_state = _build_tenant_integration_state(
        resolved_config
    )
    # M14 media services. Backends are registered from MEDIA_SERVICES; the
    # registry is empty (deny-by-default) until services are configured. Artifacts
    # are stored per-tenant via the shared S3 client (set above).
    ControlPlaneHandler.media_enabled = resolved_config.media_enabled
    ControlPlaneHandler.media_allowlist = parse_media_allowlist(resolved_config.media_allowlist)
    ControlPlaneHandler.media_executor = MediaExecutor(
        services=parse_media_services(resolved_config.media_services),
        storage_client=ControlPlaneHandler.storage_client,
    )
    ControlPlaneHandler.media_rate_limiter = RateLimiter(
        per_minute=resolved_config.media_rate_per_minute,
        max_concurrency=resolved_config.media_max_concurrency,
    )
    ControlPlaneHandler.media_max_audio_bytes = resolved_config.media_max_audio_bytes
    ControlPlaneHandler.media_max_prompt_chars = resolved_config.media_max_prompt_chars
    ControlPlaneHandler.deep_research_budgets = DeepResearchBudgets(
        max_subqueries=resolved_config.deep_research_max_subqueries,
        top_k=resolved_config.deep_research_top_k,
        wall_clock_seconds=resolved_config.deep_research_wall_clock_seconds,
        max_tokens=resolved_config.agent_loop_max_tokens,
        model=resolved_config.agent_loop_model,
    )
    # Web search for deep research: deny-by-default. Only when WEB_SEARCH is
    # configured (a guarded external JSON search endpoint — never a bundled
    # engine) does hybrid corpus+web research become available.
    _web_cfg = parse_web_search_config(resolved_config.web_search)
    ControlPlaneHandler.web_search_client = (
        WebSearchClient(_web_cfg) if _web_cfg is not None else None
    )
    if _web_cfg is not None:
        logger.info("Web research ENABLED via provider '%s'.", _web_cfg.provider)
    if resolved_config.agent_tools_enabled:
        logger.info(
            "Agent tools ENABLED; allow-listed tenants: %d; agent runs: %s.",
            len(ControlPlaneHandler.agent_tools_allowlist),
            "available" if ControlPlaneHandler.agent_loop_inference_client else "cold (no inference)",
        )

    server = ThreadingHTTPServer((host, port), ControlPlaneHandler)
    server.serve_forever()
