"""Tests for the session-store/storage-client builders in server.py.

Verifies the M3 fail-closed behavior: in non-development environments the
control plane must refuse to start with an in-memory session store, even if
DATABASE_URL is set but the pool fails to open.

Also verifies that database connection errors are logged without echoing the
exception message (which may contain the conninfo string and credentials).
"""

from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

from app.control_plane.config import ControlPlaneConfig
from app.control_plane.server import _build_session_store, _build_storage_client
from app.control_plane.session import InMemorySessionStore


def _config(*, environment: str, database_url: str | None) -> ControlPlaneConfig:
    return ControlPlaneConfig(environment=environment, database_url=database_url)


# ── DATABASE_URL unset ───────────────────────────────────────────────────────

class TestSessionStoreNoDatabaseUrl(unittest.TestCase):

    def test_development_no_url_returns_in_memory_store_with_warning(self):
        cfg = _config(environment="development", database_url=None)
        with self.assertLogs("app.control_plane.server", level="WARNING"):
            store = _build_session_store(cfg)
        self.assertIsInstance(store, InMemorySessionStore)

    def test_production_no_url_raises_runtime_error(self):
        cfg = _config(environment="production", database_url=None)
        with self.assertRaises(RuntimeError) as ctx:
            _build_session_store(cfg)
        self.assertIn("DATABASE_URL", str(ctx.exception))
        self.assertIn("production", str(ctx.exception))

    def test_staging_no_url_raises_runtime_error(self):
        cfg = _config(environment="staging", database_url=None)
        with self.assertRaises(RuntimeError):
            _build_session_store(cfg)


# ── DATABASE_URL set but connection fails ────────────────────────────────────

class TestSessionStoreConnectionFails(unittest.TestCase):

    def test_development_connection_failure_falls_back_to_in_memory(self):
        cfg = _config(environment="development", database_url="postgresql://x/y")
        with patch("app.db.connection.open_pool", side_effect=OSError("boom")):
            with self.assertLogs("app.control_plane.server", level="ERROR"):
                store = _build_session_store(cfg)
        self.assertIsInstance(store, InMemorySessionStore)

    def test_production_connection_failure_raises_runtime_error(self):
        cfg = _config(environment="production", database_url="postgresql://x/y")
        with patch("app.db.connection.open_pool", side_effect=OSError("boom")):
            with self.assertRaises(RuntimeError) as ctx:
                _build_session_store(cfg)
        self.assertIn("session store", str(ctx.exception))
        self.assertIn("production", str(ctx.exception))


# ── Connection error must not leak conninfo / credentials ────────────────────

class TestConnectionErrorDoesNotLeakConninfo(unittest.TestCase):

    SECRET_DSN = "postgresql://app:supersecret@db.example.internal:5432/appdb"

    def _trigger_failure(self, environment: str):
        cfg = _config(environment=environment, database_url=self.SECRET_DSN)
        leaky_exc = OSError(
            f"connection to server at host '{self.SECRET_DSN}' failed"
        )
        with patch("app.db.connection.open_pool", side_effect=leaky_exc):
            with self.assertLogs("app.control_plane.server", level="ERROR") as logs:
                try:
                    _build_session_store(cfg)
                except RuntimeError:
                    pass
        return logs.output

    def test_development_error_log_does_not_contain_password(self):
        for line in self._trigger_failure("development"):
            self.assertNotIn("supersecret", line)
            self.assertNotIn("db.example.internal", line)

    def test_production_error_log_does_not_contain_password(self):
        for line in self._trigger_failure("production"):
            self.assertNotIn("supersecret", line)
            self.assertNotIn("db.example.internal", line)

    def test_production_runtime_error_message_does_not_contain_password(self):
        cfg = _config(environment="production", database_url=self.SECRET_DSN)
        leaky_exc = OSError(f"could not connect to {self.SECRET_DSN}")
        with patch("app.db.connection.open_pool", side_effect=leaky_exc):
            with self.assertRaises(RuntimeError) as ctx:
                _build_session_store(cfg)
        self.assertNotIn("supersecret", str(ctx.exception))
        self.assertNotIn("db.example.internal", str(ctx.exception))


# ── Storage client ────────────────────────────────────────────────────────────

class TestStorageClientBuilder(unittest.TestCase):

    def test_no_bucket_returns_none_with_warning(self):
        cfg = ControlPlaneConfig(environment="development", object_storage_bucket=None)
        with self.assertLogs("app.control_plane.server", level="WARNING"):
            self.assertIsNone(_build_storage_client(cfg))

    def test_bucket_configured_returns_client(self):
        cfg = ControlPlaneConfig(environment="development", object_storage_bucket="my-bucket")
        client = _build_storage_client(cfg)
        self.assertIsNotNone(client)
        self.assertEqual(client.bucket, "my-bucket")


if __name__ == "__main__":
    unittest.main()
