"""S3 object-storage client for the control plane.

Wraps boto3 (Apache-2.0) to provide a minimal interface for uploading and
retrieving artifact data.  The client authenticates via IRSA in production —
no static credentials are embedded.

Usage
-----
    from app.storage.s3 import S3StorageClient

    client = S3StorageClient(bucket="my-artifacts-bucket")
    client.put_object(key="uploads/user-123/file.txt", body=b"hello")
    data = client.get_object(key="uploads/user-123/file.txt")
    client.delete_object(key="uploads/user-123/file.txt")

Key conventions
---------------
- All keys are relative to the bucket root.
- Callers are responsible for constructing keys that include a tenant or
  user prefix to maintain isolation (e.g. ``uploads/<subject>/<filename>``).
- Do not store secrets or credentials as object values.
- Do not log object contents.

Error handling
--------------
All methods raise ``StorageError`` on failure.  Callers should catch it and
degrade gracefully rather than propagating boto3 internals to API responses.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Raised when an S3 operation fails."""


class S3StorageClient:
    """Minimal S3 client for artifact upload and retrieval.

    Parameters
    ----------
    bucket:
        S3 bucket name.  Set from ``OBJECT_STORAGE_BUCKET`` in the
        environment (injected by the Helm chart ConfigMap).
    region:
        AWS region.  Defaults to the region inferred by boto3 from the
        environment or IRSA token.
    """

    def __init__(self, bucket: str, *, region: str | None = None) -> None:
        if not bucket or not bucket.strip():
            raise ValueError("bucket name is required")
        self._bucket = bucket.strip()
        self._region = region
        self._client: object | None = None

    def _get_client(self) -> object:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3>=1.35 is required for S3 storage. "
                    "Install it with: pip install boto3>=1.35"
                ) from exc
            kwargs: dict = {}
            if self._region:
                kwargs["region_name"] = self._region
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def put_object(self, *, key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
        """Upload bytes to ``s3://<bucket>/<key>``.

        Raises ``StorageError`` on failure.
        """
        try:
            self._get_client().put_object(  # type: ignore[union-attr]
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )
            logger.debug("Uploaded %d bytes to s3://%s/%s", len(body), self._bucket, key)
        except Exception as exc:
            raise StorageError(f"Failed to upload s3://{self._bucket}/{key}: {exc}") from exc

    def get_object(self, *, key: str) -> bytes:
        """Download and return bytes from ``s3://<bucket>/<key>``.

        Raises ``StorageError`` on failure, including when the key does not exist.
        """
        try:
            response = self._get_client().get_object(Bucket=self._bucket, Key=key)  # type: ignore[union-attr]
            return response["Body"].read()
        except Exception as exc:
            raise StorageError(f"Failed to download s3://{self._bucket}/{key}: {exc}") from exc

    def get_object_with_type(self, *, key: str) -> "tuple[bytes, str]":
        """Download bytes + the stored Content-Type from ``s3://<bucket>/<key>``.

        Raises ``StorageError`` on failure (including a missing key).
        """
        try:
            response = self._get_client().get_object(Bucket=self._bucket, Key=key)  # type: ignore[union-attr]
            return response["Body"].read(), response.get("ContentType", "application/octet-stream")
        except Exception as exc:
            raise StorageError(f"Failed to download s3://{self._bucket}/{key}: {exc}") from exc

    def delete_object(self, *, key: str) -> None:
        """Delete ``s3://<bucket>/<key>``.

        No-op if the key does not exist (S3 delete is idempotent).
        Raises ``StorageError`` on network or permission failures.
        """
        try:
            self._get_client().delete_object(Bucket=self._bucket, Key=key)  # type: ignore[union-attr]
        except Exception as exc:
            raise StorageError(f"Failed to delete s3://{self._bucket}/{key}: {exc}") from exc

    def generate_presigned_url(
        self, *, key: str, expires_in: int = 3600, operation: str = "get_object"
    ) -> str:
        """Generate a presigned URL for temporary direct access.

        Parameters
        ----------
        key:
            Object key relative to the bucket.
        expires_in:
            Expiry in seconds (default 1 hour; max 7 days for IRSA-backed roles).
        operation:
            ``"get_object"`` (download) or ``"put_object"`` (upload).

        Raises ``StorageError`` on failure.
        """
        try:
            url: str = self._get_client().generate_presigned_url(  # type: ignore[union-attr]
                operation,
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
            return url
        except Exception as exc:
            raise StorageError(
                f"Failed to generate presigned URL for s3://{self._bucket}/{key}: {exc}"
            ) from exc

    @property
    def bucket(self) -> str:
        return self._bucket
