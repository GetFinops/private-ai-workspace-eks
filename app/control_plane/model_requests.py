"""Model install requests — tenant/user-owned intent records (design Phase 1a).

A user asks for a Hugging Face model to be installed. The control plane RECORDS
the request (deny-by-default: gated by the MODEL_INSTALL_ENABLED kill-switch, an
operator-managed HF allow-list, and a per-tenant open-request cap) and notifies
the requester. An operator (AUTH_ADMIN_GROUP) lists and approves/rejects it.

This module NEVER downloads a model, holds an HF token, or mutates the cluster —
a request is a proposal; apply stays a human/pipeline step. See
docs/m11-followups/04-model-management.md for the full phased design.

Isolation: a request is owned by exactly one (tenant_id, user_id) and re-checked
per operation; a non-admin caller can only ever see their own requests. Content
policy: the HF repo id / revision are public model identifiers (never a token or
user content) — the store carries them; logs record only the request id + status.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import Protocol, runtime_checkable

from app.control_plane.notifications import (
    ALLOWED_EVENT_CLASSES,
    NotificationEvent,
    _extract_tenant_id,
    _now_utc,
    _verify_and_extract,
)
from app.control_plane.token_verifier import TokenVerifier

logger = logging.getLogger(__name__)

# Lifecycle: a user creates a "requested" record; an operator moves it. Apply
# (making the model actually servable) remains off the control plane.
_STATUSES = ("requested", "approved", "rejected", "applied", "failed")
_OPERATOR_STATUSES = ("approved", "rejected", "applied", "failed")

# HF repo ids are "<org>/<name>"; revisions are a branch/tag/commit token.
_HF_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_MAX_REPO_CHARS = 200      # real HF repo ids are well under this; bounds storage abuse
_MAX_REVISION_CHARS = 100
_MAX_LIST_LIMIT = 200


@dataclass(frozen=True)
class ModelInstallRequest:
    """An install request owned by exactly one (tenant_id, user_id)."""

    id: str
    tenant_id: str
    user_id: str
    hf_repo_id: str
    revision: str          # "" when unspecified
    status: str
    error_class: str       # "" when none
    created_at: datetime.datetime
    updated_at: datetime.datetime

    def to_api_dict(self) -> dict:
        return {
            "id": self.id,
            "hf_repo_id": self.hf_repo_id,
            "revision": self.revision or None,
            "status": self.status,
            "error_class": self.error_class or None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def to_admin_dict(self) -> dict:
        d = self.to_api_dict()
        d["tenant_id"] = self.tenant_id      # operators see who requested it
        d["requested_by"] = self.user_id
        return d


# ── HF allow-list (deny-by-default) ────────────────────────────────────────────


def parse_repo_allowlist(raw: str | None) -> frozenset[str]:
    """Parse MODEL_INSTALL_ALLOWLIST (comma-separated orgs / exact repo ids).

    Empty/None ⇒ empty set ⇒ deny all (deny-by-default). "*" ⇒ allow any.
    """
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def is_repo_allowed(allowlist: frozenset[str], repo_id: str) -> bool:
    """A repo is allowed if the list contains "*", the exact repo id, or its org."""
    if not allowlist:
        return False
    if "*" in allowlist:
        return True
    if repo_id in allowlist:
        return True
    org = repo_id.split("/", 1)[0]
    return org in allowlist


# ── Store protocol ─────────────────────────────────────────────────────────────


@runtime_checkable
class ModelRequestStore(Protocol):
    def create(self, item: ModelInstallRequest) -> None: ...

    def list_for_user(
        self, *, tenant_id: str, user_id: str, limit: int = 100
    ) -> list[ModelInstallRequest]: ...

    def list_all(self, *, limit: int = 100) -> list[ModelInstallRequest]: ...

    def get(self, *, request_id: str) -> "ModelInstallRequest | None": ...

    def update_status(
        self, *, request_id: str, status: str, error_class: str = ""
    ) -> "ModelInstallRequest | None": ...

    def count_open_for_tenant(self, *, tenant_id: str) -> int: ...


# ── In-memory implementation (development / tests) ─────────────────────────────


class InMemoryModelRequestStore:
    def __init__(self) -> None:
        self._items: dict[str, ModelInstallRequest] = {}   # id → request
        self._lock = threading.Lock()

    def create(self, item: ModelInstallRequest) -> None:
        with self._lock:
            self._items[item.id] = item

    def list_for_user(
        self, *, tenant_id: str, user_id: str, limit: int = 100
    ) -> list[ModelInstallRequest]:
        with self._lock:
            # isolation: only the caller's own (tenant,user) rows are returned.
            items = [
                it for it in self._items.values()
                if it.tenant_id == tenant_id and it.user_id == user_id
            ]
        items.sort(key=lambda it: it.created_at, reverse=True)
        return items[:limit]

    def list_all(self, *, limit: int = 100) -> list[ModelInstallRequest]:
        with self._lock:
            items = list(self._items.values())
        items.sort(key=lambda it: it.created_at, reverse=True)
        return items[:limit]

    def get(self, *, request_id: str) -> "ModelInstallRequest | None":
        with self._lock:
            return self._items.get(request_id)

    def update_status(
        self, *, request_id: str, status: str, error_class: str = ""
    ) -> "ModelInstallRequest | None":
        with self._lock:
            it = self._items.get(request_id)
            if it is None:
                return None
            updated = replace(it, status=status, error_class=error_class, updated_at=_now_utc())
            self._items[request_id] = updated
            return updated

    def count_open_for_tenant(self, *, tenant_id: str) -> int:
        with self._lock:
            return sum(
                1 for it in self._items.values()
                if it.tenant_id == tenant_id and it.status in ("requested", "approved")
            )


# ── PostgreSQL implementation (production — requires model_install_requests) ────


class PostgresModelRequestStore:
    def __init__(self, pool: object) -> None:
        self._pool = pool

    def create(self, item: ModelInstallRequest) -> None:
        sql = """
            INSERT INTO model_install_requests
                (id, tenant_id, user_id, hf_repo_id, revision, status, error_class, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (
                    item.id, item.tenant_id, item.user_id, item.hf_repo_id, item.revision,
                    item.status, item.error_class, item.created_at, item.updated_at,
                ))
            conn.commit()

    def list_for_user(
        self, *, tenant_id: str, user_id: str, limit: int = 100
    ) -> list[ModelInstallRequest]:
        # WHERE enforces per-user isolation at the storage layer.
        sql = """
            SELECT id, tenant_id, user_id, hf_repo_id, revision, status, error_class, created_at, updated_at
            FROM model_install_requests
            WHERE tenant_id = %s AND user_id = %s
            ORDER BY created_at DESC LIMIT %s
        """
        return self._query(sql, (tenant_id, user_id, limit))

    def list_all(self, *, limit: int = 100) -> list[ModelInstallRequest]:
        sql = """
            SELECT id, tenant_id, user_id, hf_repo_id, revision, status, error_class, created_at, updated_at
            FROM model_install_requests ORDER BY created_at DESC LIMIT %s
        """
        return self._query(sql, (limit,))

    def get(self, *, request_id: str) -> "ModelInstallRequest | None":
        sql = """
            SELECT id, tenant_id, user_id, hf_repo_id, revision, status, error_class, created_at, updated_at
            FROM model_install_requests WHERE id = %s
        """
        rows = self._query(sql, (request_id,))
        return rows[0] if rows else None

    def update_status(
        self, *, request_id: str, status: str, error_class: str = ""
    ) -> "ModelInstallRequest | None":
        sql = """
            UPDATE model_install_requests
            SET status = %s, error_class = %s, updated_at = %s
            WHERE id = %s
            RETURNING id, tenant_id, user_id, hf_repo_id, revision, status, error_class, created_at, updated_at
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (status, error_class, _now_utc(), request_id))
                row = cur.fetchone()
            conn.commit()
        return self._row(row) if row else None

    def count_open_for_tenant(self, *, tenant_id: str) -> int:
        sql = """
            SELECT COUNT(*) FROM model_install_requests
            WHERE tenant_id = %s AND status IN ('requested', 'approved')
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id,))
                return int(cur.fetchone()[0])

    def _query(self, sql: str, params: tuple) -> list[ModelInstallRequest]:
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r) -> ModelInstallRequest:
        return ModelInstallRequest(
            id=str(r[0]), tenant_id=r[1], user_id=r[2], hf_repo_id=r[3], revision=r[4] or "",
            status=r[5], error_class=r[6] or "", created_at=r[7], updated_at=r[8],
        )


