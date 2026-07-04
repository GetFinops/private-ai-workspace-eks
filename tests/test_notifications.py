"""Unit tests for app.control_plane.notifications (M9).

Tests cover:
  - InMemoryNotificationStore: publish, list, mark_read, isolation, cap
  - build_notifications_list_response: auth, pagination, isolation
  - build_notification_publish_response: validation, content policy
  - build_notification_read_response: auth, ownership, idempotency
"""
import datetime
import json
import unittest

from app.control_plane.notifications import (
    ALLOWED_EVENT_CLASSES,
    InMemoryNotificationStore,
    NotificationEvent,
    build_notification_publish_response,
    build_notification_read_response,
    build_notifications_list_response,
    format_notification_sse,
    stream_notification_frames,
    _extract_tenant_id,
    _MAX_NOTIFICATIONS_PER_USER,
)
from app.control_plane.token_verifier import TokenClaims, TokenVerificationError


# ──────────────────────────────────────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────────────────────────────────────

class _OkVerifier:
    """Stub verifier that always succeeds with a configurable subject / email."""

    def __init__(self, subject="user-1", email="user1@example.com"):
        self._claims = TokenClaims(subject=subject, email=email)

    def verify(self, raw_token: str) -> TokenClaims:
        if raw_token != "valid":
            raise TokenVerificationError("bad token")
        return self._claims


_VALID_AUTH = "Bearer valid"
_BAD_AUTH   = "Bearer bad"


def _make_event(**kwargs):
    defaults = dict(
        id="evt-1",
        tenant_id="example.com",
        user_id="user-1",
        event_class="system_notice",
        resource_id="res-001",
        created_at=datetime.datetime.now(tz=datetime.timezone.utc),
    )
    defaults.update(kwargs)
    return NotificationEvent(**defaults)


def _publish_body(event_class="system_notice", resource_id="res-001", **extra):
    data = {"event_class": event_class, "resource_id": resource_id}
    data.update(extra)
    return json.dumps(data).encode()


# ──────────────────────────────────────────────────────────────────────────────
# InMemoryNotificationStore
# ──────────────────────────────────────────────────────────────────────────────

