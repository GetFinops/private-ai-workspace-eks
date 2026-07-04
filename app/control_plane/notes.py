"""Notes & Tasks — per-tenant/user store + CRUD, mirroring the M10 memory store.

A note or a task is a small user-owned record (kind in {"note","task"}; tasks add
a `done` flag). Isolation is enforced at the storage layer and re-checked on every
operation by the verified token's (tenant_id, user_id) — a caller can only ever
read, update, or delete their own items. Content policy: title/body are the user's
own content, stored and returned only to the owner, never logged or put in
telemetry (same rule as memory).
"""
from __future__ import annotations

import datetime
import json
import threading
import uuid
from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import Protocol, runtime_checkable

from app.control_plane.notifications import (
    _extract_tenant_id,
    _now_utc,
    _verify_and_extract,
)
from app.control_plane.token_verifier import TokenVerifier

_KINDS = ("note", "task", "doc")  # "doc" backs the Documents editor (wave 2)
_MAX_TITLE_CHARS = 500
_MAX_BODY_CHARS = 20000
_MAX_LIST_LIMIT = 200


@dataclass(frozen=True)
class NoteItem:
    """A note or task owned by exactly one (tenant_id, user_id)."""

    id: str
    tenant_id: str
    user_id: str
    kind: str
    title: str
    body: str
    done: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    def to_api_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "done": self.done,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


# ── Store protocol ────────────────────────────────────────────────────────────


@runtime_checkable
class NotesStore(Protocol):
    def create(self, item: NoteItem) -> None: ...

    def list_for_user(
        self, *, tenant_id: str, user_id: str, kind: str | None = None, limit: int = 100
    ) -> list[NoteItem]: ...

    def update(
        self, *, tenant_id: str, user_id: str, item_id: str,
        title: str | None = None, body: str | None = None, done: bool | None = None,
    ) -> "NoteItem | None": ...

    def delete(self, *, tenant_id: str, user_id: str, item_id: str) -> bool: ...


# ── In-memory implementation (development / tests) ─────────────────────────────


class InMemoryNotesStore:
    def __init__(self) -> None:
        # (tenant_id, user_id) → list[NoteItem], oldest first
        self._items: dict[tuple[str, str], list[NoteItem]] = {}
        self._lock = threading.Lock()

    def create(self, item: NoteItem) -> None:
        with self._lock:
            self._items.setdefault((item.tenant_id, item.user_id), []).append(item)

    def list_for_user(
        self, *, tenant_id: str, user_id: str, kind: str | None = None, limit: int = 100
    ) -> list[NoteItem]:
        with self._lock:
            items = list(self._items.get((tenant_id, user_id), []))
        if kind is not None:
            items = [i for i in items if i.kind == kind]
        return list(reversed(items))[:limit]

    def update(
        self, *, tenant_id: str, user_id: str, item_id: str,
        title: str | None = None, body: str | None = None, done: bool | None = None,
    ) -> "NoteItem | None":
        key = (tenant_id, user_id)  # isolation: only this owner's bucket is touched
        with self._lock:
            items = self._items.get(key, [])
            for idx, it in enumerate(items):
                if it.id == item_id:
                    updated = replace(
                        it,
                        title=it.title if title is None else title,
                        body=it.body if body is None else body,
                        done=it.done if done is None else done,
                        updated_at=_now_utc(),
                    )
                    items[idx] = updated
                    return updated
        return None

    def delete(self, *, tenant_id: str, user_id: str, item_id: str) -> bool:
        key = (tenant_id, user_id)
        with self._lock:
            items = self._items.get(key, [])
            for idx, it in enumerate(items):
                if it.id == item_id:
                    del items[idx]
                    return True
        return False


# ── PostgreSQL implementation (production — requires the notes table) ──────────


