"""Tenant-isolated document retrieval (M10).

Provides:
  - Document / DocumentChunk / RetrievedPassage: immutable data models
  - RetrievalStore: structural protocol (index/query interface)
  - InMemoryRetrievalStore: single-process development implementation
  - PostgresRetrievalStore: production implementation on pgvector (M3 RDS)

Pure handler functions (no HTTP plumbing; testable without a server):
  - build_index_document_response(...)
  - build_retrieval_query_response(...)

Isolation invariant:
  Every index and query is scoped to a tenant_id derived from the caller's
  verified token. Cross-tenant retrieval is prevented at the store layer
  (queries always filter by tenant_id); the API layer derives the tenant and
  never accepts it from the client.

Content policy (M5): document text and queries are user content. Never log
them or include them in telemetry — only counts, ids, and dimensions.

Provenance:
  Chunking and similarity-ranking *patterns* are reused from common retrieval
  designs; no third-party retrieval library is vendored. Vector storage uses
  pgvector (PostgreSQL License, permissive) — recorded in NOTICE.
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
    ALLOWED_EVENT_CLASSES,
    NotificationEvent,
    NotificationStore,
    _extract_tenant_id,
    _now_utc,
    _verify_and_extract,
)
from app.control_plane.token_verifier import TokenVerifier

# ──────────────────────────────────────────────────────────────────────────────
# Size / rate limits (server-side; UI limits are defence-in-depth only)
# ──────────────────────────────────────────────────────────────────────────────

_MAX_TITLE_LEN = 256
_MAX_DOCUMENT_CHARS = 100_000   # ~100 KB of text per index call
_MAX_QUERY_CHARS = 2_000
_CHUNK_CHARS = 512              # target characters per chunk
_MAX_CHUNKS_PER_DOCUMENT = 400  # bounds embedding work per index call
_DEFAULT_TOP_K = 5
_MAX_TOP_K = 20

_INDEXING_EVENT_CLASS = "indexing_complete"  # must be in ALLOWED_EVENT_CLASSES


# ──────────────────────────────────────────────────────────────────────────────
# Data models
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Document:
    """An indexed source document, scoped to one tenant."""

    id: str
    tenant_id: str
    user_id: str
    title: str
    created_at: datetime.datetime


@dataclass(frozen=True)
class DocumentChunk:
    """A chunk of a document plus its embedding vector."""

    id: str
    document_id: str
    tenant_id: str
    chunk_index: int
    content: str
    embedding: list[float]
    created_at: datetime.datetime


@dataclass(frozen=True)
class RetrievedPassage:
    """A retrieval result: a chunk with its similarity score."""

    document_id: str
    chunk_id: str
    chunk_index: int
    content: str
    score: float

    def to_api_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "score": round(self.score, 6),
        }


def chunk_text(content: str, *, chunk_chars: int = _CHUNK_CHARS) -> list[str]:
    """Split text into chunks of ~chunk_chars on whitespace boundaries.

    Greedy word packing keeps whole words together; blank input yields no
    chunks. Deterministic so indexing is reproducible.
    """
    words = content.split()
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        added = len(word) + (1 if current else 0)
        if current and length + added > chunk_chars:
            chunks.append(" ".join(current))
            current = [word]
            length = len(word)
        else:
            current.append(word)
            length += added
    if current:
        chunks.append(" ".join(current))
    return chunks


def _cosine(a: list[float], b: list[float]) -> float:
    # Embeddings are L2-normalised, so the dot product is the cosine similarity.
    return sum(x * y for x, y in zip(a, b))


# ──────────────────────────────────────────────────────────────────────────────
# Store protocol
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class RetrievalStore(Protocol):
    """Index/query interface for tenant-isolated document retrieval."""

    def index_document(self, document: Document, chunks: list[DocumentChunk]) -> None:
        """Persist a document and its embedded chunks."""

    def query(
        self, *, tenant_id: str, embedding: list[float], top_k: int
    ) -> list[RetrievedPassage]:
        """Return the top_k most similar chunks within tenant_id, best first."""


# ──────────────────────────────────────────────────────────────────────────────
# In-memory implementation (development / single-process)
# ──────────────────────────────────────────────────────────────────────────────


class InMemoryRetrievalStore:
    """Thread-safe in-memory retrieval store (development / tests).

    Not suitable for multi-replica deployments: the index is not shared across
    processes.
    """

    def __init__(self) -> None:
        # tenant_id → list of chunks
        self._chunks: dict[str, list[DocumentChunk]] = {}
        self._lock = threading.Lock()

    def index_document(self, document: Document, chunks: list[DocumentChunk]) -> None:
        with self._lock:
            bucket = self._chunks.setdefault(document.tenant_id, [])
            bucket.extend(chunks)

    def query(
        self, *, tenant_id: str, embedding: list[float], top_k: int
    ) -> list[RetrievedPassage]:
        with self._lock:
            # Isolation: only this tenant's chunks are ever considered.
            candidates = list(self._chunks.get(tenant_id, []))
        scored = [
            RetrievedPassage(
                document_id=c.document_id,
                chunk_id=c.id,
                chunk_index=c.chunk_index,
                content=c.content,
                score=_cosine(embedding, c.embedding),
            )
            for c in candidates
        ]
        scored.sort(key=lambda p: p.score, reverse=True)
        return scored[:top_k]


# ──────────────────────────────────────────────────────────────────────────────
# PostgreSQL implementation (production — requires M3 RDS + pgvector)
# ──────────────────────────────────────────────────────────────────────────────


class PostgresRetrievalStore:
    """Production retrieval store backed by pgvector on the M3 PostgreSQL.

    Requires the documents/document_chunks tables from schema migration 0003.
    """

    def __init__(self, pool: object) -> None:
        self._pool = pool

    @staticmethod
    def _vec_literal(embedding: list[float]) -> str:
        # pgvector accepts a bracketed text literal cast to ::vector.
        return "[" + ",".join(repr(float(x)) for x in embedding) + "]"

    def index_document(self, document: Document, chunks: list[DocumentChunk]) -> None:
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents (id, tenant_id, user_id, title, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        document.id,
                        document.tenant_id,
                        document.user_id,
                        document.title,
                        document.created_at,
                    ),
                )
                for c in chunks:
                    cur.execute(
                        """
                        INSERT INTO document_chunks
                            (id, document_id, tenant_id, chunk_index, content,
                             embedding, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            c.id,
                            c.document_id,
                            c.tenant_id,
                            c.chunk_index,
                            c.content,
                            self._vec_literal(c.embedding),
                            c.created_at,
                        ),
                    )
            conn.commit()

    def query(
        self, *, tenant_id: str, embedding: list[float], top_k: int
    ) -> list[RetrievedPassage]:
        # Cosine distance operator (<=>); similarity = 1 - distance.
        # The WHERE clause enforces tenant isolation at the storage layer.
        sql = """
            SELECT document_id, id, chunk_index, content,
                   1 - (embedding <=> %s::vector) AS score
            FROM document_chunks
            WHERE tenant_id = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        vec = self._vec_literal(embedding)
        with self._pool.connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(sql, (vec, tenant_id, vec, top_k))
                rows = cur.fetchall()
        return [
            RetrievedPassage(
                document_id=str(row[0]),
                chunk_id=str(row[1]),
                chunk_index=row[2],
                content=row[3],
                score=float(row[4]),
            )
            for row in rows
        ]


# ──────────────────────────────────────────────────────────────────────────────
# Pure handler functions
# ──────────────────────────────────────────────────────────────────────────────


def build_index_document_response(
    *,
    authorization: str | None,
    body: bytes,
    token_verifier: TokenVerifier | None,
    store: RetrievalStore,
    embedding_client: EmbeddingClient,
    notification_store: NotificationStore | None = None,
) -> tuple[int, dict]:
    """Handle POST /v1/retrieval/documents.

    Indexes {title, content} for the caller's tenant. The tenant is derived
    from the verified token and is never accepted from the client. Emits an
    `indexing_complete` notification to the caller when a notification store is
    configured.
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

    title = data.get("title", "")
    if not isinstance(title, str) or not title.strip():
        return HTTPStatus.BAD_REQUEST, {
            "error": "bad_request",
            "detail": "'title' is required and must be a non-empty string.",
        }
    if len(title) > _MAX_TITLE_LEN:
        return HTTPStatus.BAD_REQUEST, {
            "error": "bad_request",
            "detail": f"'title' must not exceed {_MAX_TITLE_LEN} characters.",
        }

    content = data.get("content", "")
    if not isinstance(content, str) or not content.strip():
        return HTTPStatus.BAD_REQUEST, {
            "error": "bad_request",
            "detail": "'content' is required and must be a non-empty string.",
        }
    if len(content) > _MAX_DOCUMENT_CHARS:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
            "error": "payload_too_large",
            "detail": f"'content' must not exceed {_MAX_DOCUMENT_CHARS} characters.",
        }

    chunk_contents = chunk_text(content)
    if len(chunk_contents) > _MAX_CHUNKS_PER_DOCUMENT:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
            "error": "payload_too_large",
            "detail": f"document produces more than {_MAX_CHUNKS_PER_DOCUMENT} chunks.",
        }

    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]
    now = _now_utc()
    document_id = str(uuid.uuid4())

    vectors = embedding_client.embed(chunk_contents)
    chunks = [
        DocumentChunk(
            id=str(uuid.uuid4()),
            document_id=document_id,
            tenant_id=tenant_id,
            chunk_index=i,
            content=text,
            embedding=vector,
            created_at=now,
        )
        for i, (text, vector) in enumerate(zip(chunk_contents, vectors))
    ]

    document = Document(
        id=document_id,
        tenant_id=tenant_id,
        user_id=user_id,
        title=title.strip(),
        created_at=now,
    )
    store.index_document(document, chunks)

    # Producer event into the M9 feed (best-effort; never breaks indexing).
    if notification_store is not None and _INDEXING_EVENT_CLASS in ALLOWED_EVENT_CLASSES:
        try:
            notification_store.publish(
                NotificationEvent(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    user_id=user_id,
                    event_class=_INDEXING_EVENT_CLASS,
                    resource_id=document_id,
                    created_at=_now_utc(),
                )
            )
        except Exception:  # pragma: no cover - notification is best-effort
            pass

    return HTTPStatus.CREATED, {
        "document_id": document_id,
        "title": document.title,
        "chunk_count": len(chunks),
    }


def build_retrieval_query_response(
    *,
    authorization: str | None,
    body: bytes,
    token_verifier: TokenVerifier | None,
    store: RetrievalStore,
    embedding_client: EmbeddingClient,
) -> tuple[int, dict]:
    """Handle POST /v1/retrieval/query.

    Returns the top_k chunks most similar to the query, scoped to the caller's
    tenant. Cross-tenant results are impossible: the store query filters by the
    token-derived tenant_id.
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
        return HTTPStatus.BAD_REQUEST, {
            "error": "bad_request",
            "detail": "'top_k' must be an integer.",
        }
    top_k = min(max(1, top_k), _MAX_TOP_K)

    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    embedding = embedding_client.embed([query])[0]
    if len(embedding) != EMBEDDING_DIM:  # pragma: no cover - guard
        return HTTPStatus.INTERNAL_SERVER_ERROR, {
            "error": "internal_error",
            "detail": "embedding dimension mismatch.",
        }

    passages = store.query(tenant_id=tenant_id, embedding=embedding, top_k=top_k)
    return HTTPStatus.OK, {
        "results": [p.to_api_dict() for p in passages],
        "count": len(passages),
    }
