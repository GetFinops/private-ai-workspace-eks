"""Endpoint routing and URL normalization for the inference plane.

Adapted from endpoint resolution patterns in pewdiepie-archdaemon/odysseus (MIT).
Source: src/endpoint_resolver.py — normalize_base, build_chat_url, _first_chat_model.
Modifications: removed SQLAlchemy/FastAPI dependencies; narrowed to the internal
vLLM contract only; restricted accepted URL schemes to http/https; added explicit
error types aligned to our failure-mode design (docs/06-cloud-architecture.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


# ──────────────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────────────


class InferenceRoutingError(Exception):
    """Raised when the control plane cannot resolve an inference endpoint."""


class InferenceUnavailableError(Exception):
    """Raised when the inference plane is reachable but returns an error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ──────────────────────────────────────────────────────────────────────────────
# URL helpers
# ──────────────────────────────────────────────────────────────────────────────


_ALLOWED_SCHEMES = frozenset({"http", "https"})


def normalize_base_url(raw: str) -> str:
    """Return a normalised base URL suitable for constructing sub-paths.

    Rules:
    - Scheme must be http or https.
    - Trailing slashes are stripped from the path.
    - Fragment and query components are dropped.
    - No credentials are allowed (raises InferenceRoutingError).

    Adapted from pewdiepie-archdaemon/odysseus src/endpoint_resolver.py
    normalize_base (MIT).
    """
    if not raw:
        raise InferenceRoutingError("Inference base URL is not configured.")

    parsed = urlparse(raw.strip())

    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise InferenceRoutingError(
            f"Inference URL scheme {parsed.scheme!r} is not allowed; "
            "use http or https."
        )

    if parsed.username or parsed.password:
        raise InferenceRoutingError(
            "Inference URL must not embed credentials. "
            "Supply authentication headers separately."
        )

    normalised = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path.rstrip("/"),
        "",  # params
        "",  # query
        "",  # fragment
    ))
    return normalised


def build_chat_completions_url(base_url: str) -> str:
    """Return the chat completions endpoint for an OpenAI-compatible backend.

    Adapted from pewdiepie-archdaemon/odysseus src/endpoint_resolver.py
    build_chat_url (MIT).
    """
    base = normalize_base_url(base_url)
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def build_models_url(base_url: str) -> str:
    """Return the /v1/models listing endpoint for an OpenAI-compatible backend."""
    base = normalize_base_url(base_url)
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


# ──────────────────────────────────────────────────────────────────────────────
# Endpoint descriptor
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InferenceEndpoint:
    """A resolved inference endpoint ready for use by the inference client."""

    base_url: str
    chat_completions_url: str
    models_url: str

    @classmethod
    def from_base_url(cls, raw_base_url: str) -> "InferenceEndpoint":
        """Resolve and validate an endpoint from a raw base URL string."""
        base = normalize_base_url(raw_base_url)
        return cls(
            base_url=base,
            chat_completions_url=build_chat_completions_url(base),
            models_url=build_models_url(base),
        )