class PostgresNotesStore:
    """Production notes store backed by the M3 PostgreSQL (schema: notes table)."""

    def __init__(self, pool: object) -> None:
        self._pool = pool

    def create(self, item: NoteItem) -> None:
        sql = """
            INSERT INTO notes (id, tenant_id, user_id, kind, title, body, done, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (
                    item.id, item.tenant_id, item.user_id, item.kind,
                    item.title, item.body, item.done, item.created_at, item.updated_at,
                ))
            conn.commit()

    def list_for_user(
        self, *, tenant_id: str, user_id: str, kind: str | None = None, limit: int = 100
    ) -> list[NoteItem]:
        # WHERE enforces per-user isolation at the storage layer.
        sql = """
            SELECT id, tenant_id, user_id, kind, title, body, done, created_at, updated_at
            FROM notes
            WHERE tenant_id = %s AND user_id = %s AND (%s IS NULL OR kind = %s)
            ORDER BY created_at DESC
            LIMIT %s
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id, user_id, kind, kind, limit))
                rows = cur.fetchall()
        return [self._row(r) for r in rows]

    def update(
        self, *, tenant_id: str, user_id: str, item_id: str,
        title: str | None = None, body: str | None = None, done: bool | None = None,
    ) -> "NoteItem | None":
        # COALESCE keeps unspecified fields; the WHERE clause is the isolation
        # boundary — an item owned by another (tenant,user) simply isn't matched.
        sql = """
            UPDATE notes
            SET title = COALESCE(%s, title),
                body = COALESCE(%s, body),
                done = COALESCE(%s, done),
                updated_at = %s
            WHERE id = %s AND tenant_id = %s AND user_id = %s
            RETURNING id, tenant_id, user_id, kind, title, body, done, created_at, updated_at
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (title, body, done, _now_utc(), item_id, tenant_id, user_id))
                row = cur.fetchone()
            conn.commit()
        return self._row(row) if row else None

    def delete(self, *, tenant_id: str, user_id: str, item_id: str) -> bool:
        sql = "DELETE FROM notes WHERE id = %s AND tenant_id = %s AND user_id = %s"
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (item_id, tenant_id, user_id))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    @staticmethod
    def _row(r) -> NoteItem:
        return NoteItem(
            id=str(r[0]), tenant_id=r[1], user_id=r[2], kind=r[3], title=r[4],
            body=r[5], done=bool(r[6]), created_at=r[7], updated_at=r[8],
        )


# ── HTTP handlers ─────────────────────────────────────────────────────────────


def _validate_text(value, field, max_chars, *, required):
    if value is None and not required:
        return "", None
    if not isinstance(value, str) or (required and not value.strip()):
        return None, (HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": f"'{field}' is required."})
    if len(value) > max_chars:
        return None, (HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                      {"error": "payload_too_large", "detail": f"'{field}' exceeds {max_chars} characters."})
    return value, None


def build_note_create_response(*, authorization, body, token_verifier: TokenVerifier | None, store: NotesStore):
    """POST /v1/notes — body {"kind": "note"|"task", "title", "body"?, "done"?}."""
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err
    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "Body is not valid JSON."}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    kind = data.get("kind", "note")
    if kind not in _KINDS:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": f"'kind' must be one of {list(_KINDS)}."}
    title, terr = _validate_text(data.get("title"), "title", _MAX_TITLE_CHARS, required=True)
    if terr is not None:
        return terr
    text, berr = _validate_text(data.get("body"), "body", _MAX_BODY_CHARS, required=False)
    if berr is not None:
        return berr
    now = _now_utc()
    item = NoteItem(
        id=str(uuid.uuid4()),
        tenant_id=_extract_tenant_id(claims),  # type: ignore[arg-type]
        user_id=claims.subject,  # type: ignore[union-attr]
        kind=kind, title=title, body=text, done=bool(data.get("done", False)),
        created_at=now, updated_at=now,
    )
    store.create(item)
    return HTTPStatus.CREATED, item.to_api_dict()


def build_notes_list_response(*, authorization, token_verifier: TokenVerifier | None, store: NotesStore,
                              kind: str | None = None, limit: int = 100):
    """GET /v1/notes[?kind=note|task] — list the caller's own items."""
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err
    if kind is not None and kind not in _KINDS:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "invalid 'kind'."}
    limit = min(max(1, limit), _MAX_LIST_LIMIT)
    items = store.list_for_user(
        tenant_id=_extract_tenant_id(claims), user_id=claims.subject, kind=kind, limit=limit,  # type: ignore[arg-type]
    )
    return HTTPStatus.OK, {"notes": [i.to_api_dict() for i in items], "count": len(items)}


def build_note_update_response(*, authorization, item_id, body, token_verifier: TokenVerifier | None, store: NotesStore):
    """POST /v1/notes/{id} — partial update {"title"?, "body"?, "done"?}."""
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err
    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "Body is not valid JSON."}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    title = body_text = None
    if "title" in data:
        title, terr = _validate_text(data.get("title"), "title", _MAX_TITLE_CHARS, required=True)
        if terr is not None:
            return terr
    if "body" in data:
        body_text, berr = _validate_text(data.get("body"), "body", _MAX_BODY_CHARS, required=False)
        if berr is not None:
            return berr
    done = data.get("done")
    if done is not None and not isinstance(done, bool):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'done' must be a boolean."}
    updated = store.update(
        tenant_id=_extract_tenant_id(claims), user_id=claims.subject,  # type: ignore[arg-type]
        item_id=item_id, title=title, body=body_text, done=done,
    )
    if updated is None:
        return HTTPStatus.NOT_FOUND, {"error": "not_found"}
    return HTTPStatus.OK, updated.to_api_dict()


def build_note_delete_response(*, authorization, item_id, token_verifier: TokenVerifier | None, store: NotesStore):
    """DELETE /v1/notes/{id} — authoritative delete of a caller-owned item."""
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err
    ok = store.delete(
        tenant_id=_extract_tenant_id(claims), user_id=claims.subject, item_id=item_id,  # type: ignore[arg-type]
    )
    if not ok:
        return HTTPStatus.NOT_FOUND, {"error": "not_found"}
    return HTTPStatus.OK, {"status": "deleted", "id": item_id}
