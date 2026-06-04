"""Tests for the session-store interface and InMemorySessionStore."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest import TestCase
from uuid import UUID

from app.control_plane.session import (
    DEFAULT_SESSION_TTL,
    InMemorySessionStore,
    SessionStore,
    WorkspaceSession,
)


class WorkspaceSessionTests(TestCase):
    def test_create_returns_valid_session(self) -> None:
        s = WorkspaceSession.create(subject="alice")
        self.assertEqual(s.subject, "alice")
        self.assertIsInstance(s.session_id, UUID)
        self.assertFalse(s.is_expired())

    def test_expired_session_detected(self) -> None:
        past = datetime(2020, 1, 1, tzinfo=UTC)
        s = WorkspaceSession.create(subject="bob", now=past, ttl=timedelta(seconds=1))
        self.assertTrue(s.is_expired())

    def test_empty_subject_raises(self) -> None:
        with self.assertRaises(ValueError):
            WorkspaceSession.create(subject="  ")

    def test_non_positive_ttl_raises(self) -> None:
        with self.assertRaises(ValueError):
            WorkspaceSession.create(subject="bob", ttl=timedelta(0))


class InMemorySessionStoreTests(TestCase):
    def test_satisfies_session_store_protocol(self) -> None:
        store = InMemorySessionStore()
        self.assertIsInstance(store, SessionStore)

    def test_create_and_get_round_trip(self) -> None:
        store = InMemorySessionStore()
        session = store.create(subject="alice")
        retrieved = store.get(session.session_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.subject, "alice")

    def test_get_unknown_id_returns_none(self) -> None:
        from uuid import uuid4
        store = InMemorySessionStore()
        self.assertIsNone(store.get(uuid4()))

    def test_expired_session_returns_none(self) -> None:
        store = InMemorySessionStore()
        session = store.create(subject="eve", ttl=timedelta(milliseconds=1))
        import time; time.sleep(0.01)
        self.assertIsNone(store.get(session.session_id))

    def test_delete_removes_session(self) -> None:
        store = InMemorySessionStore()
        session = store.create(subject="charlie")
        store.delete(session.session_id)
        self.assertIsNone(store.get(session.session_id))

    def test_delete_nonexistent_is_noop(self) -> None:
        from uuid import uuid4
        store = InMemorySessionStore()
        store.delete(uuid4())  # should not raise

    def test_len_reflects_stored_sessions(self) -> None:
        store = InMemorySessionStore()
        self.assertEqual(len(store), 0)
        store.create(subject="a")
        store.create(subject="b")
        self.assertEqual(len(store), 2)
