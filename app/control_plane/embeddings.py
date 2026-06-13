"""Embedding generation for retrieval (M10).

The control plane turns text into fixed-dimension vectors that the retrieval
store indexes and queries. Two implementations are provided:

DeterministicEmbeddingClient (development / tests)
    A dependency-free, CPU-only embedding derived from token feature-hashing
    and L2-normalised. It is deterministic (same text → same vector) and
    captures lexical overlap well enough to drive retrieval in dev and unit
    tests, with no GPU and no model download.

InferenceEmbeddingClient (production)
    Computes embeddings in-cluster via an OpenAI-compatible /v1/embeddings
    endpoint — vLLM serving an embedding model, or a dedicated embedding
    deployment. Selected when EMBEDDING_BASE_URL is configured. Per the Phase 2
    adoption rules, embeddings are computed in-cluster; routing them through an
    external provider is an explicit escalation trigger and is not supported.

Content policy (M5): embedding inputs are user content. Never log the text or
the vectors; only counts and dimensions may appear in telemetry.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.request
from typing import Protocol, runtime_checkable

from app.control_plane import metrics

# Embedding dimension. Must match the vector(N) column in
# app/db/schema.sql (document_chunks.embedding); changing it requires a
# database migration.
EMBEDDING_DIM = 384

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Default timeout (seconds) for a single embedding HTTP request.
_DEFAULT_EMBEDDING_TIMEOUT = 10.0


class EmbeddingError(Exception):
    """Raised when an embedding backend request fails or returns bad data."""


def embed_measured(client: "EmbeddingClient", texts: list[str]) -> list[list[float]]:
    """Embed *texts*, recording embedding throughput + latency (M10 metrics).

    Re-raises EmbeddingError after counting the failure so callers can degrade.
    """
    start = time.monotonic()
    try:
        vectors = client.embed(texts)
    except EmbeddingError:
        metrics.EMBEDDINGS_GENERATED_TOTAL.labels(status="error").inc()
        raise
    metrics.EMBEDDING_DURATION_SECONDS.observe(time.monotonic() - start)
    metrics.EMBEDDINGS_GENERATED_TOTAL.labels(status="success").inc(len(texts))
    return vectors


@runtime_checkable
class EmbeddingClient(Protocol):
    """Turns text into fixed-dimension embedding vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one EMBEDDING_DIM-length vector per input text (same order)."""


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class DeterministicEmbeddingClient:
    """CPU-only, dependency-free embedding via token feature-hashing.

    Each token is hashed into a bucket in [0, EMBEDDING_DIM) with a signed
    contribution; the resulting vector is L2-normalised. Lexically similar
    texts produce vectors with high cosine similarity, which is sufficient for
    development and deterministic tests. Not a substitute for a real embedding
    model in production.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


class InferenceEmbeddingClient:
    """Computes embeddings via an in-cluster OpenAI-compatible /v1/embeddings API.

    Compatible with vLLM serving an embedding model, or a dedicated in-cluster
    embedding deployment. Embeddings are computed in-cluster — routing them
    through an external provider is an explicit escalation trigger and is not
    supported here. The backend must return EMBEDDING_DIM-length vectors; a
    mismatch raises EmbeddingError so a misconfigured model surfaces at once
    rather than silently corrupting the index.

    Uses stdlib urllib (no httpx/openai SDK), mirroring app/control_plane/
    inference.py's request/timeout/error policy.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dim: int = EMBEDDING_DIM,
        timeout_seconds: float = _DEFAULT_EMBEDDING_TIMEOUT,
    ) -> None:
        self._url = base_url.rstrip("/") + "/v1/embeddings"
        self._model = model
        self._dim = dim
        self._timeout = timeout_seconds

    def embed(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self._model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise EmbeddingError(f"embedding backend returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EmbeddingError(f"embedding backend unreachable: {exc}") from None

        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) != len(texts):
            raise EmbeddingError("embedding backend returned an unexpected response shape")

        # The OpenAI contract does not guarantee input order; sort by index.
        vectors: list[list[float]] = []
        for item in sorted(items, key=lambda d: d.get("index", 0)):
            vec = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vec, list) or len(vec) != self._dim:
                raise EmbeddingError(
                    f"embedding dimension mismatch: backend returned "
                    f"{len(vec) if isinstance(vec, list) else 'non-list'}, expected {self._dim}"
                )
            vectors.append([float(x) for x in vec])
        return vectors
