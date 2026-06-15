"""Configuration model for the control-plane service.

The first implementation keeps stateful dependencies external and explicit.
Local development may start the service without these values, but readiness
should only pass when production-critical dependencies are configured.
"""

from __future__ import annotations

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
    # Retrieval embeddings (M10). EMBEDDING_BASE_URL points at an in-cluster
    # OpenAI-compatible /v1/embeddings endpoint (vLLM or a dedicated embedding
    # deployment). When unset, the deterministic dev embedding is used.
    embedding_base_url: str | None = None
    embedding_model: str = "embedding"
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
            embedding_base_url=_clean(values.get("EMBEDDING_BASE_URL")),
            embedding_model=values.get("EMBEDDING_MODEL", "embedding"),
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
