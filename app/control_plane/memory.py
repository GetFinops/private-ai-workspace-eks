"""Per-user long-term memory (M10).

Memory is scoped one level tighter than retrieval: to a single
(tenant_id, user_id). It mirrors the retrieval store shape but adds the
controls the milestone requires:

  - opt-in writes: every record call must carry explicit per-write consent;
    implicit/background capture is excluded by default.
  - user controls: list, recall, and authoritative delete of one's own
    memories. Deletion removes the row (no soft-delete that leaves data
    recoverable without an audit trail).

Isolation invariant:
  Every record/list/recall/delete is scoped to (tenant_id, user_id) derived
  from the verified token. Cross-user recall is impossible at the store layer
  (queries always filter by user_id); the handler never accepts the identity
  from the client.

Content policy (M5): memory text and queries are user content. Never log them
or include them in telemetry — only counts, ids, and dimensions.
"""

from __future__ import annotations

import datetime
import json
import threading
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from typing import Protocol, runtime_checkable

from app.control_plane.embeddings import EMBEDDING_DIM, EmbeddingClient
from app.control_plane.notifications import (
    _extract_tenant_id,
    _now_utc,
    _verify_and_extract,
)
from app.control_plane.retrieval import _cosine
from app.control_plane.token_verifier import TokenVerifier

# ──────────────────────────────────────────────────────────────────────────────
# Size / rate limits (server-side)
# ──────────────────────────────────────────────────────────────────────────────

_MAX_MEMORY_CHARS = 10_000
_MAX_QUERY_CHARS = 2_000
_DEFAULT_TOP_K = 5
_MAX_TOP_K = 20
_MAX_LIST_LIMIT = 200


# ──────────────────────────────────────────────────────────────────────────────
# Data models
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Memory:
    """A single stored memory, owned by exactly one (tenant_id, user_id)."""

    id: str
    tenant_id: str
    user_id: str
    content: str
    created_at: datetime.datetime

    def to_api_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class RecalledMemory:
    """A recall result: a memory with its similarity score."""

    memory: Memory
    score: float

    def to_api_dict(self) -> dict:
        out = self.memory.to_api_dict()
        out["score"] = round(self.score, 6)
        return out


# ──────────────────────────────────────────────────────────────────────────────
# Store protocol
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class MemoryStore(Protocol):
    """Record/list/recall/delete interface for per-user memory."""

    def record(self, memory: Memory, embedding: list[float]) -> None:
        """Persist a memory and its embedding."""

    def list_for_user(
        self, *, tenant_id: str, user_id: str, limit: int = 50
    ) -> list[Memory]:
        """Return the user's memories, newest first."""

    def recall(
        self, *, tenant_id: str, user_id: str, embedding: list[float], top_k: int
    ) -> list[RecalledMemory]:
        """Return the user's top_k most similar memories, best first."""

    def delete(self, *, tenant_id: str, user_id: str, memory_id: str) -> bool:
        """Authoritatively delete a memory. Returns True if a row was removed."""


# ──────────────────────────────────────────────────────────────────────────────
# In-memory implementation (development / single-process)
# ──────────────────────────────────────────────────────────────────────────────


class InMemoryMemoryStore:
    """Thread-safe in-memory memory store (development / tests)."""

    def __init__(self) -> None:
        # (tenant_id, user_id) → list of (Memory, embedding), oldest first
        self._items: dict[tuple[str, str], list[tuple[Memory, list[float]]]] = {}
        self._lock = threading.Lock()

    def record(self, memory: Memory, embedding: list[float]) -> None:
        key = (memory.tenant_id, memory.user_id)
        with self._lock:
            self._items.setdefault(key, []).append((memory, embedding))

    def list_for_user(
        self, *, tenant_id: str, user_id: str, limit: int = 50
    ) -> list[Memory]:
        with self._lock:
            items = self._items.get((tenant_id, user_id), [])
            memories = [m for m, _ in items]
        return list(reversed(memories))[:limit]

    def recall(
        self, *, tenant_id: str, user_id: str, embedding: list[float], top_k: int
    ) -> list[RecalledMemory]:
        with self._lock:
            # Isolation: only this user's memories are ever considered.
            items = list(self._items.get((tenant_id, user_id), []))
        scored = [
            RecalledMemory(memory=m, score=_cosine(embedding, vec))
            for m, vec in items
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]

    def delete(self, *, tenant_id: str, user_id: str, memory_id: str) -> bool:
        key = (tenant_id, user_id)
        with self._lock:
            items = self._items.get(key, [])
            for i, (m, _) in enumerate(items):
                if m.id == memory_id:
                    del items[i]
                    return True
        return False


