"""Configuration model for the control-plane service.

The first implementation keeps stateful dependencies external and explicit.
Local development may start the service without these values, but readiness
should only pass when production-critical dependencies are configured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from os import environ
from typing import TYPE_CHECKING, Mapping

from app.control_plane.auth import AuthSettings

if TYPE_CHECKING:
    from app.control_plane.token_verifier import TokenVerifier


@dataclass(frozen=True)
class ControlPlaneConfig:
    """Runtime configuration loaded from environment variables."""

    service_name: str = "private-ai-workspace-control-plane"
    environment: str = "development"
    database_url: str | None = None
    object_storage_bucket: str | None = None
    secrets_provider: str = "aws-secrets-manager"
    inference_base_url: str | None = None
    # Selectable chat models exposed at GET /v1/models (single source of truth,
    # replacing the UI-baked list). MODELS is a JSON array or comma-separated list
    # of model names the inference plane serves; empty falls back to ["default"].
    models: str | None = None
    # Retrieval embeddings (M10). EMBEDDING_BASE_URL points at an in-cluster
    # OpenAI-compatible /v1/embeddings endpoint (vLLM or a dedicated embedding
    # deployment). When unset, the deterministic dev embedding is used.
    embedding_base_url: str | None = None
    embedding_model: str = "embedding"
    # RAG file upload (Tier A). Max bytes for POST /v1/retrieval/upload.
    retrieval_max_upload_bytes: int = 10 * 1024 * 1024
    # Agent tool framework (M11). Disabled by default (operator kill-switch).
    # AGENT_TOOLS_ALLOWLIST is JSON: {"<tenant>": ["tool", ...]} — deny by default.
    agent_tools_enabled: bool = False
    agent_tools_allowlist: str | None = None
    agent_tools_rate_per_minute: int = 30
    agent_tools_max_concurrency: int = 4
    # Job-sandbox (M11 follow-up 3). Tools flagged executor="job" run via the
    # tool-runner dispatcher; the control plane holds NO Kubernetes privileges
    # and reaches the dispatcher over HTTP with a shared token. When unset, job-
    # backed tools are unavailable (the subprocess sandbox is unaffected).
    agent_tools_dispatcher_url: str | None = None
    agent_tools_dispatcher_token: str | None = None
    # Deep-research (M11 follow-up 2). Shares the kill-switch + allow-list (the
    # "deep_research" capability) and needs inference configured. Budgets are
    # server-enforced; model/max_tokens reuse the agent-loop settings.
    deep_research_max_subqueries: int = 4
    deep_research_top_k: int = 5
    deep_research_wall_clock_seconds: float = 90.0
    # MCP integration (M12). Disabled by default (operator kill-switch).
    # MCP_ALLOWLIST is JSON: {"<tenant>": ["<server>", ...]} — deny by default.
    mcp_enabled: bool = False
    mcp_allowlist: str | None = None
    # Personal-information integrations (M13). Disabled by default (operator
    # kill-switch). INTEGRATIONS_ALLOWLIST is JSON: {"<tenant>": ["<integration>",
    # ...]} — deny by default. The rate limiter is dedicated (not shared with the
    # agent-tools budget). Credentials are resolved per tenant at request time.
    integrations_enabled: bool = False
    integrations_allowlist: str | None = None
    integrations_rate_per_minute: int = 30
    integrations_max_concurrency: int = 4
    integrations_outbound_timeout_s: float = 10.0
    # Dev-only: when set, registers the synthetic loopback fixture integration
    # pointing at this base URL. Leave unset in staging/production (the registry
    # is empty there until a real integration is adopted).
    integrations_fixture_url: str | None = None
    # The environment token used to build integration secret ids
    # (<project>/<secret_env>/integrations/...). Defaults to `environment`, but
    # the platform's Secrets Manager naming uses the Terraform environment token
    # ("dev"), which differs from the app's ENVIRONMENT ("development"); set
    # INTEGRATIONS_SECRET_ENV to align with the IRSA-scoped prefix.
    integrations_secret_env: str | None = None
    # TTL (seconds) of the per-secret resolver cache. A rotated Secrets Manager
    # value propagates within this window with no pod restart. Lower in dev for
    # faster rotation validation.
    integrations_secret_ttl_s: int = 300
    # Dev-only: a fixture credential supplied directly (development environment
    # only) so the loopback smoke can exercise the full credentialed round-trip
    # without AWS Secrets Manager. Production resolves credentials ONLY through
    # Secrets Manager/IRSA — this field is ignored outside development.
    integrations_fixture_token: str | None = None
    # Media services (M14). Disabled by default (operator kill-switch). Each
    # service runs as an isolated GPU backend; the control plane routes to it.
    # MEDIA_ALLOWLIST is JSON {"<tenant>": ["<service>", ...]} — deny by default.
    # MEDIA_SERVICES is JSON {"<name>": {"kind": "stt"|"image", "base_url": "..."}}.
    media_enabled: bool = False
    media_allowlist: str | None = None
    media_services: str | None = None
    media_rate_per_minute: int = 10
    media_max_concurrency: int = 2
    media_max_audio_bytes: int = 25 * 1024 * 1024
    media_max_prompt_chars: int = 2000
    # Model management — self-serve install REQUESTS (design Phase 1a). Disabled
    # by default (operator kill-switch). This records tenant-scoped install intent
    # + notifies the requester for an operator to review; it NEVER downloads a
    # model or mutates the cluster (see docs/m11-followups/04-model-management.md).
    # MODEL_INSTALL_ALLOWLIST is a comma-separated list of allowed HF orgs or exact
    # repo ids (deny-by-default: empty ⇒ deny all; "*" ⇒ allow any, dev only).
    model_install_enabled: bool = False
    model_install_allowlist: str | None = None
    model_install_max_open_per_tenant: int = 25
    # Who may request a model install (a "permission"). A user has it when they
    # are in MODEL_INSTALL_GROUP (an OIDC group/role claim), when they are an
    # admin (AUTH_ADMIN_GROUP), or when MODEL_INSTALL_ALLOW_ALL_USERS is true
    # (dev convenience — every authenticated user is permitted). Deny-by-default:
    # if none apply, the request is refused even with the kill-switch on.
    model_install_group: str | None = None
    model_install_allow_all_users: bool = False
    # Web search for deep research (deny-by-default, off unless configured).
    # WEB_SEARCH is JSON {"provider","url","host","api_key","api_key_header","top_k"}.
    # No search engine is bundled: this points at an external JSON search API
    # reached through the hardened outbound guard (never SearXNG/AGPL vendored).
    web_search: str | None = None
    # Kill-switches for the inference-amplifying chat features (default on; an
    # operator can disable them without a redeploy). M7b backpressure hardening.
    compare_enabled: bool = True
    documents_enabled: bool = True
    # Upper bound on a single SSE chat-stream connection's lifetime (seconds), so
    # a client cannot hold a relay socket + thread indefinitely (M7b backpressure).
    chat_stream_max_seconds: float = 300.0
    # Agent loop (M11 follow-up). Shares the agent_tools kill-switch and
    # allow-list; additionally requires inference to be configured (cold → 503).
    # Budgets are server-enforced and never client/model settable.
    agent_loop_max_steps: int = 6
    agent_loop_wall_clock_seconds: float = 60.0
    agent_loop_max_tokens: int = 512
    agent_loop_model: str = "default"
    auth: AuthSettings = field(default_factory=AuthSettings)
    # Optional: pre-shared token accepted only in development mode.
    # Set DEV_AUTH_TOKEN in local .env to enable DevTokenVerifier.
    # Never set in staging or production environments.
    dev_auth_token: str | None = None
    log_level: str = "INFO"
    # Observability (M5)
    # LOG_FORMAT: "json" (default, for aggregators) | "text" (human-readable local dev)
    log_format: str = "json"
    # OTEL_EXPORTER_OTLP_ENDPOINT: gRPC endpoint for the OTel Collector.
    # If unset, tracing runs in no-op mode (spans collected but not exported).
    otel_endpoint: str | None = None

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None
    ) -> "ControlPlaneConfig":
        values = environ if env is None else env
        return cls(
            service_name=values.get(
                "CONTROL_PLANE_SERVICE_NAME",
                "private-ai-workspace-control-plane",
            ),
            environment=values.get("ENVIRONMENT", "development"),
            database_url=_clean(values.get("DATABASE_URL")),
            object_storage_bucket=_clean(values.get("OBJECT_STORAGE_BUCKET")),
            secrets_provider=values.get("SECRETS_PROVIDER", "aws-secrets-manager"),
            inference_base_url=_clean(values.get("INFERENCE_BASE_URL")),
            models=_clean(values.get("MODELS")),
            embedding_base_url=_clean(values.get("EMBEDDING_BASE_URL")),
            embedding_model=values.get("EMBEDDING_MODEL", "embedding"),
            retrieval_max_upload_bytes=int(values.get("RETRIEVAL_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)) or str(10 * 1024 * 1024)),
            agent_tools_enabled=values.get("AGENT_TOOLS_ENABLED", "false").lower() == "true",
            agent_tools_allowlist=_clean(values.get("AGENT_TOOLS_ALLOWLIST")),
            agent_tools_rate_per_minute=int(values.get("AGENT_TOOLS_RATE_PER_MINUTE", "30") or "30"),
            agent_tools_max_concurrency=int(values.get("AGENT_TOOLS_MAX_CONCURRENCY", "4") or "4"),
            agent_loop_max_steps=int(values.get("AGENT_LOOP_MAX_STEPS", "6") or "6"),
            agent_loop_wall_clock_seconds=float(values.get("AGENT_LOOP_WALL_CLOCK_SECONDS", "60") or "60"),
            agent_loop_max_tokens=int(values.get("AGENT_LOOP_MAX_TOKENS", "512") or "512"),
            agent_loop_model=values.get("AGENT_LOOP_MODEL", "default") or "default",
            agent_tools_dispatcher_url=_clean(values.get("AGENT_TOOLS_DISPATCHER_URL")),
            agent_tools_dispatcher_token=_clean(values.get("AGENT_TOOLS_DISPATCHER_TOKEN")),
            deep_research_max_subqueries=int(values.get("DEEP_RESEARCH_MAX_SUBQUERIES", "4") or "4"),
            deep_research_top_k=int(values.get("DEEP_RESEARCH_TOP_K", "5") or "5"),
            deep_research_wall_clock_seconds=float(values.get("DEEP_RESEARCH_WALL_CLOCK_SECONDS", "90") or "90"),
            mcp_enabled=values.get("MCP_ENABLED", "false").lower() == "true",
            mcp_allowlist=_clean(values.get("MCP_ALLOWLIST")),
            integrations_enabled=values.get("INTEGRATIONS_ENABLED", "false").lower() == "true",
            integrations_allowlist=_clean(values.get("INTEGRATIONS_ALLOWLIST")),
            integrations_rate_per_minute=int(values.get("INTEGRATIONS_RATE_PER_MINUTE", "30") or "30"),
            integrations_max_concurrency=int(values.get("INTEGRATIONS_MAX_CONCURRENCY", "4") or "4"),
            integrations_outbound_timeout_s=float(values.get("INTEGRATIONS_OUTBOUND_TIMEOUT_S", "10") or "10"),
            integrations_fixture_url=_clean(values.get("INTEGRATIONS_FIXTURE_URL")),
            integrations_secret_env=_clean(values.get("INTEGRATIONS_SECRET_ENV")),
            integrations_secret_ttl_s=int(values.get("INTEGRATIONS_SECRET_TTL_S", "300") or "300"),
            integrations_fixture_token=_clean(values.get("INTEGRATIONS_FIXTURE_TOKEN")),
            media_enabled=values.get("MEDIA_ENABLED", "false").lower() == "true",
            media_allowlist=_clean(values.get("MEDIA_ALLOWLIST")),
            media_services=_clean(values.get("MEDIA_SERVICES")),
            web_search=_clean(values.get("WEB_SEARCH")),
            compare_enabled=values.get("COMPARE_ENABLED", "true").lower() == "true",
            documents_enabled=values.get("DOCUMENTS_ENABLED", "true").lower() == "true",
            chat_stream_max_seconds=float(values.get("CHAT_STREAM_MAX_SECONDS", "300") or "300"),
            media_rate_per_minute=int(values.get("MEDIA_RATE_PER_MINUTE", "10") or "10"),
            media_max_concurrency=int(values.get("MEDIA_MAX_CONCURRENCY", "2") or "2"),
            media_max_audio_bytes=int(values.get("MEDIA_MAX_AUDIO_BYTES", str(25 * 1024 * 1024)) or str(25 * 1024 * 1024)),
            media_max_prompt_chars=int(values.get("MEDIA_MAX_PROMPT_CHARS", "2000") or "2000"),
            model_install_enabled=values.get("MODEL_INSTALL_ENABLED", "false").lower() == "true",
            model_install_allowlist=_clean(values.get("MODEL_INSTALL_ALLOWLIST")),
            model_install_max_open_per_tenant=int(
                values.get("MODEL_INSTALL_MAX_OPEN_PER_TENANT", "25") or "25"
            ),
            model_install_group=_clean(values.get("MODEL_INSTALL_GROUP")),
            model_install_allow_all_users=values.get(
                "MODEL_INSTALL_ALLOW_ALL_USERS", "false"
            ).lower() == "true",
            auth=AuthSettings(
                issuer_url=_clean(values.get("AUTH_ISSUER_URL")),
                audience=_clean(values.get("AUTH_AUDIENCE")),
                admin_group=_clean(values.get("AUTH_ADMIN_GROUP")),
            ),
            dev_auth_token=_clean(values.get("DEV_AUTH_TOKEN")),
            log_level=values.get("LOG_LEVEL", "INFO").upper(),
            log_format=values.get("LOG_FORMAT", "json").lower(),
            otel_endpoint=_clean(values.get("OTEL_EXPORTER_OTLP_ENDPOINT")),
        )

    def model_list(self) -> list[str]:
        """Parse MODELS (JSON array or comma-separated) into the selectable model
        list. Falls back to the agent-loop model, then to ["default"]. Pure config
        — no inference-plane call, so /v1/models works while the GPU is cold."""
        raw = self.models
        names: list[str] = []
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    names = [str(m).strip() for m in parsed if str(m).strip()]
            except (ValueError, json.JSONDecodeError):
                names = [m.strip() for m in raw.split(",") if m.strip()]
        if not names:
            fallback = (self.agent_loop_model or "").strip()
            names = [fallback] if fallback and fallback != "default" else ["default"]
        # De-duplicate, preserving order.
        seen: set[str] = set()
        return [m for m in names if not (m in seen or seen.add(m))]

    def readiness_checks(self) -> dict[str, bool]:
        """Return dependency checks that are safe to expose operationally.

        Checks reflect whether each dependency is *configured* (URL/bucket
        present in environment).  Deep connectivity probes are not performed
        here to keep the readiness endpoint fast and avoid probe-induced load.
        """
        return {
            "database_configured": bool(self.database_url),
            "object_storage_configured": bool(self.object_storage_bucket),
            "secrets_provider_configured": bool(self.secrets_provider),
            "inference_configured": bool(self.inference_base_url),
            "auth_configured": self.auth.is_configured(),
        }

    def is_ready(self) -> bool:
        """Whether the control plane is ready for production traffic.

        Requires database, object storage, secrets provider, and auth to be
        configured.  Inference is not required for readiness (the chat endpoint
        degrades gracefully when inference is unavailable).
        """
        checks = self.readiness_checks()
        return (
            checks["database_configured"]
            and checks["object_storage_configured"]
            and checks["secrets_provider_configured"]
            and checks["auth_configured"]
        )

    def make_token_verifier(self) -> "TokenVerifier | None":
        """Return the appropriate token verifier for this configuration.

        Returns None when neither OIDC nor dev auth is configured, which
        causes the chat endpoint to return 503 with an explicit message.
        """
        from app.control_plane.token_verifier import DevTokenVerifier, OIDCTokenVerifier

        # Development mode: accept a pre-shared static token.
        if self.environment == "development" and self.dev_auth_token:
            return DevTokenVerifier(
                dev_token=self.dev_auth_token,
                admin_group=self.auth.admin_group or "admin",
                environment=self.environment,
            )

        # Production/staging mode: OIDC JWT verification.
        if self.auth.is_configured():
            return OIDCTokenVerifier(
                issuer_url=self.auth.issuer_url,  # type: ignore[arg-type]
                audience=self.auth.audience,       # type: ignore[arg-type]
            )

        return None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
