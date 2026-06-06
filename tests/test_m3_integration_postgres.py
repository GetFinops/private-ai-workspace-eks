"""PostgreSQL integration tests for the session store and migration runner.

These tests require a live PostgreSQL instance.  Set ``TEST_DATABASE_URL``
to a libpq connection URI before running:

    export TEST_DATABASE_URL="postgresql://user:pass@localhost:5432/testdb"
    python3 -m unittest tests.test_m3_integration_postgres

The tests are skipped automatically when ``TEST_DATABASE_URL`` is not set,
so the standard CI suite (without a database) is unaffected.

Schema management
-----------------
Each test class creates a fresh schema by calling ``apply_migrations`` inside
``setUpClass``.  Individual tests that need an isolated state drop and
recreate specific tables in ``setUp`` so they do not depend on execution order.
"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

_SKIP_REASON = (
    "TEST_DATABASE_URL not set — "
    "export TEST_DATABASE_URL='postgresql://user:pass@host/db' to run"
)


@unittest.skipUnless(TEST_DATABASE_URL, _SKIP_REASON)
class TestMigrationRunner(unittest.TestCase):
    """apply_migrations is idempotent and serialises concurrent callers."""

    @classmethod
    def setUpClass(cls) -> None:
        from app.db.connection import open_pool
        from app.db.migrations import apply_migrations

        cls.pool = open_pool(TEST_DATABASE_URL)  # type: ignore[arg-type]
        apply_migrations(cls.pool)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pool.close()

    def test_apply_migrations_is_idempotent(self):
        """Running migrations twice must not raise."""
        from app.db.migrations import apply_migrations
        apply_migrations(self.pool)  # second call — should be a no-op

    def test_sessions_table_exists_after_migration(self):
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT to_regclass('public.sessions')"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row[0], "sessions table should exist")

    def test_sessions_index_exists_after_migration(self):
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT to_regclass('public.idx_sessions_expires_at')"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row[0], "expires_at index should exist")


@unittest.skipUnless(TEST_DATABASE_URL, _SKIP_REASON)
class TestPostgresSessionStoreIntegration(unittest.TestCase):
    """Full create/get/delete round-trips against a live PostgreSQL sessions table."""

    @classmethod
    def setUpClass(cls) -> None:
        from app.db.connection import open_pool
        from app.db.migrations import apply_migrations
        from app.control_plane.session_postgres import PostgresSessionStore

        cls.pool = open_pool(TEST_DATABASE_URL)  # type: ignore[arg-type]
        apply_migrations(cls.pool)
        cls.store = PostgresSessionStore(cls.pool)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pool.close()

    def setUp(self) -> None:
        # Truncate sessions between tests for a clean slate.
        with self.pool.connection() as conn:
            conn.execute("TRUNCATE TABLE sessions")
            conn.commit()

    # ── create ───────────────────────────────────────────────────────────────

    def test_create_persists_row(self):
        session = self.store.create(subject="alice@example.com")
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT subject FROM sessions WHERE session_id = %s",
                (str(session.session_id),),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "alice@example.com")

    def test_create_multiple_sessions_for_same_subject(self):
        s1 = self.store.create(subject="bob@example.com")
        s2 = self.store.create(subject="bob@example.com")
        self.assertNotEqual(s1.session_id, s2.session_id)

    # ── get ──────────────────────────────────────────────────────────────────

    def test_get_returns_session(self):
        original = self.store.create(subject="carol@example.com")
        fetched = self.store.get(original.session_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.session_id, original.session_id)
        self.assertEqual(fetched.subject, "carol@example.com")

    def test_get_unknown_id_returns_none(self):
        self.assertIsNone(self.store.get(uuid4()))

    def test_get_expired_session_returns_none(self):
        # Insert an already-expired row directly so we don't have to wait.
        now = datetime.now(UTC)
        session_id = uuid4()
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO sessions VALUES (%s, %s, %s, %s)",
                (str(session_id), "dave@example.com", now - timedelta(hours=9), now - timedelta(hours=1)),
            )
            conn.commit()
        self.assertIsNone(self.store.get(session_id))

    # ── delete ────────────────────────────────────────────────────────────────

    def test_delete_removes_session(self):
        session = self.store.create(subject="eve@example.com")
        self.store.delete(session.session_id)
        self.assertIsNone(self.store.get(session.session_id))

    def test_delete_nonexistent_is_noop(self):
        self.store.delete(uuid4())  # must not raise

    # ── purge ─────────────────────────────────────────────────────────────────

    def test_purge_removes_only_expired_rows(self):
        from app.db.migrations import purge_expired_sessions

        live = self.store.create(subject="frank@example.com", ttl=timedelta(hours=8))
        now = datetime.now(UTC)
        expired_id = uuid4()
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO sessions VALUES (%s, %s, %s, %s)",
                (str(expired_id), "ghost@example.com", now - timedelta(hours=9), now - timedelta(seconds=1)),
            )
            conn.commit()

        count = purge_expired_sessions(self.pool)

        self.assertGreaterEqual(count, 1)
        self.assertIsNotNone(self.store.get(live.session_id), "live session should survive purge")

    # ── advisory lock — concurrent migration serialisation ────────────────────

    def test_advisory_lock_key_is_consistent(self):
        """The lock key constant must not drift between module imports."""
        from app.db.migrations import _MIGRATION_LOCK_KEY
        import struct
        import hashlib

        expected = struct.unpack(
            ">q",
            hashlib.sha256(b"private-ai-workspace-migrations").digest()[:8],
        )[0]
        self.assertEqual(_MIGRATION_LOCK_KEY, expected)


if __name__ == "__main__":
    unittest.main()
