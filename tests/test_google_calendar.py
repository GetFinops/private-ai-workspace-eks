"""Tests for the Google Calendar integration (app/control_plane/integrations_google.py).

Covers request building + URL-encoding, the bearer credential, that the full URL
guard applies (no private-host permit), and an executor round-trip with DNS and
the guarded send stubbed (no real network / no real Google call).
"""
import socket
import unittest
from unittest import mock

from app.control_plane import integrations as integ
from app.control_plane import outbound
from app.control_plane.integrations import IntegrationExecutor, UnknownOperation
from app.control_plane.integrations_google import GoogleCalendarIntegration, register
from app.control_plane.outbound import OutboundResponse


class TestRequestBuilding(unittest.TestCase):
    def setUp(self):
        self.gc = GoogleCalendarIntegration()

    def test_declares_public_host_and_no_permit(self):
        self.assertEqual(self.gc.allowed_hosts, frozenset({"www.googleapis.com"}))
        self.assertEqual(self.gc.permit_private_hosts, frozenset())  # full guard
        self.assertTrue(self.gc.requires_secret)

    def test_list_events_url_and_bearer(self):
        req = self.gc.build_request("list_events", {}, {"ACCESS_TOKEN": "tok-123"})
        self.assertEqual(req.method, "GET")
        self.assertTrue(req.url.startswith(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events?"))
        self.assertIn("maxResults=10", req.url)
        self.assertEqual(req.headers["Authorization"], "Bearer tok-123")
        self.assertFalse(req.allow_http)  # https only

    def test_calendar_id_is_url_encoded(self):
        req = self.gc.build_request("list_events", {"calendar_id": "team@example.com"}, {"ACCESS_TOKEN": "t"})
        self.assertIn("/calendars/team%40example.com/events", req.url)

    def test_max_results_clamped(self):
        req = self.gc.build_request("list_events", {"max_results": 9999}, {"ACCESS_TOKEN": "t"})
        self.assertIn("maxResults=250", req.url)
        req2 = self.gc.build_request("list_events", {"max_results": 0}, {"ACCESS_TOKEN": "t"})
        self.assertIn("maxResults=1", req2.url)

    def test_get_event_requires_event_id(self):
        with self.assertRaises(UnknownOperation):
            self.gc.build_request("get_event", {"calendar_id": "primary"}, {"ACCESS_TOKEN": "t"})

    def test_get_event_encodes_ids(self):
        req = self.gc.build_request("get_event", {"event_id": "abc/def"}, {"ACCESS_TOKEN": "t"})
        self.assertIn("/events/abc%2Fdef", req.url)

    def test_unknown_operation(self):
        with self.assertRaises(UnknownOperation):
            self.gc.build_request("delete_event", {}, {"ACCESS_TOKEN": "t"})

    def test_missing_token_yields_empty_bearer(self):
        req = self.gc.build_request("list_events", {}, None)
        self.assertEqual(req.headers["Authorization"], "Bearer ")


class TestRegister(unittest.TestCase):
    def test_register_adds_to_registry(self):
        reg = register({})
        self.assertIn("google_calendar", reg)
        self.assertIsInstance(reg["google_calendar"], GoogleCalendarIntegration)


def _public_dns(ip="142.250.72.10"):
    info = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))]
    return mock.patch.object(outbound.socket, "getaddrinfo", return_value=info)


def _private_dns():
    info = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.9", 443))]
    return mock.patch.object(outbound.socket, "getaddrinfo", return_value=info)


class TestExecutorRoundTrip(unittest.TestCase):
    def _executor(self, token="tok"):
        return IntegrationExecutor(
            integrations=register({}),
            secret_resolver=lambda t, i: {"ACCESS_TOKEN": token} if i == "google_calendar" else None,
        )

    def test_success_through_guard(self):
        ex = self._executor()
        body = b'{"items": [{"id": "e1", "summary": "Sync"}]}'
        with _public_dns(), mock.patch.object(
            integ, "guarded_open",
            return_value=OutboundResponse(200, {"content-type": "application/json"}, body),
        ) as go:
            outcome = ex.invoke("google_calendar", "list_events", {}, tenant_id="tenant-a.test")
        self.assertEqual(outcome.result_class, "success")
        self.assertEqual(outcome.status, 200)
        self.assertEqual(outcome.result["data"]["items"][0]["id"], "e1")
        # The guarded sender was given the googleapis host, pinned to a public IP.
        target = go.call_args.args[0]
        self.assertEqual(target.host, "www.googleapis.com")

    def test_no_credentials_blocks_before_egress(self):
        ex = IntegrationExecutor(integrations=register({}), secret_resolver=lambda t, i: None)
        outcome = ex.invoke("google_calendar", "list_events", {}, tenant_id="tenant-a.test")
        self.assertEqual(outcome.result_class, "no_credentials")

    def test_guard_blocks_if_host_resolves_private(self):
        # Defense-in-depth: even Google's host, if it somehow resolved to a
        # private IP (rebinding), is refused — permit_private_hosts is empty.
        ex = self._executor()
        with _private_dns(), mock.patch.object(integ, "guarded_open") as go:
            outcome = ex.invoke("google_calendar", "list_events", {}, tenant_id="tenant-a.test")
        self.assertEqual(outcome.result_class, "blocked:private_ip")
        go.assert_not_called()


if __name__ == "__main__":
    unittest.main()