# ── Notification helper (best-effort; never breaks the request) ────────────────


def _notify(store, *, tenant_id: str, user_id: str, event_class: str, resource_id: str) -> None:
    if store is None or event_class not in ALLOWED_EVENT_CLASSES:
        return
    try:
        store.publish(NotificationEvent(
            id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id,
            event_class=event_class, resource_id=resource_id, created_at=_now_utc(),
        ))
    except Exception:  # pragma: no cover - notification is best-effort
        pass


# ── HTTP handlers ──────────────────────────────────────────────────────────────


def _bad(detail: str):
    return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": detail}


def build_model_request_create_response(
    *, authorization, body, token_verifier: TokenVerifier | None, store: ModelRequestStore,
    config, notification_store=None, rate_limiter=None,
):
    """POST /v1/models/install-requests — record an install request (Phase 1a)."""
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err
    # Kill-switch (deny-by-default).
    if not getattr(config, "model_install_enabled", False):
        return HTTPStatus.FORBIDDEN, {
            "error": "model_install_disabled",
            "detail": "Self-serve model install is not enabled on this workspace.",
        }

    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]

    # Per-tenant rate limit (defense-in-depth alongside the open-request cap).
    acquired = False
    if rate_limiter is not None:
        if not rate_limiter.try_acquire(tenant_id, now=int(time.time())):
            return HTTPStatus.TOO_MANY_REQUESTS, {
                "error": "rate_limited",
                "detail": "Too many install requests right now — please slow down.",
            }
        acquired = True
    try:
        try:
            data = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return _bad("Body is not valid JSON.")
        if not isinstance(data, dict):
            return _bad("Body must be a JSON object.")

        repo = data.get("hf_repo_id")
        if not isinstance(repo, str) or len(repo) > _MAX_REPO_CHARS or not _HF_REPO_RE.match(repo.strip()):
            return _bad("'hf_repo_id' must look like 'org/model'.")
        repo = repo.strip()

        revision = data.get("revision") or ""
        if revision:
            # Reject ".." defensively: a revision is a git ref, never a path — this
            # keeps it safe if a later phase resolves it against a filesystem/URL.
            if (not isinstance(revision, str) or len(revision) > _MAX_REVISION_CHARS
                    or ".." in revision or not _REVISION_RE.match(revision)):
                return _bad("'revision' is not a valid ref.")

        # Allow-list (deny-by-default).
        allowlist = parse_repo_allowlist(getattr(config, "model_install_allowlist", None))
        if not is_repo_allowed(allowlist, repo):
            return HTTPStatus.FORBIDDEN, {
                "error": "repo_not_allowed",
                "detail": "This model's org/repo is not on the operator allow-list.",
            }

        # Per-tenant open-request cap (bounds stored rows even if requests are
        # never actioned).
        cap = getattr(config, "model_install_max_open_per_tenant", 25)
        if store.count_open_for_tenant(tenant_id=tenant_id) >= cap:
            return HTTPStatus.TOO_MANY_REQUESTS, {
                "error": "too_many_open_requests",
                "detail": f"Too many open install requests (max {cap}). Wait for an operator to review.",
            }

        now = _now_utc()
        item = ModelInstallRequest(
            id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id,
            hf_repo_id=repo, revision=revision, status="requested", error_class="",
            created_at=now, updated_at=now,
        )
        store.create(item)
        _notify(notification_store, tenant_id=tenant_id, user_id=user_id,
                event_class="model_install_requested", resource_id=item.id)
        logger.info("model_install_request created id=%s status=%s", item.id, item.status)
        return HTTPStatus.ACCEPTED, item.to_api_dict()
    finally:
        if acquired and rate_limiter is not None:
            rate_limiter.release()


