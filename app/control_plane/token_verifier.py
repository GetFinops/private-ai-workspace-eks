"""Bearer-token verification for the control-plane request path.

Two implementations are provided:

OIDCTokenVerifier (production)
    Fetches the JWKS from the configured issuer, verifies the JWT signature
    (RS256 or ES256), and validates standard claims (iss, aud, exp).
    Requires PyJWT >= 2.7 with the cryptography backend.

DevTokenVerifier (development only)
    Accepts a pre-configured opaque string token and returns a fixed principal.
    It performs no cryptographic work and MUST NOT be used in hosted
    deployments.  Enabled only when ENVIRONMENT=development and
    DEV_AUTH_TOKEN is set.

Adapted from authentication patterns in pewdiepie-archdaemon/odysseus (MIT);
no Odysseus code is copied directly — only the interface convention and the
claim-extraction structure are reused.  Provenance recorded in NOTICE.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ──────────────────────────────────────────────────────────────────────────────
# Public error type
# ──────────────────────────────────────────────────────────────────────────────


class TokenVerificationError(Exception):
    """Raised when a bearer token cannot be verified or has expired."""


# ──────────────────────────────────────────────────────────────────────────────
# Verified claims
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenClaims:
    """Decoded, verified claims from a validated bearer token."""

    subject: str
    email: str
    groups: frozenset[str] = field(default_factory=frozenset)
    expires_at: datetime.datetime | None = None

    def has_group(self, group: str) -> bool:
        return group in self.groups


# ──────────────────────────────────────────────────────────────────────────────
# Protocol
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class TokenVerifier(Protocol):
    """Structural interface for bearer-token verifiers."""

    def verify(self, raw_token: str) -> TokenClaims:
        """Verify *raw_token* and return its claims, or raise TokenVerificationError."""


# ──────────────────────────────────────────────────────────────────────────────
# Production: OIDC / JWT
# ──────────────────────────────────────────────────────────────────────────────


class OIDCTokenVerifier:
    """Verifies RS256/ES256 JWTs issued by a standard OIDC provider.

    Compatible with AWS Cognito, Okta, Auth0, Keycloak, and any provider
    that publishes a JWKS at {issuer_url}/.well-known/jwks.json.

    The JWKS client provided by PyJWT caches public keys automatically.
    """

    def __init__(self, *, issuer_url: str, audience: str) -> None:
        try:
            from jwt import PyJWKClient  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "OIDCTokenVerifier requires PyJWT >= 2.7 with the cryptography "
                "extra: pip install 'PyJWT>=2.7' 'cryptography>=42'"
            ) from exc

        self._issuer_url = issuer_url.rstrip("/")
        self._audience = audience
        self._jwks_client = PyJWKClient(
            f"{self._issuer_url}/.well-known/jwks.json",
            cache_keys=True,
        )

    def verify(self, raw_token: str) -> TokenClaims:
        """Verify *raw_token* against the configured OIDC issuer.

        Raises TokenVerificationError on any failure: expired token, bad
        signature, wrong audience or issuer, network error fetching JWKS.
        """
        import jwt as _jwt  # type: ignore[import]

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(raw_token)
            payload = _jwt.decode(
                raw_token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=self._audience,
                issuer=self._issuer_url,
                options={"require": ["sub", "exp", "iss", "aud"]},
            )
        except Exception as exc:
            raise TokenVerificationError(f"Token verification failed: {exc}") from exc

        exp_ts = payload.get("exp")
        expires_at = (
            datetime.datetime.fromtimestamp(exp_ts, tz=datetime.timezone.utc)
            if exp_ts is not None
            else None
        )

        # Collect group membership from common claim names used by Cognito,
        # Okta, and standard OIDC providers.
        groups: list[str] = []
        for key in ("groups", "cognito:groups", "roles"):
            value = payload.get(key)
            if isinstance(value, list):
                groups.extend(str(g) for g in value)

        return TokenClaims(
            subject=payload["sub"],
            email=payload.get("email", ""),
            groups=frozenset(groups),
            expires_at=expires_at,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Development-only: pre-shared static token
# ──────────────────────────────────────────────────────────────────────────────


class DevTokenVerifier:
    """Development-only token verifier.  MUST NOT be used in hosted deployments.

    Accepts a pre-configured opaque token string (from DEV_AUTH_TOKEN env var)
    and returns a fixed principal with admin group membership.  No
    cryptographic verification is performed.

    Activated only when ENVIRONMENT=development and DEV_AUTH_TOKEN is set.
    """

    _FORBIDDEN_ENVIRONMENTS = frozenset({"staging", "production", "prod"})

    def __init__(
        self,
        *,
        dev_token: str,
        subject: str = "dev-user",
        email: str = "dev@localhost",
        admin_group: str = "admin",
        environment: str = "development",
    ) -> None:
        if environment in self._FORBIDDEN_ENVIRONMENTS:
            raise ValueError(
                f"DevTokenVerifier must not be used in {environment!r} environment."
            )
        if not dev_token:
            raise ValueError("dev_token must not be empty.")

        self._dev_token = dev_token
        self._subject = subject
        self._email = email
        self._admin_group = admin_group

    def verify(self, raw_token: str) -> TokenClaims:
        if raw_token != self._dev_token:
            raise TokenVerificationError("Invalid dev token.")
        return TokenClaims(
            subject=self._subject,
            email=self._email,
            groups=frozenset({self._admin_group}),
        )
