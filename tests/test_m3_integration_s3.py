"""S3 integration tests using moto.

Requires the test extra:  pip install -e '.[test]'

These tests run against an in-process S3 mock (moto).  No live AWS
credentials or endpoints are needed, so they are suitable for CI without
any environment variables.

If moto is not installed the entire module is skipped rather than erroring,
so that the base `pip install .` (without [test]) still allows the suite to
pass.
"""

from __future__ import annotations

import unittest

try:
    import boto3
    import moto

    HAS_MOTO = True
except ImportError:
    HAS_MOTO = False

from app.storage.s3 import S3StorageClient, StorageError

_BUCKET = "integration-test-bucket"
_REGION = "us-east-1"


@unittest.skipUnless(HAS_MOTO, "moto[s3] not installed — run: pip install -e '.[test]'")
class TestS3StorageClientIntegration(unittest.TestCase):
    """Full put/get/delete/presign round-trips against the moto S3 mock."""

    def setUp(self) -> None:
        # Start the moto mock and create the bucket.
        self._mock = moto.mock_aws()
        self._mock.start()
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        self.client = S3StorageClient(bucket=_BUCKET, region=_REGION)

    def tearDown(self) -> None:
        self._mock.stop()

    # ── put / get round-trip ─────────────────────────────────────────────────

    def test_put_then_get_returns_same_bytes(self):
        payload = b"hello integration test"
        self.client.put_object(key="uploads/user-1/file.bin", body=payload)
        result = self.client.get_object(key="uploads/user-1/file.bin")
        self.assertEqual(result, payload)

    def test_put_with_content_type_is_stored(self):
        self.client.put_object(
            key="doc.json", body=b'{"ok": true}', content_type="application/json"
        )
        s3 = boto3.client("s3", region_name=_REGION)
        head = s3.head_object(Bucket=_BUCKET, Key="doc.json")
        self.assertEqual(head["ContentType"], "application/json")

    def test_empty_body_round_trips(self):
        self.client.put_object(key="empty", body=b"")
        self.assertEqual(self.client.get_object(key="empty"), b"")

    # ── delete ───────────────────────────────────────────────────────────────

    def test_delete_removes_object(self):
        self.client.put_object(key="to-delete", body=b"gone")
        self.client.delete_object(key="to-delete")
        with self.assertRaises(StorageError):
            self.client.get_object(key="to-delete")

    def test_delete_nonexistent_is_idempotent(self):
        # S3 DELETE is always 204; StorageError should not be raised.
        self.client.delete_object(key="does-not-exist")

    # ── missing key ──────────────────────────────────────────────────────────

    def test_get_missing_key_raises_storage_error(self):
        with self.assertRaises(StorageError):
            self.client.get_object(key="missing/key")

    # ── presigned URL ────────────────────────────────────────────────────────

    def test_presigned_get_url_is_a_string(self):
        self.client.put_object(key="signed-file", body=b"data")
        url = self.client.generate_presigned_url(key="signed-file", expires_in=300)
        self.assertIsInstance(url, str)
        self.assertTrue(url.startswith("https://"))

    def test_presigned_put_url_is_a_string(self):
        url = self.client.generate_presigned_url(
            key="upload-target", expires_in=120, operation="put_object"
        )
        self.assertIsInstance(url, str)

    # ── tenant isolation — key namespace ─────────────────────────────────────

    def test_different_subject_keys_do_not_collide(self):
        self.client.put_object(key="uploads/alice/file.txt", body=b"alice data")
        self.client.put_object(key="uploads/bob/file.txt", body=b"bob data")
        self.assertEqual(self.client.get_object(key="uploads/alice/file.txt"), b"alice data")
        self.assertEqual(self.client.get_object(key="uploads/bob/file.txt"), b"bob data")


if __name__ == "__main__":
    unittest.main()
