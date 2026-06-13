"""Unit tests for app.control_plane.memory (M10 — per-user memory).

Covers:
  - record: auth, explicit-consent requirement, validation, size limit
  - list: returns only the caller's memories
  - recall: relevance + cross-user isolation (same tenant, different user)
  - delete: authoritative removal, 404 on not-owned / unknown
"""
import json
import unittest

from app.control_plane.embeddings import DeterministicEmbeddingClient, EmbeddingError
from app.control_plane.memory import (
    InMemoryMemoryStore,
    _MAX_MEMORY_CHARS,
    build_memory_delete_response,
    build_memory_list_response,
    build_memory_recall_response,
    build_memory_record_response,
)
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


class _Verifier:
    def __init__(self, subject="user-a", email="alice@tenant-a.test"):
        self._claims = TokenClaims(subject=subject, email=email)

    def verify(self, raw_token: str) -> TokenClaims:
        if raw_token != "valid":
            raise TokenVerificationError("bad token")
        return self._claims


_AUTH = "Bearer valid"
_EMBED = DeterministicEmbeddingClient()

# Same tenant (tenant-a.test), different users — the cross-user isolation case.
_ALICE = _Verifier(subject="user-a", email="alice@tenant-a.test")
_CAROL = _Verifier(subject="user-c", email="carol@tenant-a.test")


def _record_body(content="my favourite colour is teal", consent=True):
    data = {"content": content}
    if consent is not None:
        data["consent"] = consent
    return json.dumps(data).encode()


def _recall_body(query="favourite colour", **extra):
    data = {"query": query}
    data.update(extra)
    return json.dumps(data).encode()


class TestMemoryRecord(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryMemoryStore()

    def test_requires_auth(self):
        status, _ = build_memory_record_response(
            authorization=None, body=_record_body(), token_verifier=_ALICE,
            store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 401)

    def test_consent_required(self):
        status, payload = build_memory_record_response(
            authorization=_AUTH, body=_record_body(consent=None),
            token_verifier=_ALICE, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"], "consent_required")
        # consent: false is also rejected.
        status, _ = build_memory_record_response(
            authorization=_AUTH, body=_record_body(consent=False),
            token_verifier=_ALICE, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 403)

    def test_validation_and_size(self):
        status, _ = build_memory_record_response(
            authorization=_AUTH, body=json.dumps({"consent": True}).encode(),
            token_verifier=_ALICE, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 400)
        status, _ = build_memory_record_response(
            authorization=_AUTH, body=_record_body(content="x" * (_MAX_MEMORY_CHARS + 1)),
            token_verifier=_ALICE, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 413)

    def test_record_then_list(self):
        status, payload = build_memory_record_response(
            authorization=_AUTH, body=_record_body(), token_verifier=_ALICE,
            store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 201)
        self.assertIn("id", payload)
        status, listing = build_memory_list_response(
            authorization=_AUTH, token_verifier=_ALICE, store=self.store,
        )
        self.assertEqual(status, 200)
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["memories"][0]["content"], "my favourite colour is teal")


class TestMemoryRecallAndDelete(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryMemoryStore()
        # Alice records two memories.
        for content in ("my favourite colour is teal", "i live in berlin"):
            build_memory_record_response(
                authorization=_AUTH, body=_record_body(content=content),
                token_verifier=_ALICE, store=self.store, embedding_client=_EMBED,
            )

    def test_recall_relevance(self):
        status, payload = build_memory_recall_response(
            authorization=_AUTH, body=_recall_body(query="favourite colour"),
            token_verifier=_ALICE, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["count"], 1)
        self.assertIn("colour", payload["results"][0]["content"])

    def test_cross_user_recall_isolation(self):
        # Carol (same tenant, different user) recalls the same terms — nothing.
        status, payload = build_memory_recall_response(
            authorization=_AUTH, body=_recall_body(query="favourite colour berlin"),
            token_verifier=_CAROL, store=self.store, embedding_client=_EMBED,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 0)
        # Carol's list is empty too.
        _, listing = build_memory_list_response(
            authorization=_AUTH, token_verifier=_CAROL, store=self.store,
        )
        self.assertEqual(listing["count"], 0)

    def test_authoritative_delete(self):
        _, listing = build_memory_list_response(
            authorization=_AUTH, token_verifier=_ALICE, store=self.store,
        )
        target = listing["memories"][0]["id"]
        status, payload = build_memory_delete_response(
            authorization=_AUTH, memory_id=target, token_verifier=_ALICE, store=self.store,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["deleted"])
        # Gone from list and recall; second delete is 404.
        _, listing = build_memory_list_response(
            authorization=_AUTH, token_verifier=_ALICE, store=self.store,
        )
        self.assertNotIn(target, [m["id"] for m in listing["memories"]])
        status, _ = build_memory_delete_response(
            authorization=_AUTH, memory_id=target, token_verifier=_ALICE, store=self.store,
        )
        self.assertEqual(status, 404)

    def test_cross_user_delete_is_404(self):
        _, listing = build_memory_list_response(
            authorization=_AUTH, token_verifier=_ALICE, store=self.store,
        )
        target = listing["memories"][0]["id"]
        # Carol cannot delete Alice's memory.
        status, _ = build_memory_delete_response(
            authorization=_AUTH, memory_id=target, token_verifier=_CAROL, store=self.store,
        )
        self.assertEqual(status, 404)
        # Alice's memory is still there.
        _, listing = build_memory_list_response(
            authorization=_AUTH, token_verifier=_ALICE, store=self.store,
        )
        self.assertIn(target, [m["id"] for m in listing["memories"]])


class _FailingEmbedder:
    def embed(self, texts):
        raise EmbeddingError("backend down")


class TestMemoryEmbeddingDegradation(unittest.TestCase):
    def test_record_and_recall_degrade_to_503(self):
        store = InMemoryMemoryStore()
        status, payload = build_memory_record_response(
            authorization=_AUTH, body=_record_body(), token_verifier=_ALICE,
            store=store, embedding_client=_FailingEmbedder(),
        )
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "embedding_unavailable")
        status, _ = build_memory_recall_response(
            authorization=_AUTH, body=_recall_body(), token_verifier=_ALICE,
            store=store, embedding_client=_FailingEmbedder(),
        )
        self.assertEqual(status, 503)


if __name__ == "__main__":
    unittest.main()
