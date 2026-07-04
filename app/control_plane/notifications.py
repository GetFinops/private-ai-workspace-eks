"""Tenant-scoped in-app notification service.

Provides:
  - NotificationEvent: immutable event data model
  - NotificationStore: structural protocol (read/write interface)
  - InMemoryNotificationStore: single-process development implementation
  - PostgresNotificationStore: production implementation (requires M3 RDS)

Pure handler functions (no HTTP plumbing; testable without a server):
  - build_notifications_list_response(...)
  - build_notification_publish_response(...)
  - build_notification_read_response(...)

Content policy (inherited from M5 observability baseline):
  NEVER include prompt text, user content, or completion content in
  notification events.  Stored fields are: event_class, resource_id,
  and timestamps only.

Isolation invariant:
  Every read and write is scoped to (tenant_id, user_id).  Cross-tenant and
  cross-user access is prevented at the store layer; the API layer adds a
  second check but MUST NOT be the only safeguard.

Provenance:
  EventBus and NotificationService patterns adapted from
  pewdiepie-archdaemon/odysseus src/event_bus.py and src/webhook_manager.py
  (MIT).  No Odysseus code is copied directly — only the structural pattern
  of tenant-scoped queues with read/unread tracking is reused.
"""

from __future__ import annotations

import datetime
import json
import threading
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from typing import Protocol, runtime_checkable

from app.control_plane.token_verifier import TokenClaims, TokenVerificationError, TokenVerifier


# ──────────────────────────────────────────────────────────────────────────────
# Allowed event classes (content-policy enforcement at the API boundary)
# ──────────────────────────────────────────────────────────────────────────────

ALLOWED_EVENT_CLASSES: frozenset[str] = frozenset({
    "indexing_complete",       # M10: RAG document indexed
    "agent_task_done",         # M11: long-running agent task (generic)
    "agent_task_progress",     # M11: agent run started / in progress
    "agent_task_completed",    # M11: tool/agent task completed
    "agent_task_failed",       # M11: tool/agent task failed
    "media_generation_complete",  # M14: image/video generation completed
    "media_task_completed",    # M14: media task (transcribe/generate) completed
    "media_task_failed",       # M14: media task failed
    "model_install_requested", # model mgmt (Phase 1a): install request recorded
    "model_install_updated",   # model mgmt (Phase 1a): operator changed request status
    "system_notice",           # platform-level operator messages
})

_MAX_RESOURCE_ID_LEN = 256
_MAX_NOTIFICATIONS_PER_USER = 200  # ring-buffer cap; oldest are dropped


# ──────────────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NotificationEvent:
    """An immutable notification event record."""

    id: str
    tenant_id: str
    user_id: str
    event_class: str
    resource_id: str
    created_at: datetime.datetime
    read_at: datetime.datetime | None = None

    def to_api_dict(self) -> dict:
        """Return a serialisable dict safe for the public API response."""
        return {
            "id": self.id,
            "event_class": self.event_class,
            "resource_id": self.resource_id,
            "created_at": self.created_at.isoformat(),
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "read": self.read_at is not None,
        }


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc)


def _extract_tenant_id(claims: TokenClaims) -> str:
    """Derive a tenant identifier from OIDC claims.

    Uses the email domain as the tenant key for a private-org deployment.
    Falls back to 'default' when no email domain is present.
    """
    if claims.email and "@" in claims.email:
        return claims.email.split("@", 1)[1].lower()
    return "default"


# ──────────────────────────────────────────────────────────────────────────────
# Store protocol
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class NotificationStore(Protocol):
    """Read/write interface for notification events."""

    def list_for_user(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 50,
        include_read: bool = False,
    ) -> list[NotificationEvent]:
        """Return the most recent notifications for (tenant_id, user_id).

        Results are ordered newest-first.  Only unread events are returned
        unless include_read is True.
        """

    def publish(self, event: NotificationEvent) -> None:
        """Persist a new notification event."""

    def mark_read(
        self,
        *,
        tenant_id: str,
        user_id: str,
        notification_id: str,
    ) -> NotificationEvent | None:
        """Mark a notification as read.

        Returns the updated event, or None if not found or not owned by the
        (tenant_id, user_id) pair.
        """


# ──────────────────────────────────────────────────────────────────────────────
# In-memory implementation (development / single-process)
# ──────────────────────────────────────────────────────────────────────────────


