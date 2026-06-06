"""Tests for PostgresSessionStore.

Uses a fake connection pool that records SQL calls, so no live database is
required.  Integration tests against a real PostgreSQL instance can be added
by setting TEST_DATABASE_URL and running the test with that environment variable.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call
from uuid import UUID, uuid4

from app.control_plane.session import WorkspaceSession
from app.control_plane.session_postgres import PostgresSessionStore


# ── Fake pool / connection helpers ────────────────────────────────────────────

def _make_pool(fetchone_return=None):
    """Build a minimal fake ConnectionPool that captures execute() calls."""
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.execute.return_value = MagicMock(fetchone=MagicMock(return_value=fetchone_return))

    pool = MagicMock()
    pool.connection.return_value = conn
    return pool, conn


# ── create ────────────────────────────────────────────────────────────────────

class TestPostgresSessionStoreCreate(unittest.TestCase):

    def test_create_inserts_row_and_returns_session(self):
        pool, conn = _make_pool()
        store = PostgresSessionStore(pool)

        session = store.create(subject="alice@example.com")

        self.assertIsInstance(session, WorkspaceSession)
        self.assertEqual(session.subject, "alice@example.com")
        self.assertFalse(session.is_expired())

        conn.execute.assert_called_once()
        sql, params = conn.execute.call_args.args
        self.assertIn("INSERT INTO sessions", sql)
        self.assertEqual(params[1], "alice@example.com")

    def test_create_commits(self):
        pool, conn = _make_pool()
        store = PostgresSessionStore(pool)
        store.create(subject="bob@example.com")
        conn.commit.assert_called_once()

    def test_create_custom_ttl(self):
        pool, _ = _make_pool()
        store = PostgresSessionStore(pool)
        session = store.create(subject="charlie@example.com", ttl=timedelta(minutes=30))
        delta = session.expires_at - session.created_at
        self.assertAlmostEqual(delta.total_seconds(), 1800, delta=5)


# ── get ───────────────────────────────────────────────────────────────────────

class TestPostgresSessionStoreGet(unittest.TestCase):

    def _make_row(self, session_id, subject, created_at, expires_at):
        return (str(session_id), subject, created_at, expires_at)

    def test_get_returns_session_when_found_and_not_expired(self):
        session_id = uuid4()
        now = datetime.now(UTC)
        row = self._make_row(
            session_id, "alice@example.com", now, now + timedelta(hours=8)
        )
        pool, _ = _make_pool(fetchone_return=row)
        store = PostgresSessionStore(pool)

        result = store.get(session_id)

        self.assertIsNotNone(result)
        self.assertEqual(result.session_id, session_id)
        self.assertEqual(result.subject, "alice@example.com")

    def test_get_returns_none_when_not_found(self):
        pool, _ = _make_pool(fetchone_return=None)
        store = PostgresSessionStore(pool)
        result = store.get(uuid4())
        self.assertIsNone(result)

    def test_get_returns_none_when_expired(self):
        session_id = uuid4()
        past = datetime.now(UTC) - timedelta(hours=1)
        expired_row = self._make_row(
            session_id, "alice@example.com", past - timedelta(hours=8), past
        )
        pool, _ = _make_pool(fetchone_return=expired_row)
        store = PostgresSessionStore(pool)
        result = store.get(session_id)
        self.assertIsNone(result)


# ── delete ────────────────────────────────────────────────────────────────────

class TestPostgresSessionStoreDelete(unittest.TestCase):

    def test_delete_executes_delete_statement(self):
        pool, conn = _make_pool()
        store = PostgresSessionStore(pool)
        session_id = uuid4()

        store.delete(session_id)

        conn.execute.assert_called_once()
        sql, params = conn.execute.call_args.args
        self.assertIn("DELETE FROM sessions", sql)
        self.assertEqual(params[0], str(session_id))

    def test_delete_commits(self):
        pool, conn = _make_pool()
        store = PostgresSessionStore(pool)
        store.delete(uuid4())
        conn.commit.assert_called_once()


# ── Protocol conformance ──────────────────────────────────────────────────────

class TestPostgresSessionStoreProtocol(unittest.TestCase):

    def test_satisfies_session_store_protocol(self):
        from app.control_plane.session import SessionStore
        pool, _ = _make_pool()
        store = PostgresSessionStore(pool)
        self.assertIsInstance(store, SessionStore)


if __name__ == "__main__":
    unittest.main()
