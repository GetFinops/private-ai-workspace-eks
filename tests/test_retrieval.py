"""Unit tests for app.control_plane.retrieval and embeddings (M10).

Covers:
  - DeterministicEmbeddingClient: dimension, determinism, normalisation
  - chunk_text: greedy whitespace packing
  - InMemoryRetrievalStore: index, ranked query, tenant isolation
  - build_index_document_response: auth, validation, size limits, notification
  - build_retrieval_query_response: auth, validation, top_k clamp, relevance,
    cross-tenant isolation
"""
import json
import math
import unittest
from unittest import mock

from app.control_plane.embeddings import (
    EMBEDDING_DIM,
    DeterministicEmbeddingClient,
    EmbeddingError,
    InferenceEmbeddingClient,
)
from app.control_plane.notifications import InMemoryNotificationStore
from app.control_plane.retrieval import (
    InMemoryRetrievalStore,
    _MAX_DOCUMENT_CHARS,
    _MAX_QUERY_CHARS,
    _MAX_TOP_K,
    build_index_document_response,
    build_retrieval_query_response,
    chunk_text,
)
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


class _Verifier:
    """Stub verifier that maps the token 'valid' to a configurable principal."""

    def __init__(self, subject="user-a", email="alice@tenant-a.test"):
        self._claims = TokenClaims(subject=subject, email=email)

    def verify(self, raw_token: str) -> TokenClaims:
        if raw_token != "valid":
            raise TokenVerificationError("bad token")
        return self._claims


_AUTH = "Bearer valid"
_EMBED = DeterministicEmbeddingClient()


def _index_body(title="Doc", content="hello world"):
    return json.dumps({"title": title, "content": content}).encode()


def _query_body(query="hello", **extra):
    data = {"query": query}
    data.update(extra)
    return json.dumps(data).encode()


# ──────────────────────────────────────────────────────────────────────────────
# Embeddings
# ──────────────────────────────────────────────────────────────────────────────

class TestEmbeddings(unittest.TestCase):
    def test_dimension(self):
        vecs = _EMBED.embed(["kubernetes pods scaling"])
        self.assertEqual(len(vecs), 1)
        self.assertEqual(len(vecs[0]), EMBEDDING_DIM)

    def test_deterministic(self):
        self.assertEqual(_EMBED.embed(["same text"]), _EMBED.embed(["same text"]))

    def test_normalised(self):
        vec = _EMBED.embed(["several distinct tokens here"])[0]
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in vec)), 1.0, places=5)

    def test_empty_text_is_zero_vector(self):
        vec = _EMBED.embed([""])[0]
        self.assertEqual(len(vec), EMBEDDING_DIM)
        self.assertEqual(sum(abs(v) for v in vec), 0.0)


# ──────────────────────────────────────────────────────────────────────────────
# chunk_text
# ──────────────────────────────────────────────────────────────────────────────

class TestChunking(unittest.TestCase):
    def test_blank_yields_no_chunks(self):
        self.assertEqual(chunk_text("   "), [])

    def test_short_text_one_chunk(self):
        self.assertEqual(chunk_text("a few words"), ["a few words"])

    def test_long_text_splits(self):
        text = " ".join(["word"] * 500)  # ~2500 chars
        chunks = chunk_text(text, chunk_chars=100)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 105 for c in chunks))
        # No content lost.
        self.assertEqual(" ".join(chunks).split(), text.split())


# ──────────────────────────────────────────────────────────────────────────────
# InMemoryRetrievalStore + handlers
# ──────────────────────────────────────────────────────────────────────────────