class InMemoryNotificationStore:
    """Thread-safe in-memory notification store.

    Not suitable for multi-replica deployments: events are not shared across
    processes.  Used in development and tests.
    """

    def __init__(self) -> None:
        # Key: (tenant_id, user_id) → ordered list (oldest first)
        self._events: dict[tuple[str, str], list[NotificationEvent]] = {}
        self._lock = threading.Lock()

    def list_for_user(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 50,
        include_read: bool = False,
    ) -> list[NotificationEvent]:
        with self._lock:
            events = self._events.get((tenant_id, user_id), [])
            if not include_read:
                events = [e for e in events if e.read_at is None]
            return list(reversed(events[-limit:]))

    def publish(self, event: NotificationEvent) -> None:
        key = (event.tenant_id, event.user_id)
        with self._lock:
            bucket = self._events.setdefault(key, [])
            bucket.append(event)
            if len(bucket) > _MAX_NOTIFICATIONS_PER_USER:
                self._events[key] = bucket[-_MAX_NOTIFICATIONS_PER_USER:]

    def mark_read(
        self,
        *,
        tenant_id: str,
        user_id: str,
        notification_id: str,
    ) -> NotificationEvent | None:
        key = (tenant_id, user_id)
        with self._lock:
            bucket = self._events.get(key, [])
            for i, event in enumerate(bucket):
                if event.id == notification_id and event.read_at is None:
                    updated = NotificationEvent(
                        id=event.id,
                        tenant_id=event.tenant_id,
                        user_id=event.user_id,
                        event_class=event.event_class,
                        resource_id=event.resource_id,
                        created_at=event.created_at,
                        read_at=_now_utc(),
                    )
                    bucket[i] = updated
                    return updated
        return None


# ──────────────────────────────────────────────────────────────────────────────
# PostgreSQL implementation (production — requires M3 RDS)
# ──────────────────────────────────────────────────────────────────────────────


