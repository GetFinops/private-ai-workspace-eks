"""Embedding generation for retrieval (M10).

The control plane turns text into fixed-dimension vectors that the retrieval
store indexes and queries. Two implementations are provided:

DeterministicEmbeddingClient (development / tests)
    A dependency-free, CPU-only embedding derived from token feature-hashing
    and L2-normalised. It is deterministic (same text → same vector) and
    captures lexical overlap well enough to drive retrieval in dev and unit
    tests, with no GPU and no model download.

InferenceEmbeddingClient (production — wired in a follow-up)
    Computes embeddings in-cluster via a dedicated embedding deployment or the
    vLLM inference plane (M4). Per the Phase 2 adoption rules, embeddings are
    computed in-cluster; routing them through an external provider is an
    explicit escalation trigger and is not done by default.

Content policy (M5): embedding inputs are user content. Never log the text or
the vectors; only counts and dimensions may appear in telemetry.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

# Embedding dimension. Must match the vector(N) column in
# app/db/schema.sql (document_chunks.embedding); changing it requires a
# database migration.
EMBEDDING_DIM = 384

_TOKEN_RE = re.compile(r"[a-z0-9]+")


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
