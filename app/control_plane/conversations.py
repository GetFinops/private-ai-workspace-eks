"""Server-side conversation persistence (Tier A, pre-production gap plan).

Stores chat threads per (tenant_id, user_id) so history survives tab close /
device switch — the M9 UI kept everything in sessionStorage. Mirrors the M10
memory store pattern: InMemory + Postgres backends behind a Protocol, strict
per-tenant + per-user isolation on every operation, and authoritative delete.

Message content is the user's OWN data, returned only to its owner — consistent
with the memory store. The M5 content policy governs LOGS/telemetry (never carry
content), NOT this owner-scoped store.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from typing import Protocol
from uuid import UUID, uuid4

from app.control_plane.notifications import (
    _extract_tenant_id,
    _now_utc,
    _verify_and_extract,
)
from app.control_plane.token_verifier import TokenVerifier

logger = logging.getLogger(__name__)

_MAX_TITLE_LEN = 200
_MAX_CONTENT_LEN = 100_000        # per message
_MAX_MESSAGES_PER_CONVERSATION = 2_000
_VALID_ROLES = frozenset({"user", "assistant", "system"})


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Message:
    id: str
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class Conversation:
    id: str
    tenant_id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list = field(default_factory=list)  # list[Message], empty in list views

    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def detail(self) -> dict:
        return {
            **self.summary(),
            "messages": [
                {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
                for m in self.messages
            ],
        }


class ConversationStore(Protocol):
    def create(self, *, tenant_id: str, user_id: str, title: str) -> Conversation:
        ...

    def list_for_user(self, *, tenant_id: str, user_id: str, limit: int = 100) -> list[Conversation]:
        ...

    def get(self, *, tenant_id: str, user_id: str, conversation_id: str) -> Conversation | None:
        ...

    def delete(self, *, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        ...

    def append(self, *, tenant_id: str, user_id: str, conversation_id: str, role: str, content: str) -> Message | None:
        ...


# ── In-memory backend ─────────────────────────────────────────────────────────


class InMemoryConversationStore:
    def __init__(self) -> None:
        # conversation_id -> (Conversation, list[Message])
        self._convs: dict[str, tuple[Conversation, list[Message]]] = {}

    def create(self, *, tenant_id: str, user_id: str, title: str) -> Conversation:
        now = _now_utc()
        conv = Conversation(
            id=str(uuid4()), tenant_id=tenant_id, user_id=user_id,
            title=title[:_MAX_TITLE_LEN], created_at=now, updated_at=now,
        )
        self._convs[conv.id] = (conv, [])
        return conv

    def _owned(self, conversation_id, tenant_id, user_id):
        entry = self._convs.get(conversation_id)
        if entry is None:
            return None
        conv, _ = entry
        if conv.tenant_id != tenant_id or conv.user_id != user_id:
            return None
        return entry

    def list_for_user(self, *, tenant_id, user_id, limit=100):
        convs = [c for (c, _) in self._convs.values() if c.tenant_id == tenant_id and c.user_id == user_id]
        convs.sort(key=lambda c: c.updated_at, reverse=True)
        return convs[:limit]

    def get(self, *, tenant_id, user_id, conversation_id):
        entry = self._owned(conversation_id, tenant_id, user_id)
        if entry is None:
            return None
        conv, msgs = entry
        return Conversation(conv.id, conv.tenant_id, conv.user_id, conv.title,
                            conv.created_at, conv.updated_at, list(msgs))

    def delete(self, *, tenant_id, user_id, conversation_id):
        if self._owned(conversation_id, tenant_id, user_id) is None:
            return False
        del self._convs[conversation_id]
        return True

    def append(self, *, tenant_id, user_id, conversation_id, role, content):
        entry = self._owned(conversation_id, tenant_id, user_id)
        if entry is None:
            return None
        conv, msgs = entry
        if len(msgs) >= _MAX_MESSAGES_PER_CONVERSATION:
            msgs.pop(0)
        msg = Message(id=str(uuid4()), role=role, content=content[:_MAX_CONTENT_LEN], created_at=_now_utc())
        msgs.append(msg)
        # First user message seeds the title; bump updated_at.
        title = conv.title
        if conv.title == "New conversation" and role == "user":
            title = content[:_MAX_TITLE_LEN].strip() or conv.title
        self._convs[conversation_id] = (
            Conversation(conv.id, conv.tenant_id, conv.user_id, title, conv.created_at, msg.created_at),
            msgs,
        )
        return msg


# ── Postgres backend ──────────────────────────────────────────────────────────


class PostgresConversationStore:
    def __init__(self, pool: object) -> None:
        self._pool = pool

    def create(self, *, tenant_id, user_id, title):
        now = _now_utc()
        conv = Conversation(str(uuid4()), tenant_id, user_id, title[:_MAX_TITLE_LEN], now, now)
        with self._pool.connection() as conn:  # type: ignore[union-attr]
            conn.execute(
                "INSERT INTO conversations (id, tenant_id, user_id, title, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (conv.id, tenant_id, user_id, conv.title, now, now),
            )
            conn.commit()
        return conv

    def list_for_user(self, *, tenant_id, user_id, limit=100):
        with self._pool.connection() as conn:  # type: ignore[union-attr]
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations "
                "WHERE tenant_id = %s AND user_id = %s ORDER BY updated_at DESC LIMIT %s",
                (tenant_id, user_id, limit),
            ).fetchall()
        return [Conversation(_uid(r[0]), tenant_id, user_id, r[1], r[2], r[3]) for r in rows]

    def get(self, *, tenant_id, user_id, conversation_id):
        with self._pool.connection() as conn:  # type: ignore[union-attr]
            row = conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations "
                "WHERE id = %s AND tenant_id = %s AND user_id = %s",
                (conversation_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                return None
            mrows = conn.execute(
                "SELECT id, role, content, created_at FROM conversation_messages "
                "WHERE conversation_id = %s AND tenant_id = %s ORDER BY created_at ASC",
                (conversation_id, tenant_id),
            ).fetchall()
        msgs = [Message(_uid(m[0]), m[1], m[2], m[3]) for m in mrows]
        return Conversation(_uid(row[0]), tenant_id, user_id, row[1], row[2], row[3], msgs)

    def delete(self, *, tenant_id, user_id, conversation_id):
        with self._pool.connection() as conn:  # type: ignore[union-attr]
            cur = conn.execute(
                "DELETE FROM conversations WHERE id = %s AND tenant_id = %s AND user_id = %s",
                (conversation_id, tenant_id, user_id),
            )
            conn.commit()
            return (cur.rowcount or 0) > 0

    def append(self, *, tenant_id, user_id, conversation_id, role, content):
        now = _now_utc()
        with self._pool.connection() as conn:  # type: ignore[union-attr]
            owner = conn.execute(
                "SELECT title FROM conversations WHERE id = %s AND tenant_id = %s AND user_id = %s",
                (conversation_id, tenant_id, user_id),
            ).fetchone()
            if owner is None:
                return None
            msg = Message(str(uuid4()), role, content[:_MAX_CONTENT_LEN], now)
            conn.execute(
                "INSERT INTO conversation_messages (id, conversation_id, tenant_id, user_id, role, content, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (msg.id, conversation_id, tenant_id, user_id, role, msg.content, now),
            )
            title = owner[0]
            if title == "New conversation" and role == "user":
                title = content[:_MAX_TITLE_LEN].strip() or title
            conn.execute(
                "UPDATE conversations SET updated_at = %s, title = %s WHERE id = %s",
                (now, title, conversation_id),
            )
            conn.commit()
        return msg


def _uid(v) -> str:
    return str(v) if isinstance(v, (UUID, str)) else str(v)


# ── HTTP handlers ─────────────────────────────────────────────────────────────


def _auth(authorization, token_verifier):
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return None, None, err
    return _extract_tenant_id(claims), claims.subject, None


def build_conversation_create_response(*, authorization, body, token_verifier, store):
    tenant_id, user_id, err = _auth(authorization, token_verifier)
    if err is not None:
        return err
    title = "New conversation"
    try:
        import json
        data = json.loads(body) if body else {}
        if isinstance(data, dict) and isinstance(data.get("title"), str) and data["title"].strip():
            title = data["title"].strip()
    except (ValueError, Exception):  # noqa: BLE001
        pass
    conv = store.create(tenant_id=tenant_id, user_id=user_id, title=title)
    return HTTPStatus.CREATED, conv.summary()


def build_conversations_list_response(*, authorization, token_verifier, store, limit=100):
    tenant_id, user_id, err = _auth(authorization, token_verifier)
    if err is not None:
        return err
    convs = store.list_for_user(tenant_id=tenant_id, user_id=user_id, limit=limit)
    return HTTPStatus.OK, {"conversations": [c.summary() for c in convs]}


def build_conversation_get_response(*, authorization, conversation_id, token_verifier, store):
    tenant_id, user_id, err = _auth(authorization, token_verifier)
    if err is not None:
        return err
    conv = store.get(tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id)
    if conv is None:
        return HTTPStatus.NOT_FOUND, {"error": "not_found"}
    return HTTPStatus.OK, conv.detail()


def build_conversation_delete_response(*, authorization, conversation_id, token_verifier, store):
    tenant_id, user_id, err = _auth(authorization, token_verifier)
    if err is not None:
        return err
    ok = store.delete(tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id)
    if not ok:
        return HTTPStatus.NOT_FOUND, {"error": "not_found"}
    return HTTPStatus.OK, {"status": "deleted", "id": conversation_id}


def build_conversation_append_response(*, authorization, conversation_id, body, token_verifier, store):
    tenant_id, user_id, err = _auth(authorization, token_verifier)
    if err is not None:
        return err
    try:
        import json
        data = json.loads(body)
    except (ValueError, Exception):  # noqa: BLE001
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    role = data.get("role")
    content = data.get("content")
    if role not in _VALID_ROLES:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "role must be user|assistant|system"}
    if not isinstance(content, str) or not content:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'content' is required."}
    if len(content) > _MAX_CONTENT_LEN:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "content_too_large"}
    msg = store.append(tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id,
                       role=role, content=content)
    if msg is None:
        return HTTPStatus.NOT_FOUND, {"error": "not_found"}
    return HTTPStatus.CREATED, {"id": msg.id, "role": msg.role, "created_at": msg.created_at.isoformat()}
