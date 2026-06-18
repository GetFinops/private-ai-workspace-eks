"""Tests for server-side conversation persistence (Tier A).

In-memory store + handlers, plus a fake-pool Postgres test. Focus: per-tenant +
per-user isolation, title seeding, validation, authoritative delete.
"""
import json
import unittest
from http import HTTPStatus
from unittest.mock import MagicMock

from app.control_plane.conversations import (
    InMemoryConversationStore,
    PostgresConversationStore,
    build_conversation_append_response,
    build_conversation_create_response,
    build_conversation_delete_response,
    build_conversation_get_response,
    build_conversations_list_response,
)
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


class _Verifier:
    def __init__(self, sub, email):
        self._c = TokenClaims(subject=sub, email=email)

    def verify(self, raw):
        if raw != "valid":
            raise TokenVerificationError("bad")
        return self._c


_ALICE = _Verifier("alice", "alice@tenant-a.test")
_ALICE2 = _Verifier("carol", "carol@tenant-a.test")   # same tenant, different user
_BOB = _Verifier("bob", "bob@tenant-b.test")           # different tenant
_AUTH = "Bearer valid"


def _create(store, verifier=_ALICE, title=None):
    body = json.dumps({"title": title}) if title else "{}"
    return build_conversation_create_response(
        authorization=_AUTH, body=body, token_verifier=verifier, store=store)


class TestConversationCRUD(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryConversationStore()

    def test_create_and_list(self):
        status, conv = _create(self.store, title="Trip plan")
        self.assertEqual(status, HTTPStatus.CREATED)
        self.assertEqual(conv["title"], "Trip plan")
        s, payload = build_conversations_list_response(
            authorization=_AUTH, token_verifier=_ALICE, store=self.store)
        self.assertEqual([c["id"] for c in payload["conversations"]], [conv["id"]])

    def test_append_seeds_title_and_get_returns_messages(self):
        _, conv = _create(self.store)  # default title "New conversation"
        cid = conv["id"]
        s, _ = build_conversation_append_response(
            authorization=_AUTH, conversation_id=cid,
            body=json.dumps({"role": "user", "content": "How tall is Everest?"}),
            token_verifier=_ALICE, store=self.store)
        self.assertEqual(s, HTTPStatus.CREATED)
        s, detail = build_conversation_get_response(
            authorization=_AUTH, conversation_id=cid, token_verifier=_ALICE, store=self.store)
        self.assertEqual(s, HTTPStatus.OK)
        self.assertEqual(detail["title"], "How tall is Everest?")  # seeded from first user msg
        self.assertEqual(len(detail["messages"]), 1)
        self.assertEqual(detail["messages"][0]["content"], "How tall is Everest?")

    def test_delete_then_404(self):
        _, conv = _create(self.store)
        s, _ = build_conversation_delete_response(
            authorization=_AUTH, conversation_id=conv["id"], token_verifier=_ALICE, store=self.store)
        self.assertEqual(s, HTTPStatus.OK)
        s, _ = build_conversation_get_response(
            authorization=_AUTH, conversation_id=conv["id"], token_verifier=_ALICE, store=self.store)
        self.assertEqual(s, HTTPStatus.NOT_FOUND)

    def test_anonymous_unauthorized(self):
        s, _ = build_conversations_list_response(
            authorization=None, token_verifier=_ALICE, store=self.store)
        self.assertEqual(s, HTTPStatus.UNAUTHORIZED)


class TestIsolation(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryConversationStore()
        _, self.conv = _create(self.store, verifier=_ALICE)
        self.cid = self.conv["id"]

    def test_other_tenant_cannot_get_or_delete_or_append(self):
        for verifier in (_BOB,):
            s, _ = build_conversation_get_response(
                authorization=_AUTH, conversation_id=self.cid, token_verifier=verifier, store=self.store)
            self.assertEqual(s, HTTPStatus.NOT_FOUND)
            s, _ = build_conversation_delete_response(
                authorization=_AUTH, conversation_id=self.cid, token_verifier=verifier, store=self.store)
            self.assertEqual(s, HTTPStatus.NOT_FOUND)
            s, _ = build_conversation_append_response(
                authorization=_AUTH, conversation_id=self.cid,
                body=json.dumps({"role": "user", "content": "x"}), token_verifier=verifier, store=self.store)
            self.assertEqual(s, HTTPStatus.NOT_FOUND)

    def test_same_tenant_other_user_isolated(self):
        # carol (same tenant, different user) cannot see alice's conversation.
        s, payload = build_conversations_list_response(
            authorization=_AUTH, token_verifier=_ALICE2, store=self.store)
        self.assertEqual(payload["conversations"], [])
        s, _ = build_conversation_get_response(
            authorization=_AUTH, conversation_id=self.cid, token_verifier=_ALICE2, store=self.store)
        self.assertEqual(s, HTTPStatus.NOT_FOUND)


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryConversationStore()
        _, self.conv = _create(self.store)
        self.cid = self.conv["id"]

    def _append(self, body):
        return build_conversation_append_response(
            authorization=_AUTH, conversation_id=self.cid, body=body, token_verifier=_ALICE, store=self.store)

    def test_bad_role(self):
        s, _ = self._append(json.dumps({"role": "robot", "content": "x"}))
        self.assertEqual(s, HTTPStatus.BAD_REQUEST)

    def test_missing_content(self):
        s, _ = self._append(json.dumps({"role": "user"}))
        self.assertEqual(s, HTTPStatus.BAD_REQUEST)

    def test_content_too_large(self):
        s, _ = self._append(json.dumps({"role": "user", "content": "x" * 200_000}))
        self.assertEqual(s, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

    def test_append_to_unknown_conversation(self):
        s, _ = build_conversation_append_response(
            authorization=_AUTH, conversation_id="00000000-0000-0000-0000-000000000000",
            body=json.dumps({"role": "user", "content": "x"}), token_verifier=_ALICE, store=self.store)
        self.assertEqual(s, HTTPStatus.NOT_FOUND)


class TestPostgresStore(unittest.TestCase):
    def _pool(self, fetchone=None, fetchall=(), rowcount=1):
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        result = MagicMock()
        result.fetchone = MagicMock(return_value=fetchone)
        result.fetchall = MagicMock(return_value=list(fetchall))
        result.rowcount = rowcount
        conn.execute.return_value = result
        pool = MagicMock()
        pool.connection.return_value = conn
        return pool, conn

    def test_create_inserts_and_commits(self):
        pool, conn = self._pool()
        conv = PostgresConversationStore(pool).create(tenant_id="t", user_id="u", title="hi")
        self.assertEqual(conv.title, "hi")
        sql = conn.execute.call_args.args[0]
        self.assertIn("INSERT INTO conversations", sql)
        conn.commit.assert_called()

    def test_get_scopes_by_tenant_and_user(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        pool, conn = self._pool(fetchone=("cid", "title", now, now), fetchall=[("mid", "user", "hi", now)])
        conv = PostgresConversationStore(pool).get(tenant_id="t", user_id="u", conversation_id="cid")
        self.assertIsNotNone(conv)
        self.assertEqual(len(conv.messages), 1)
        # The conversation SELECT filters by tenant AND user.
        first_sql = conn.execute.call_args_list[0].args[0]
        self.assertIn("tenant_id = %s AND user_id = %s", first_sql)

    def test_delete_returns_false_when_not_owned(self):
        pool, _ = self._pool(rowcount=0)
        self.assertFalse(PostgresConversationStore(pool).delete(tenant_id="t", user_id="u", conversation_id="x"))


if __name__ == "__main__":
    unittest.main()