# ──────────────────────────────────────────────────────────────────────────────
# PostgreSQL implementation (production — requires M3 RDS + pgvector)
# ──────────────────────────────────────────────────────────────────────────────


class PostgresMemoryStore:
    """Production memory store backed by pgvector on the M3 PostgreSQL.

    Requires the memories table from schema migration 0004.
    """

    def __init__(self, pool: object) -> None:
        self._pool = pool

    @staticmethod
    def _vec_literal(embedding: list[float]) -> str:
        return "[" + ",".join(repr(float(x)) for x in embedding) + "]"

    def record(self, memory: Memory, embedding: list[float]) -> None:
        sql = """
            INSERT INTO memories (id, tenant_id, user_id, content, embedding, created_at)
            VALUES (%s, %s, %s, %s, %s::vector, %s)
            ON CONFLICT (id) DO NOTHING
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        memory.id,
                        memory.tenant_id,
                        memory.user_id,
                        memory.content,
                        self._vec_literal(embedding),
                        memory.created_at,
                    ),
                )
            conn.commit()

    def list_for_user(
        self, *, tenant_id: str, user_id: str, limit: int = 50
    ) -> list[Memory]:
        sql = """
            SELECT id, tenant_id, user_id, content, created_at
            FROM memories
            WHERE tenant_id = %s AND user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id, user_id, limit))
                rows = cur.fetchall()
        return [
            Memory(
                id=str(r[0]), tenant_id=r[1], user_id=r[2], content=r[3], created_at=r[4]
            )
            for r in rows
        ]

    def recall(
        self, *, tenant_id: str, user_id: str, embedding: list[float], top_k: int
    ) -> list[RecalledMemory]:
        # WHERE enforces per-user isolation at the storage layer.
        sql = """
            SELECT id, tenant_id, user_id, content, created_at,
                   1 - (embedding <=> %s::vector) AS score
            FROM memories
            WHERE tenant_id = %s AND user_id = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        vec = self._vec_literal(embedding)
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (vec, tenant_id, user_id, vec, top_k))
                rows = cur.fetchall()
        return [
            RecalledMemory(
                memory=Memory(
                    id=str(r[0]), tenant_id=r[1], user_id=r[2], content=r[3], created_at=r[4]
                ),
                score=float(r[5]),
            )
            for r in rows
        ]

    def delete(self, *, tenant_id: str, user_id: str, memory_id: str) -> bool:
        sql = """
            DELETE FROM memories
            WHERE id = %s AND tenant_id = %s AND user_id = %s
            RETURNING id
        """
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (memory_id, tenant_id, user_id))
                row = cur.fetchone()
            conn.commit()
        return row is not None


# ──────────────────────────────────────────────────────────────────────────────
# Pure handler functions
# ──────────────────────────────────────────────────────────────────────────────


def build_memory_record_response(
    *,
    authorization: str | None,
    body: bytes,
    token_verifier: TokenVerifier | None,
    store: MemoryStore,
    embedding_client: EmbeddingClient,
) -> tuple[int, dict]:
    """Handle POST /v1/memory.

    Records a memory for the caller. Writes are opt-in: the body must carry an
    explicit `consent: true` (per-write consent). Implicit capture is rejected.
    """
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "Body is not valid JSON."}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "Body must be a JSON object."}

    if data.get("consent") is not True:
        return HTTPStatus.FORBIDDEN, {
            "error": "consent_required",
            "detail": "Memory writes require explicit 'consent': true; implicit capture is not permitted.",
        }

    content = data.get("content", "")
    if not isinstance(content, str) or not content.strip():
        return HTTPStatus.BAD_REQUEST, {
            "error": "bad_request",
            "detail": "'content' is required and must be a non-empty string.",
        }
    if len(content) > _MAX_MEMORY_CHARS:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
            "error": "payload_too_large",
            "detail": f"'content' must not exceed {_MAX_MEMORY_CHARS} characters.",
        }

    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]
    embedding = embedding_client.embed([content])[0]
    memory = Memory(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        user_id=user_id,
        content=content,
        created_at=_now_utc(),
    )
    store.record(memory, embedding)
    return HTTPStatus.CREATED, memory.to_api_dict()


def build_memory_list_response(
    *,
    authorization: str | None,
    token_verifier: TokenVerifier | None,
    store: MemoryStore,
    limit: int = 50,
) -> tuple[int, dict]:
    """Handle GET /v1/memory — list (and thereby export) the caller's memories."""
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err

    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]
    limit = min(max(1, limit), _MAX_LIST_LIMIT)
    memories = store.list_for_user(tenant_id=tenant_id, user_id=user_id, limit=limit)
    return HTTPStatus.OK, {
        "memories": [m.to_api_dict() for m in memories],
        "count": len(memories),
    }


