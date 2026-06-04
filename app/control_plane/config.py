"""Configuration model for the control-plane service.

The first implementation keeps stateful dependencies external and explicit.
Local development may start the service without these values, but readiness
should only pass when production-critical dependencies are configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ
from typing import Mapping

from app.control_plane.auth import AuthSettings


@dataclass(frozen=True)
class ControlPlaneConfig:
    """Runtime configuration loaded from environment variables."""

    service_name: str = "private-ai-workspace-control-plane"
    environment: str = "development"
    database_url: str | None = None
    object_storage_bucket: str | None = None
    secrets_provider: str = "aws-secrets-manager"
    inference_base_url: str | None = None
    auth: AuthSettings = field(default_factory=AuthSettings)
    log_level: str = "INFO"

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
            auth=AuthSettings(
                issuer_url=_clean(values.get("AUTH_ISSUER_URL")),
                audience=_clean(values.get("AUTH_AUDIENCE")),
                admin_group=_clean(values.get("AUTH_ADMIN_GROUP")),
            ),
            log_level=values.get("LOG_LEVEL", "INFO").upper(),
        )

    def readiness_checks(self) -> dict[str, bool]:
        """Return dependency checks that are safe to expose operationally."""

        return {
            "database_configured": bool(self.database_url),
            "object_storage_configured": bool(self.object_storage_bucket),
            "secrets_provider_configured": bool(self.secrets_provider),
            "inference_configured": bool(self.inference_base_url),
            "auth_configured": self.auth.is_configured(),
        }

    def is_ready(self) -> bool:
        """Whether the control plane is ready for production traffic."""

        checks = self.readiness_checks()
        return (
            checks["database_configured"]
            and checks["object_storage_configured"]
            and checks["secrets_provider_configured"]
            and checks["auth_configured"]
        )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