def _is_admin(claims, config) -> bool:
    admin_group = (getattr(getattr(config, "auth", None), "admin_group", None) or "admin")
    try:
        return claims.has_group(admin_group)
    except Exception:
        return False


def build_model_requests_list_response(
    *, authorization, token_verifier: TokenVerifier | None, store: ModelRequestStore,
    config, limit: int = 100,
):
    """GET /v1/models/install-requests — own requests; admins see all."""
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err
    limit = min(max(1, limit), _MAX_LIST_LIMIT)
    admin = _is_admin(claims, config)
    if admin:
        items = store.list_all(limit=limit)
        payload = [it.to_admin_dict() for it in items]
    else:
        items = store.list_for_user(
            tenant_id=_extract_tenant_id(claims), user_id=claims.subject, limit=limit,  # type: ignore[arg-type]
        )
        payload = [it.to_api_dict() for it in items]
    return HTTPStatus.OK, {
        "requests": payload,
        "count": len(payload),
        "is_admin": admin,
        "enabled": bool(getattr(config, "model_install_enabled", False)),
    }


def build_model_request_update_response(
    *, authorization, request_id, body, token_verifier: TokenVerifier | None,
    store: ModelRequestStore, config, notification_store=None,
):
    """POST /v1/models/install-requests/{id} — operator status change (admin only)."""
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err
    if not _is_admin(claims, config):
        return HTTPStatus.FORBIDDEN, {"error": "forbidden", "detail": "Operator role required."}
    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return _bad("Body is not valid JSON.")
    if not isinstance(data, dict):
        return _bad("Body must be a JSON object.")
    status = data.get("status")
    if status not in _OPERATOR_STATUSES:
        return _bad(f"'status' must be one of {list(_OPERATOR_STATUSES)}.")
    error_class = data.get("error_class") or ""
    if not isinstance(error_class, str) or len(error_class) > 100:
        return _bad("'error_class' must be a short string.")

    updated = store.update_status(request_id=request_id, status=status, error_class=error_class)
    if updated is None:
        return HTTPStatus.NOT_FOUND, {"error": "not_found"}
    # Notify the request's owner that an operator acted on it.
    _notify(notification_store, tenant_id=updated.tenant_id, user_id=updated.user_id,
            event_class="model_install_updated", resource_id=updated.id)
    logger.info("model_install_request updated id=%s status=%s", updated.id, updated.status)
    return HTTPStatus.OK, updated.to_admin_dict()