class PostgresNotificationStore:
    """Production notification store backed by the M3 PostgreSQL instance.

    Requires the notifications table from schema migration 0002.
    """

    def __init__(self, pool: object) -> None:
        self._pool = pool

    def list_for_user(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 50,
        include_read: bool = False,
    ) -> list[NotificationEvent]:
        read_clause = "" if include_read else "AND read_at IS NULL"
        sql = f"""
            SELECT id, tenant_id, user_id, event_class, resource_id,
                   created_at, read_at
            FROM notifications
            WHERE tenant_id = %s AND user_id = %s {read_clause}
            ORDER BY created_at DESC
            LIMIT %s
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id, user_id, limit))
                rows = cur.fetchall()

        return [
            NotificationEvent(
                id=str(row[0]),
                tenant_id=row[1],
                user_id=row[2],
                event_class=row[3],
                resource_id=row[4],
                created_at=row[5],
                read_at=row[6],
            )
            for row in rows
        ]

    def publish(self, event: NotificationEvent) -> None:
        sql = """
            INSERT INTO notifications
                (id, tenant_id, user_id, event_class, resource_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        event.id,
                        event.tenant_id,
                        event.user_id,
                        event.event_class,
                        event.resource_id,
                        event.created_at,
                    ),
                )
            conn.commit()

    def mark_read(
        self,
        *,
        tenant_id: str,
        user_id: str,
        notification_id: str,
    ) -> NotificationEvent | None:
        sql = """
            UPDATE notifications
            SET read_at = NOW()
            WHERE id = %s AND tenant_id = %s AND user_id = %s
                AND read_at IS NULL
            RETURNING id, tenant_id, user_id, event_class, resource_id,
                      created_at, read_at
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (notification_id, tenant_id, user_id))
                row = cur.fetchone()
            conn.commit()

        if row is None:
            return None
        return NotificationEvent(
            id=str(row[0]),
            tenant_id=row[1],
            user_id=row[2],
            event_class=row[3],
            resource_id=row[4],
            created_at=row[5],
            read_at=row[6],
        )


# ──────────────────────────────────────────────────────────────────────────────
# Auth helpers shared across notification handlers
# ──────────────────────────────────────────────────────────────────────────────

# Import the Response dataclass from server at runtime to avoid circular
# imports; type annotations use a string forward reference.
from http import HTTPStatus as _HTTP

_UNAUTH_MISSING = (
    _HTTP.UNAUTHORIZED,
    {"error": "unauthorized", "detail": "Bearer token required."},
)
_UNAUTH_INVALID = (
    _HTTP.UNAUTHORIZED,
    {"error": "unauthorized", "detail": "Invalid or expired token."},
)
_AUTH_NOT_CONFIGURED = (
    _HTTP.SERVICE_UNAVAILABLE,
    {"error": "auth_not_configured",
     "detail": "Authentication is not configured on this instance.",
     "status": "degraded"},
)


def _verify_and_extract(
    authorization: str | None,
    token_verifier: TokenVerifier | None,
) -> tuple[TokenClaims | None, tuple | None]:
    """Verify the bearer token and return (claims, None) or (None, error_tuple)."""
    if not authorization:
        return None, _UNAUTH_MISSING
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None, _UNAUTH_MISSING
    raw_token = parts[1].strip()
    if not raw_token:
        return None, _UNAUTH_MISSING

    if token_verifier is None:
        return None, _AUTH_NOT_CONFIGURED

    try:
        claims = token_verifier.verify(raw_token)
        return claims, None
    except TokenVerificationError:
        return None, _UNAUTH_INVALID


# ──────────────────────────────────────────────────────────────────────────────
# Pure handler functions
# ──────────────────────────────────────────────────────────────────────────────


# ── Real-time push (Server-Sent Events) ──────────────────────────────────────

_STREAM_DEFAULT_LIMIT = 50


def format_notification_sse(event: NotificationEvent) -> bytes:
    """One SSE `data:` frame for a notification — shape only, never content."""
    return b"data: " + json.dumps(event.to_api_dict()).encode("utf-8") + b"\n\n"


def stream_notification_frames(
    store: NotificationStore,
    *,
    tenant_id: str,
    user_id: str,
    max_ticks: int,
    sleep,
    seen=None,
    limit: int = _STREAM_DEFAULT_LIMIT,
):
    """Yield SSE byte frames of a user's unread notifications as they appear.

    Server-side polling of the store, presented to the client as a single push
    stream: the client holds one connection instead of polling every 30s. Each
    tick emits any not-yet-seen unread events (dedup by id, oldest-first) then a
    heartbeat comment so a dead peer is detected promptly. Bounded to `max_ticks`
    iterations — the client reconnects when the stream closes. Content-safe: a
    frame carries only id / event_class / resource_id / timestamps, never any
    prompt, completion, or user content (same guarantee as the list endpoint).
    """
    emitted = set(seen or ())
    for _ in range(max_ticks):
        events = store.list_for_user(tenant_id=tenant_id, user_id=user_id, limit=limit)
        for event in reversed(events):  # store is newest-first; emit chronologically
            if event.id not in emitted:
                emitted.add(event.id)
                yield format_notification_sse(event)
        yield b": ping\n\n"
        sleep()


def build_notifications_list_response(
    *,
    authorization: str | None,
    token_verifier: TokenVerifier | None,
    store: NotificationStore,
    include_read: bool = False,
    limit: int = 50,
) -> tuple[int, dict]:
    """Handle GET /v1/notifications.

    Returns a JSON payload with the caller's unread (or all) notifications.
    Isolation: only events owned by (tenant_id, user_id) are returned.
    """
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err

    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]

    limit = min(max(1, limit), 100)
    events = store.list_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
        include_read=include_read,
    )

    return _HTTP.OK, {
        "notifications": [e.to_api_dict() for e in events],
        "count": len(events),
    }


def build_notification_publish_response(
    *,
    authorization: str | None,
    body: bytes,
    token_verifier: TokenVerifier | None,
    store: NotificationStore,
) -> tuple[int, dict]:
    """Handle POST /v1/notifications.

    Publishes a new notification for the authenticated user's (tenant, subject).
    The publisher scopes the event to their own identity — cross-user publishing
    is not permitted through this endpoint.
    """
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return _HTTP.BAD_REQUEST, {"error": "bad_request", "detail": "Body is not valid JSON."}

    if not isinstance(data, dict):
        return _HTTP.BAD_REQUEST, {"error": "bad_request", "detail": "Body must be a JSON object."}

    event_class = data.get("event_class", "")
    if event_class not in ALLOWED_EVENT_CLASSES:
        return _HTTP.UNPROCESSABLE_ENTITY, {
            "error": "invalid_event_class",
            "detail": f"event_class must be one of: {sorted(ALLOWED_EVENT_CLASSES)}",
        }

    resource_id = data.get("resource_id", "")
    if not isinstance(resource_id, str) or not resource_id.strip():
        return _HTTP.BAD_REQUEST, {
            "error": "bad_request",
            "detail": "'resource_id' is required and must be a non-empty string.",
        }
    if len(resource_id) > _MAX_RESOURCE_ID_LEN:
        return _HTTP.BAD_REQUEST, {
            "error": "bad_request",
            "detail": f"'resource_id' must not exceed {_MAX_RESOURCE_ID_LEN} characters.",
        }

    # Enforce: events carry no prompt/completion content.
    if "content" in data or "prompt" in data or "completion" in data:
        return _HTTP.BAD_REQUEST, {
            "error": "policy_violation",
            "detail": "Notification events must not carry prompt or completion content.",
        }

    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]

    event = NotificationEvent(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        user_id=user_id,
        event_class=event_class,
        resource_id=resource_id.strip(),
        created_at=_now_utc(),
    )
    store.publish(event)

    return _HTTP.CREATED, event.to_api_dict()


def build_notification_read_response(
    *,
    authorization: str | None,
    notification_id: str,
    token_verifier: TokenVerifier | None,
    store: NotificationStore,
) -> tuple[int, dict]:
    """Handle POST /v1/notifications/{id}/read.

    Marks a notification as read.  Returns 404 if the notification does not
    exist or is not owned by the authenticated user (prevents enumeration).
    """
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err

    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]

    updated = store.mark_read(
        tenant_id=tenant_id,
        user_id=user_id,
        notification_id=notification_id,
    )

    if updated is None:
        return _HTTP.NOT_FOUND, {
            "error": "not_found",
            "detail": "Notification not found or already read.",
        }

    return _HTTP.OK, updated.to_api_dict()
