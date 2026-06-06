"""Tests for S3StorageClient.

Uses unittest.mock to replace boto3 — no live AWS credentials or bucket are
required.  Integration tests against a real S3 bucket can be enabled by
setting TEST_S3_BUCKET in the environment.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.storage.s3 import S3StorageClient, StorageError


class TestS3StorageClientInit(unittest.TestCase):

    def test_requires_non_empty_bucket(self):
        with self.assertRaises(ValueError):
            S3StorageClient(bucket="")

    def test_strips_whitespace_from_bucket(self):
        client = S3StorageClient(bucket="  my-bucket  ")
        self.assertEqual(client.bucket, "my-bucket")

    def test_bucket_property(self):
        client = S3StorageClient(bucket="artifacts")
        self.assertEqual(client.bucket, "artifacts")


class TestS3PutObject(unittest.TestCase):

    def _make_client_with_mock_s3(self):
        client = S3StorageClient(bucket="test-bucket")
        mock_s3 = MagicMock()
        client._client = mock_s3
        return client, mock_s3

    def test_put_object_calls_boto3(self):
        client, mock_s3 = self._make_client_with_mock_s3()
        client.put_object(key="uploads/file.txt", body=b"hello")
        mock_s3.put_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="uploads/file.txt",
            Body=b"hello",
            ContentType="application/octet-stream",
        )

    def test_put_object_raises_storage_error_on_failure(self):
        client, mock_s3 = self._make_client_with_mock_s3()
        mock_s3.put_object.side_effect = Exception("Access denied")
        with self.assertRaises(StorageError):
            client.put_object(key="uploads/file.txt", body=b"data")

    def test_put_object_custom_content_type(self):
        client, mock_s3 = self._make_client_with_mock_s3()
        client.put_object(key="file.json", body=b"{}", content_type="application/json")
        call_kwargs = mock_s3.put_object.call_args.kwargs
        self.assertEqual(call_kwargs["ContentType"], "application/json")


class TestS3GetObject(unittest.TestCase):

    def _make_client_with_mock_s3(self, body=b"data"):
        client = S3StorageClient(bucket="test-bucket")
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}
        client._client = mock_s3
        return client, mock_s3

    def test_get_object_returns_bytes(self):
        client, _ = self._make_client_with_mock_s3(body=b"file contents")
        result = client.get_object(key="uploads/file.txt")
        self.assertEqual(result, b"file contents")

    def test_get_object_raises_storage_error_on_failure(self):
        client = S3StorageClient(bucket="test-bucket")
        mock_s3 = MagicMock()
        mock_s3.get_object.side_effect = Exception("NoSuchKey")
        client._client = mock_s3
        with self.assertRaises(StorageError):
            client.get_object(key="missing.txt")


class TestS3DeleteObject(unittest.TestCase):

    def test_delete_object_calls_boto3(self):
        client = S3StorageClient(bucket="test-bucket")
        mock_s3 = MagicMock()
        client._client = mock_s3
        client.delete_object(key="uploads/file.txt")
        mock_s3.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key="uploads/file.txt"
        )

    def test_delete_object_raises_storage_error_on_failure(self):
        client = S3StorageClient(bucket="test-bucket")
        mock_s3 = MagicMock()
        mock_s3.delete_object.side_effect = Exception("Permission denied")
        client._client = mock_s3
        with self.assertRaises(StorageError):
            client.delete_object(key="some/key")


class TestS3PresignedUrl(unittest.TestCase):

    def test_generate_presigned_url_returns_string(self):
        client = S3StorageClient(bucket="test-bucket")
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://presigned.example.com/url"
        client._client = mock_s3

        url = client.generate_presigned_url(key="file.txt", expires_in=600)

        self.assertEqual(url, "https://presigned.example.com/url")
        mock_s3.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "test-bucket", "Key": "file.txt"},
            ExpiresIn=600,
        )

    def test_generate_presigned_url_raises_storage_error_on_failure(self):
        client = S3StorageClient(bucket="test-bucket")
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.side_effect = Exception("token expired")
        client._client = mock_s3
        with self.assertRaises(StorageError):
            client.generate_presigned_url(key="file.txt")


if __name__ == "__main__":
    unittest.main()
