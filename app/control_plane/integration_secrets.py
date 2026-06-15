"""Per-tenant credential resolution for M13 integrations (Secrets Manager/IRSA).

PR 2 of the M13 shared harness (see ``docs/m13-followups/00-build-plan.md`` and
the Decision B sign-off in ``docs/m13-shared-harness-escalation.md``). Personal-
information integrations need third-party credentials that are **per-tenant**,
fetched **at runtime**, and pick up **rotation without a pod restart** — none of
which the static External-Secrets-Operator → env-var path provides.

Design, mirroring ``app/storage/s3.py``:

- **boto3 behind an injected interface.** The low-level secret *fetch* is a
  plain ``fetch(secret_id) -> str | None`` callable. ``build_resolver`` wraps it
  with the secret-id construction and a TTL cache; ``make_secrets_manager_
  resolver`` supplies a boto3-backed fetch for production. The unit suite injects
  a fake fetch and never imports boto3 — keeping app-logic tests stdlib-only.
- **Per-tenant isolation by construction.** The secret id is built from the
  *verified token's* tenant; every path component is validated against a strict
  charset, so a tenant can never craft an id (``../other-tenant``, ``*``) that
  escapes its own prefix. The IRSA grant is scoped to
  ``<project>/<env>/integrations/*`` only (see ``modules/irsa-app``).
- **Rotation without restart.** Values are cached per secret id for a short TTL;
  once it lapses the next call refetches, so a rotated value propagates within
  the TTL with no redeploy.

Never logs secret ids or values.
"""

from __future__ import annotations

import json
import re
import time
from typing import Callable

__all__ = [
    "IntegrationSecretError",
    "build_secret_id",
    "build_resolver",
    "make_secrets_manager_resolver",
    "DEFAULT_PROJECT",
    "DEFAULT_TTL_SECONDS",
]

DEFAULT_PROJECT = "private-ai-workspace"
DEFAULT_TTL_SECONDS = 300

# A resolver maps (tenant_id, integration) -> an env-var mapping, or None when no
# credential is configured for that tenant+integration.
SecretResolver = Callable[[str, str], "dict[str, str] | None"]

# Every path component must match this. It deliberately excludes '/' and '*' so a
# component can neither traverse out of the tenant prefix nor widen the IRSA
# wildcard. Tenants are email domains (dots/hyphens), integrations and user subs
# are short slugs/uuids — all covered.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class IntegrationSecretError(Exception):
    """Raised when a secret id component is unsafe or a fetch fails fatally."""


def _check(component: str, *, label: str) -> str:
    if not component or not _SAFE_COMPONENT.match(component):
        # Do not echo the value — it may carry attacker-controlled content.
        raise IntegrationSecretError(f"unsafe {label} component")
    return component


def build_secret_id(
    env: str,
    tenant: str,
    integration: str,
    *,
    user: str | None = None,
    project: str = DEFAULT_PROJECT,
) -> str:
    """Construct the Secrets Manager id for a tenant's integration credential.

    Layout: ``<project>/<env>/integrations/<tenant>/<integration>[/<user>]``.
    Every component is validated; an unsafe component raises
    ``IntegrationSecretError`` rather than producing an id that could escape the
    tenant's prefix.
    """
    parts = [
        _check(project, label="project"),
        _check(env, label="env"),
        "integrations",
        _check(tenant, label="tenant"),
        _check(integration, label="integration"),
    ]
    if user is not None:
        parts.append(_check(user, label="user"))
    return "/".join(parts)


def _parse_secret_string(raw: str | None) -> dict[str, str] | None:
    """Parse a SecretString into an env-var mapping, or None if absent."""
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        # Non-JSON secret: expose it under a single well-known key.
        return {"value": raw}
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    return {"value": str(data)}


def build_resolver(
    fetch: Callable[[str], "str | None"],
    *,
    env: str,
    project: str = DEFAULT_PROJECT,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> SecretResolver:
    """Wrap a low-level ``fetch(secret_id) -> str | None`` into a cached resolver.

    The returned ``resolver(tenant_id, integration)`` builds the per-tenant secret
    id, fetches through ``fetch``, parses the SecretString into an env mapping,
    and caches the result per secret id for ``ttl_seconds`` (so rotation
    propagates within the TTL without a restart). ``clock`` is injectable for
    tests. Both hits and misses are cached.
    """
    cache: dict[str, tuple[float, dict[str, str] | None]] = {}

    def resolver(tenant_id: str, integration: str) -> dict[str, str] | None:
        secret_id = build_secret_id(env, tenant_id, integration, project=project)
        now = clock()
        cached = cache.get(secret_id)
        if cached is not None and cached[0] > now:
            return cached[1]
        value = _parse_secret_string(fetch(secret_id))
        cache[secret_id] = (now + ttl_seconds, value)
        return value

    return resolver


def make_secrets_manager_resolver(
    env: str,
    *,
    region: str | None = None,
    project: str = DEFAULT_PROJECT,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> SecretResolver:
    """Production resolver backed by AWS Secrets Manager via IRSA (boto3).

    boto3 is imported lazily (as in ``app/storage/s3.py``); a missing secret
    resolves to ``None`` (deny, not error), while transport/permission failures
    raise ``IntegrationSecretError`` so the caller degrades rather than leaking
    boto3 internals.
    """
    client_box: dict[str, object] = {}

    def _client() -> object:
        if "c" not in client_box:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - prod image ships boto3
                raise IntegrationSecretError(
                    "boto3>=1.35 is required for Secrets Manager access."
                ) from exc
            kwargs: dict = {}
            if region:
                kwargs["region_name"] = region
            client_box["c"] = boto3.client("secretsmanager", **kwargs)
        return client_box["c"]

    def fetch(secret_id: str) -> str | None:
        client = _client()
        try:
            resp = client.get_secret_value(SecretId=secret_id)  # type: ignore[attr-defined]
        except client.exceptions.ResourceNotFoundException:  # type: ignore[attr-defined]
            return None
        except Exception as exc:  # pragma: no cover - transport/permission errors
            raise IntegrationSecretError("secret fetch failed") from exc
        return resp.get("SecretString")

    return build_resolver(fetch, env=env, project=project, ttl_seconds=ttl_seconds)
