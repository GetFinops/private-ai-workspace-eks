"""Tests for the RAG file-upload endpoint (Tier A)."""
import unittest
from http import HTTPStatus

from app.control_plane.embeddings import DeterministicEmbeddingClient
from app.control_plane.retrieval import (
    InMemoryRetrievalStore,
    build_retrieval_upload_response,
)
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


class _Verifier:
    def verify(self, raw):
        if raw != "valid":
            raise TokenVerificationError("bad")
        return TokenClaims(subject="user-a", email="alice@tenant-a.test")


class _Storage:
    def __init__(self):
        self.objects = {}

    def put_object(self, *, key, body, content_type="application/octet-stream"):
        self.objects[key] = (body, content_type)


_V = _Verifier()


def _upload(filename, ctype, body, *, store=None, storage=None, max_bytes=10 * 1024 * 1024, auth="Bearer valid"):
    return build_retrieval_upload_response(
        authorization=auth, filename=filename, content_type=ctype, body=body,
        token_verifier=_V, store=store or InMemoryRetrievalStore(),
        embedding_client=DeterministicEmbeddingClient(), storage_client=storage,
        max_upload_bytes=max_bytes)


class TestRetrievalUpload(unittest.TestCase):
    def test_markdown_indexed_and_stored_per_tenant(self):
        storage = _Storage()
        status, payload = _upload("notes.md", "text/markdown", b"# Title\n\nEverest is 8849m tall.", storage=storage)
        self.assertEqual(status, HTTPStatus.CREATED)
        self.assertEqual(payload["title"], "notes.md")
        self.assertGreaterEqual(payload["chunk_count"], 1)
        key = payload["upload_key"]
        self.assertTrue(key.startswith("uploads/tenant-a.test/user-a/"))
        self.assertIn(key, storage.objects)

    def test_plain_text_no_storage_ok(self):
        status, payload = _upload("a.txt", "text/plain", b"hello world")
        self.assertEqual(status, HTTPStatus.CREATED)
        self.assertIsNone(payload["upload_key"])  # no storage client wired

    def test_binary_pdf_rejected(self):
        status, payload = _upload("doc.pdf", "application/pdf", b"%PDF-1.7 binary")
        self.assertEqual(status, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        self.assertEqual(payload["error"], "unsupported_media_type")

    def test_non_utf8_rejected(self):
        status, payload = _upload("x.txt", "text/plain", b"\xff\xfe\x00bad")
        self.assertEqual(status, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)

    def test_missing_filename(self):
        status, _ = _upload(None, "text/plain", b"hi")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)

    def test_empty_body(self):
        status, _ = _upload("a.txt", "text/plain", b"")
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)

    def test_too_large(self):
        status, _ = _upload("a.txt", "text/plain", b"x" * 100, max_bytes=10)
        self.assertEqual(status, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

    def test_anonymous_unauthorized(self):
        status, _ = _upload("a.txt", "text/plain", b"hi", auth=None)
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)


if __name__ == "__main__":
    unittest.main()
