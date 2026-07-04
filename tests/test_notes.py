"""Tests for Notes & Tasks — CRUD + per-tenant/user isolation."""
import json
import unittest
from http import HTTPStatus

from app.control_plane.notes import (
    InMemoryNotesStore,
    build_note_create_response,
    build_note_delete_response,
    build_note_update_response,
    build_notes_list_response,
)
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


class _Verifier:
    def __init__(self, subject, email):
        self._claims = TokenClaims(subject=subject, email=email)

    def verify(self, raw):
        if raw != "valid":
            raise TokenVerificationError("bad")
        return self._claims


# Two users in tenant-a, one user in tenant-b.
_ALICE = _Verifier("user-alice", "alice@tenant-a.test")
_BEN = _Verifier("user-ben", "ben@tenant-a.test")          # same tenant, different user
_CARL = _Verifier("user-carl", "carl@tenant-b.test")       # different tenant


def _create(store, verifier, **body):
    body.setdefault("title", "t")
    return build_note_create_response(
        authorization="Bearer valid", body=json.dumps(body).encode(),
        token_verifier=verifier, store=store)


def _list(store, verifier, kind=None):
    return build_notes_list_response(
        authorization="Bearer valid", token_verifier=verifier, store=store, kind=kind)


def _update(store, verifier, item_id, **body):
    return build_note_update_response(
        authorization="Bearer valid", item_id=item_id, body=json.dumps(body).encode(),
        token_verifier=verifier, store=store)


def _delete(store, verifier, item_id):
    return build_note_delete_response(
        authorization="Bearer valid", item_id=item_id, token_verifier=verifier, store=store)


class TestCrud(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryNotesStore()

    def test_create_and_list(self):
        status, payload = _create(self.store, _ALICE, kind="note", title="Buy milk", body="2%")
        self.assertEqual(status, HTTPStatus.CREATED)
        self.assertEqual(payload["kind"], "note")
        self.assertEqual(payload["title"], "Buy milk")
        self.assertFalse(payload["done"])
        _, listed = _list(self.store, _ALICE)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["notes"][0]["id"], payload["id"])

    def test_list_filter_by_kind(self):
        _create(self.store, _ALICE, kind="note", title="n")
        _create(self.store, _ALICE, kind="task", title="t")
        self.assertEqual(_list(self.store, _ALICE, kind="task")[1]["count"], 1)
        self.assertEqual(_list(self.store, _ALICE, kind="note")[1]["count"], 1)
        self.assertEqual(_list(self.store, _ALICE)[1]["count"], 2)

    def test_update_toggle_done(self):
        _, created = _create(self.store, _ALICE, kind="task", title="ship it")
        status, updated = _update(self.store, _ALICE, created["id"], done=True)
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(updated["done"])
        self.assertGreaterEqual(updated["updated_at"], created["updated_at"])

    def test_update_partial_keeps_other_fields(self):
        _, created = _create(self.store, _ALICE, kind="note", title="A", body="B")
        _, updated = _update(self.store, _ALICE, created["id"], title="A2")
        self.assertEqual(updated["title"], "A2")
        self.assertEqual(updated["body"], "B")  # body untouched

    def test_delete(self):
        _, created = _create(self.store, _ALICE, kind="note", title="x")
        self.assertEqual(_delete(self.store, _ALICE, created["id"])[0], HTTPStatus.OK)
        self.assertEqual(_list(self.store, _ALICE)[1]["count"], 0)


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryNotesStore()

    def test_bad_kind(self):
        self.assertEqual(_create(self.store, _ALICE, kind="video")[0], HTTPStatus.BAD_REQUEST)

    def test_missing_title(self):
        status, _ = build_note_create_response(
            authorization="Bearer valid", body=json.dumps({"kind": "note", "body": "x"}).encode(),
            token_verifier=_ALICE, store=self.store)
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)

    def test_title_too_long(self):
        self.assertEqual(_create(self.store, _ALICE, title="x" * 600)[0],
                         HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

    def test_update_bad_done_type(self):
        _, created = _create(self.store, _ALICE, title="x")
        self.assertEqual(_update(self.store, _ALICE, created["id"], done="yes")[0],
                         HTTPStatus.BAD_REQUEST)

    def test_requires_auth(self):
        status, _ = build_note_create_response(
            authorization=None, body=b"{}", token_verifier=_ALICE, store=self.store)
        self.assertEqual(status, HTTPStatus.UNAUTHORIZED)


class TestIsolation(unittest.TestCase):
    """A caller can only ever touch their own (tenant_id, user_id) items."""

    def setUp(self):
        self.store = InMemoryNotesStore()
        _, self.alice_item = _create(self.store, _ALICE, kind="note", title="alice-secret")

    def test_other_user_same_tenant_cannot_see(self):
        self.assertEqual(_list(self.store, _BEN)[1]["count"], 0)

    def test_other_tenant_cannot_see(self):
        self.assertEqual(_list(self.store, _CARL)[1]["count"], 0)

    def test_other_user_update_is_404(self):
        self.assertEqual(_update(self.store, _BEN, self.alice_item["id"], done=True)[0],
                         HTTPStatus.NOT_FOUND)
        # Alice's item is untouched.
        self.assertFalse(_list(self.store, _ALICE)[1]["notes"][0]["done"])

    def test_other_tenant_delete_is_404(self):
        self.assertEqual(_delete(self.store, _CARL, self.alice_item["id"])[0], HTTPStatus.NOT_FOUND)
        self.assertEqual(_list(self.store, _ALICE)[1]["count"], 1)  # still there


if __name__ == "__main__":
    unittest.main()