class TestIndexAndQuery(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryRetrievalStore()

    def test_index_requires_auth(self):
        status, _ = build_index_document_response(
            authorization=None, body=_index_body(), token_verifier=_Verifier(),
            store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 401)

    def test_index_validation(self):
        v = _Verifier()
        # missing title
        status, _ = build_index_document_response(
            authorization=_AUTH, body=json.dumps({"content": "x"}).encode(),
            token_verifier=v, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 400)
        # oversized content
        big = "word " * (_MAX_DOCUMENT_CHARS)
        status, _ = build_index_document_response(
            authorization=_AUTH, body=_index_body(content=big),
            token_verifier=v, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 413)

    def test_index_success_and_notification(self):
        v = _Verifier()
        notes = InMemoryNotificationStore()
        status, payload = build_index_document_response(
            authorization=_AUTH,
            body=_index_body(title="Runbook", content="kubernetes pods autoscaling horizontal"),
            token_verifier=v, store=self.store, embedding_client=_EMBED,
            notification_store=notes,
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["chunk_count"], 1)
        self.assertIn("document_id", payload)
        # indexing_complete event reached the owner's feed.
        feed = notes.list_for_user(tenant_id="tenant-a.test", user_id="user-a")
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0].event_class, "indexing_complete")
        self.assertEqual(feed[0].resource_id, payload["document_id"])

    def test_query_returns_relevant_passage(self):
        v = _Verifier()
        build_index_document_response(
            authorization=_AUTH,
            body=_index_body(title="K8s", content="kubernetes pods autoscaling horizontal"),
            token_verifier=v, store=self.store, embedding_client=_EMBED,
        )
        build_index_document_response(
            authorization=_AUTH,
            body=_index_body(title="DB", content="postgres database nightly backups"),
            token_verifier=v, store=self.store, embedding_client=_EMBED,
        )
        status, payload = build_retrieval_query_response(
            authorization=_AUTH, body=_query_body(query="kubernetes pods"),
            token_verifier=v, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["count"], 1)
        top = payload["results"][0]
        self.assertIn("kubernetes", top["content"])
        self.assertGreater(top["score"], 0.0)

    def test_query_validation_and_top_k_clamp(self):
        v = _Verifier()
        status, _ = build_retrieval_query_response(
            authorization=_AUTH, body=json.dumps({"query": "  "}).encode(),
            token_verifier=v, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 400)
        status, _ = build_retrieval_query_response(
            authorization=_AUTH, body=_query_body(query="x" * (_MAX_QUERY_CHARS + 1)),
            token_verifier=v, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 400)
        # top_k above the cap is clamped, not rejected.
        for _ in range(_MAX_TOP_K + 5):
            build_index_document_response(
                authorization=_AUTH, body=_index_body(content="alpha beta gamma"),
                token_verifier=v, store=self.store, embedding_client=_EMBED,
            )
        status, payload = build_retrieval_query_response(
            authorization=_AUTH, body=_query_body(query="alpha", top_k=999),
            token_verifier=v, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 200)
        self.assertLessEqual(payload["count"], _MAX_TOP_K)

    def test_cross_tenant_isolation(self):
        alice = _Verifier(subject="user-a", email="alice@tenant-a.test")
        bob = _Verifier(subject="user-b", email="bob@tenant-b.test")
        # Alice indexes a document.
        build_index_document_response(
            authorization=_AUTH,
            body=_index_body(title="Secret", content="acme quarterly revenue figures"),
            token_verifier=alice, store=self.store, embedding_client=_EMBED,
        )
        # Bob queries the same terms — must see nothing from tenant-a.
        status, payload = build_retrieval_query_response(
            authorization=_AUTH, body=_query_body(query="acme quarterly revenue"),
            token_verifier=bob, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["results"], [])
        # Alice still retrieves her own document.
        status, payload = build_retrieval_query_response(
            authorization=_AUTH, body=_query_body(query="acme quarterly revenue"),
            token_verifier=alice, store=self.store, embedding_client=_EMBED,
        )
        self.assertGreaterEqual(payload["count"], 1)


class TestRetrievalMetrics(unittest.TestCase):
    def test_m10_metrics_emitted(self):
        from app.control_plane.metrics import PROMETHEUS_AVAILABLE, metrics_output
        if not PROMETHEUS_AVAILABLE:
            self.skipTest("prometheus_client not installed")
        store = InMemoryRetrievalStore()
        v = _Verifier()
        build_index_document_response(
            authorization=_AUTH, body=_index_body(content="alpha beta gamma"),
            token_verifier=v, store=store, embedding_client=_EMBED,
        )
        build_retrieval_query_response(
            authorization=_AUTH, body=_query_body(query="alpha"),
            token_verifier=v, store=store, embedding_client=_EMBED,
        )
        text = metrics_output()[0].decode()
        for name in (
            "control_plane_retrieval_operation_duration_seconds",
            "control_plane_document_chunks_indexed_total",
            "control_plane_embeddings_generated_total",
            "control_plane_retrieval_results_returned",
            "control_plane_embedding_duration_seconds",
        ):
            self.assertIn(name, text)


class _FakeResp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


class _FailingEmbedder:
    def embed(self, texts):
        raise EmbeddingError("backend down")


class TestInferenceEmbeddingClient(unittest.TestCase):
    def _client(self):
        return InferenceEmbeddingClient(base_url="http://embed.svc:8000", model="bge-small")

    def test_success_orders_by_index(self):
        payload = {"data": [
            {"index": 1, "embedding": [0.2] * EMBEDDING_DIM},
            {"index": 0, "embedding": [0.1] * EMBEDDING_DIM},
        ]}
        with mock.patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
            vecs = self._client().embed(["a", "b"])
        self.assertEqual(len(vecs), 2)
        self.assertEqual(vecs[0][0], 0.1)  # index 0 first
        self.assertEqual(vecs[1][0], 0.2)

    def test_dimension_mismatch_raises(self):
        payload = {"data": [{"index": 0, "embedding": [0.1] * 10}]}
        with mock.patch("urllib.request.urlopen", return_value=_FakeResp(payload)):
            with self.assertRaises(EmbeddingError):
                self._client().embed(["a"])

    def test_unreachable_raises(self):
        import urllib.error
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")):
            with self.assertRaises(EmbeddingError):
                self._client().embed(["a"])


class TestEmbeddingDegradation(unittest.TestCase):
    def test_index_and_query_degrade_to_503(self):
        store = InMemoryRetrievalStore()
        v = _Verifier()
        status, payload = build_index_document_response(
            authorization=_AUTH, body=_index_body(content="hello world"),
            token_verifier=v, store=store, embedding_client=_FailingEmbedder(),
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "embedding_unavailable")
        status, _ = build_retrieval_query_response(
            authorization=_AUTH, body=_query_body(query="hello"),
            token_verifier=v, store=store, embedding_client=_FailingEmbedder(),
        )
        self.assertEqual(status, 503)


if __name__ == "__main__":
    unittest.main()