def build_memory_recall_response(
    *,
    authorization: str | None,
    body: bytes,
    token_verifier: TokenVerifier | None,
    store: MemoryStore,
    embedding_client: EmbeddingClient,
) -> tuple[int, dict]:
    """Handle POST /v1/memory/recall.

    Returns the caller's top_k most similar memories. Cross-user recall is
    impossible: the store filters by the token-derived (tenant_id, user_id).
    """
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "Body is not valid JSON."}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "Body must be a JSON object."}

    query = data.get("query", "")
    if not isinstance(query, str) or not query.strip():
        return HTTPStatus.BAD_REQUEST, {
            "error": "bad_request",
            "detail": "'query' is required and must be a non-empty string.",
        }
    if len(query) > _MAX_QUERY_CHARS:
        return HTTPStatus.BAD_REQUEST, {
            "error": "bad_request",
            "detail": f"'query' must not exceed {_MAX_QUERY_CHARS} characters.",
        }

    top_k = data.get("top_k", _DEFAULT_TOP_K)
    if not isinstance(top_k, int) or isinstance(top_k, bool):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'top_k' must be an integer."}
    top_k = min(max(1, top_k), _MAX_TOP_K)

    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]
    embedding = embedding_client.embed([query])[0]
    if len(embedding) != EMBEDDING_DIM:  # pragma: no cover - guard
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal_error", "detail": "embedding dimension mismatch."}

    results = store.recall(tenant_id=tenant_id, user_id=user_id, embedding=embedding, top_k=top_k)
    return HTTPStatus.OK, {
        "results": [r.to_api_dict() for r in results],
        "count": len(results),
    }


def build_memory_delete_response(
    *,
    authorization: str | None,
    memory_id: str,
    token_verifier: TokenVerifier | None,
    store: MemoryStore,
) -> tuple[int, dict]:
    """Handle DELETE /v1/memory/{id}.

    Authoritative delete. Returns 404 if the memory does not exist or is not
    owned by the caller (prevents enumeration and cross-user deletion).
    """
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err

    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]
    deleted = store.delete(tenant_id=tenant_id, user_id=user_id, memory_id=memory_id)
    if not deleted:
        return HTTPStatus.NOT_FOUND, {"error": "not_found", "detail": "Memory not found."}
    return HTTPStatus.OK, {"id": memory_id, "deleted": True}