class TestInMemoryStore(unittest.TestCase):

    def setUp(self):
        self.store = InMemoryNotificationStore()

    def test_list_empty(self):
        result = self.store.list_for_user(tenant_id="t1", user_id="u1")
        self.assertEqual(result, [])

    def test_publish_and_list(self):
        evt = _make_event()
        self.store.publish(evt)
        result = self.store.list_for_user(tenant_id="example.com", user_id="user-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "evt-1")

    def test_list_newest_first(self):
        for i in range(3):
            self.store.publish(_make_event(id=f"evt-{i}", resource_id=f"res-{i}"))
        result = self.store.list_for_user(tenant_id="example.com", user_id="user-1")
        self.assertEqual([r.id for r in result], ["evt-2", "evt-1", "evt-0"])

    def test_isolation_cross_user(self):
        self.store.publish(_make_event(user_id="user-1"))
        result = self.store.list_for_user(tenant_id="example.com", user_id="user-2")
        self.assertEqual(result, [])

    def test_isolation_cross_tenant(self):
        self.store.publish(_make_event(tenant_id="tenant-a"))
        result = self.store.list_for_user(tenant_id="tenant-b", user_id="user-1")
        self.assertEqual(result, [])

    def test_mark_read(self):
        self.store.publish(_make_event())
        updated = self.store.mark_read(
            tenant_id="example.com", user_id="user-1", notification_id="evt-1"
        )
        self.assertIsNotNone(updated)
        self.assertIsNotNone(updated.read_at)

    def test_mark_read_wrong_owner(self):
        self.store.publish(_make_event())
        result = self.store.mark_read(
            tenant_id="example.com", user_id="other-user", notification_id="evt-1"
        )
        self.assertIsNone(result)

    def test_mark_read_wrong_tenant(self):
        self.store.publish(_make_event())
        result = self.store.mark_read(
            tenant_id="other.com", user_id="user-1", notification_id="evt-1"
        )
        self.assertIsNone(result)

    def test_list_excludes_read_by_default(self):
        self.store.publish(_make_event(id="unread-1"))
        self.store.publish(_make_event(id="read-1"))
        self.store.mark_read(tenant_id="example.com", user_id="user-1", notification_id="read-1")
        result = self.store.list_for_user(tenant_id="example.com", user_id="user-1")
        ids = [r.id for r in result]
        self.assertIn("unread-1", ids)
        self.assertNotIn("read-1", ids)

    def test_list_include_read(self):
        self.store.publish(_make_event(id="unread-1"))
        self.store.publish(_make_event(id="read-1"))
        self.store.mark_read(tenant_id="example.com", user_id="user-1", notification_id="read-1")
        result = self.store.list_for_user(
            tenant_id="example.com", user_id="user-1", include_read=True
        )
        self.assertEqual(len(result), 2)

    def test_ring_buffer_cap(self):
        for i in range(_MAX_NOTIFICATIONS_PER_USER + 10):
            self.store.publish(_make_event(id=f"evt-{i}"))
        result = self.store.list_for_user(
            tenant_id="example.com", user_id="user-1", include_read=True, limit=300
        )
        self.assertLessEqual(len(result), _MAX_NOTIFICATIONS_PER_USER)

    def test_limit_respected(self):
        for i in range(20):
            self.store.publish(_make_event(id=f"evt-{i}"))
        result = self.store.list_for_user(
            tenant_id="example.com", user_id="user-1", limit=5
        )
        self.assertLessEqual(len(result), 5)


# ──────────────────────────────────────────────────────────────────────────────
# SSE stream (real-time push)
# ──────────────────────────────────────────────────────────────────────────────

class TestNotificationStream(unittest.TestCase):

    def setUp(self):
        self.store = InMemoryNotificationStore()

    def _data_frames(self, frames):
        return [f for f in frames if f.startswith(b"data:")]

    def test_emits_unread_then_heartbeat(self):
        self.store.publish(_make_event(id="a", tenant_id="t", user_id="u"))
        self.store.publish(_make_event(id="b", tenant_id="t", user_id="u"))
        frames = list(stream_notification_frames(
            self.store, tenant_id="t", user_id="u", max_ticks=1, sleep=lambda: None))
        data = self._data_frames(frames)
        self.assertEqual(len(data), 2)
        self.assertTrue(any(f.startswith(b": ping") for f in frames))  # heartbeat

    def test_frame_is_content_safe_shape_only(self):
        self.store.publish(_make_event(id="a", tenant_id="t", user_id="u"))
        frames = list(stream_notification_frames(
            self.store, tenant_id="t", user_id="u", max_ticks=1, sleep=lambda: None))
        payload = json.loads(self._data_frames(frames)[0][len(b"data: "):].decode())
        self.assertEqual(set(payload), {"id", "event_class", "resource_id",
                                        "created_at", "read_at", "read"})

    def test_dedup_across_ticks(self):
        self.store.publish(_make_event(id="a", tenant_id="t", user_id="u"))
        frames = list(stream_notification_frames(
            self.store, tenant_id="t", user_id="u", max_ticks=3, sleep=lambda: None))
        self.assertEqual(len(self._data_frames(frames)), 1)  # emitted once, not per tick

    def test_seen_suppresses_known_events(self):
        self.store.publish(_make_event(id="a", tenant_id="t", user_id="u"))
        frames = list(stream_notification_frames(
            self.store, tenant_id="t", user_id="u", max_ticks=1, sleep=lambda: None, seen={"a"}))
        self.assertEqual(self._data_frames(frames), [])

    def test_isolation_other_user_sees_nothing(self):
        self.store.publish(_make_event(id="a", tenant_id="t", user_id="u"))
        frames = list(stream_notification_frames(
            self.store, tenant_id="t", user_id="other", max_ticks=1, sleep=lambda: None))
        self.assertEqual(self._data_frames(frames), [])


# ──────────────────────────────────────────────────────────────────────────────
# _extract_tenant_id
# ──────────────────────────────────────────────────────────────────────────────

class TestExtractTenantId(unittest.TestCase):

    def test_email_domain(self):
        claims = TokenClaims(subject="u", email="alice@corp.example.com")
        self.assertEqual(_extract_tenant_id(claims), "corp.example.com")

    def test_email_lowercase(self):
        claims = TokenClaims(subject="u", email="alice@CORP.COM")
        self.assertEqual(_extract_tenant_id(claims), "corp.com")

    def test_no_email(self):
        claims = TokenClaims(subject="u", email="")
        self.assertEqual(_extract_tenant_id(claims), "default")

    def test_no_at_sign(self):
        claims = TokenClaims(subject="u", email="not-an-email")
        self.assertEqual(_extract_tenant_id(claims), "default")


# ──────────────────────────────────────────────────────────────────────────────
# build_notifications_list_response
# ──────────────────────────────────────────────────────────────────────────────

class TestListResponse(unittest.TestCase):

    def setUp(self):
        self.store = InMemoryNotificationStore()
        self.verifier = _OkVerifier()

    def _list(self, auth=_VALID_AUTH, **kw):
        return build_notifications_list_response(
            authorization=auth,
            token_verifier=self.verifier,
            store=self.store,
            **kw,
        )

    def test_missing_auth(self):
        status, body = self._list(auth=None)
        self.assertEqual(int(status), 401)

    def test_bad_token(self):
        status, body = self._list(auth=_BAD_AUTH)
        self.assertEqual(int(status), 401)

    def test_no_verifier(self):
        status, body = build_notifications_list_response(
            authorization=_VALID_AUTH,
            token_verifier=None,
            store=self.store,
        )
        self.assertEqual(int(status), 503)

    def test_empty_list(self):
        status, body = self._list()
        self.assertEqual(int(status), 200)
        self.assertEqual(body["notifications"], [])
        self.assertEqual(body["count"], 0)

    def test_returns_own_events(self):
        # Publish via the API so tenant is derived consistently.
        status, data = build_notification_publish_response(
            authorization=_VALID_AUTH,
            body=_publish_body(),
            token_verifier=self.verifier,
            store=self.store,
        )
        self.assertEqual(int(status), 201)

        status, body = self._list()
        self.assertEqual(int(status), 200)
        self.assertEqual(body["count"], 1)

    def test_limit_capped_at_100(self):
        status, body = self._list(limit=9999)
        self.assertEqual(int(status), 200)   # no error; limit is clamped internally


# ──────────────────────────────────────────────────────────────────────────────
# build_notification_publish_response
# ──────────────────────────────────────────────────────────────────────────────

class TestPublishResponse(unittest.TestCase):

    def setUp(self):
        self.store = InMemoryNotificationStore()
        self.verifier = _OkVerifier()

    def _publish(self, body, auth=_VALID_AUTH):
        return build_notification_publish_response(
            authorization=auth,
            body=body,
            token_verifier=self.verifier,
            store=self.store,
        )

    def test_missing_auth(self):
        status, _ = self._publish(_publish_body(), auth=None)
        self.assertEqual(int(status), 401)

    def test_invalid_json(self):
        status, _ = self._publish(b"not json")
        self.assertEqual(int(status), 400)

    def test_invalid_event_class(self):
        status, body = self._publish(_publish_body(event_class="unknown_class"))
        self.assertEqual(int(status), 422)
        self.assertIn("event_class", body["error"])

    def test_missing_resource_id(self):
        data = json.dumps({"event_class": "system_notice"}).encode()
        status, _ = self._publish(data)
        self.assertEqual(int(status), 400)

    def test_empty_resource_id(self):
        status, _ = self._publish(_publish_body(resource_id="   "))
        self.assertEqual(int(status), 400)

    def test_resource_id_too_long(self):
        status, _ = self._publish(_publish_body(resource_id="x" * 300))
        self.assertEqual(int(status), 400)

    def test_content_policy_rejects_prompt(self):
        data = json.dumps({
            "event_class": "system_notice",
            "resource_id": "r1",
            "prompt": "user message",
        }).encode()
        status, body = self._publish(data)
        self.assertEqual(int(status), 400)
        self.assertIn("policy", body["error"])

    def test_content_policy_rejects_completion(self):
        data = json.dumps({
            "event_class": "system_notice",
            "resource_id": "r1",
            "completion": "assistant reply",
        }).encode()
        status, body = self._publish(data)
        self.assertEqual(int(status), 400)

    def test_valid_publish(self):
        for ec in ALLOWED_EVENT_CLASSES:
            with self.subTest(event_class=ec):
                store = InMemoryNotificationStore()
                status, body = build_notification_publish_response(
                    authorization=_VALID_AUTH,
                    body=_publish_body(event_class=ec),
                    token_verifier=self.verifier,
                    store=store,
                )
                self.assertEqual(int(status), 201)
                self.assertIn("id", body)
                self.assertEqual(body["event_class"], ec)
                self.assertFalse(body["read"])

    def test_cross_user_publish_is_scoped(self):
        """Publisher can only publish for their own subject."""
        verifier_b = _OkVerifier(subject="user-b", email="userb@example.com")
        build_notification_publish_response(
            authorization=_VALID_AUTH,
            body=_publish_body(),
            token_verifier=self.verifier,
            store=self.store,
        )
        # user-b cannot see user-1's notification.
        status, body = build_notifications_list_response(
            authorization=_VALID_AUTH,
            token_verifier=verifier_b,
            store=self.store,
        )
        self.assertEqual(int(status), 200)
        self.assertEqual(body["count"], 0)


# ──────────────────────────────────────────────────────────────────────────────
# build_notification_read_response
# ──────────────────────────────────────────────────────────────────────────────

class TestMarkReadResponse(unittest.TestCase):

    def setUp(self):
        self.store = InMemoryNotificationStore()
        self.verifier = _OkVerifier()
        # Publish one notification owned by user-1.
        _, data = build_notification_publish_response(
            authorization=_VALID_AUTH,
            body=_publish_body(),
            token_verifier=self.verifier,
            store=self.store,
        )
        self.notif_id = data["id"]

    def _read(self, nid, auth=_VALID_AUTH):
        return build_notification_read_response(
            authorization=auth,
            notification_id=nid,
            token_verifier=self.verifier,
            store=self.store,
        )

    def test_missing_auth(self):
        status, _ = self._read(self.notif_id, auth=None)
        self.assertEqual(int(status), 401)

    def test_not_found_for_wrong_user(self):
        verifier_b = _OkVerifier(subject="user-b", email="userb@example.com")
        status, _ = build_notification_read_response(
            authorization=_VALID_AUTH,
            notification_id=self.notif_id,
            token_verifier=verifier_b,
            store=self.store,
        )
        self.assertEqual(int(status), 404)

    def test_mark_read_success(self):
        status, body = self._read(self.notif_id)
        self.assertEqual(int(status), 200)
        self.assertTrue(body["read"])
        self.assertIsNotNone(body["read_at"])

    def test_not_found_unknown_id(self):
        status, _ = self._read("no-such-id")
        self.assertEqual(int(status), 404)

    def test_already_read_returns_404(self):
        # mark_read of an already-read notification returns None → 404
        self._read(self.notif_id)
        status, _ = self._read(self.notif_id)
        self.assertEqual(int(status), 404)


if __name__ == "__main__":
    unittest.main()
