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
from app.control_plane.integrations_google import (
    GoogleCalendarIntegration,
    GoogleOAuthRefresh,
    register,
)
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


class TestOAuthRefresher(unittest.TestCase):
    def setUp(self):
        self.r = GoogleOAuthRefresh()

    def test_declares_oauth_host_and_token_key(self):
        self.assertEqual(self.r.allowed_hosts, frozenset({"oauth2.googleapis.com"}))
        self.assertEqual(self.r.token_key, "ACCESS_TOKEN")

    def test_build_request_is_form_post_to_token_endpoint(self):
        req = self.r.build_request({"CLIENT_ID": "cid", "CLIENT_SECRET": "s", "REFRESH_TOKEN": "rt"})
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.url, "https://oauth2.googleapis.com/token")
        self.assertEqual(req.headers["Content-Type"], "application/x-www-form-urlencoded")
        self.assertIn(b"grant_type=refresh_token", req.body)
        self.assertIn(b"refresh_token=rt", req.body)
        self.assertFalse(req.allow_http)

    def test_parse_token_success(self):
        token, life = self.r.parse_token(200, b'{"access_token": "AT", "expires_in": 3599}')
        self.assertEqual(token, "AT")
        self.assertEqual(life, 3599)

    def test_parse_token_non_200(self):
        self.assertEqual(self.r.parse_token(401, b'{"error": "x"}'), (None, 0))

    def test_parse_token_bad_json(self):
        self.assertEqual(self.r.parse_token(200, b"not json"), (None, 0))

    def test_parse_token_missing_field(self):
        self.assertEqual(self.r.parse_token(200, b'{"expires_in": 10}'), (None, 10))


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


_OAUTH_CREDS = {"CLIENT_ID": "cid", "CLIENT_SECRET": "csec", "REFRESH_TOKEN": "rtok"}
_TOKEN_RESP = OutboundResponse(
    200, {"content-type": "application/json"}, b'{"access_token": "minted-AT", "expires_in": 3600}')
_CAL_RESP = OutboundResponse(
    200, {"content-type": "application/json"}, b'{"items": [{"id": "e1", "summary": "Sync"}]}')


def _by_host(token=_TOKEN_RESP, calendar=_CAL_RESP):
    """guarded_open side_effect routing by target host (oauth vs api)."""
    def _send(target, **kw):
        if target.host == "oauth2.googleapis.com":
            return token
        return calendar
    return _send


class TestExecutorRoundTrip(unittest.TestCase):
    def _executor(self):
        # Secret now holds OAuth2 refresh material; the access token is minted.
        return IntegrationExecutor(
            integrations=register({}),
            secret_resolver=lambda t, i: dict(_OAUTH_CREDS) if i == "google_calendar" else None,
        )

    def test_success_refreshes_then_calls(self):
        ex = self._executor()
        with _public_dns(), mock.patch.object(integ, "guarded_open", side_effect=_by_host()) as go:
            outcome = ex.invoke("google_calendar", "list_events", {}, tenant_id="tenant-a.test")
        self.assertEqual(outcome.result_class, "success")
        self.assertEqual(outcome.result["data"]["items"][0]["id"], "e1")
        # Two guarded calls: token endpoint then the calendar API.
        hosts = [c.args[0].host for c in go.call_args_list]
        self.assertEqual(hosts, ["oauth2.googleapis.com", "www.googleapis.com"])
        # The calendar request carried the MINTED token, not anything from the secret.
        cal_call = go.call_args_list[1]
        self.assertEqual(cal_call.kwargs["headers"]["Authorization"], "Bearer minted-AT")

    def test_token_cached_across_calls(self):
        ex = self._executor()
        with _public_dns(), mock.patch.object(integ, "guarded_open", side_effect=_by_host()) as go:
            ex.invoke("google_calendar", "list_events", {}, tenant_id="tenant-a.test")
            ex.invoke("google_calendar", "list_events", {}, tenant_id="tenant-a.test")
        hosts = [c.args[0].host for c in go.call_args_list]
        # One refresh, then two calendar calls (token reused on the 2nd invoke).
        self.assertEqual(hosts, ["oauth2.googleapis.com", "www.googleapis.com", "www.googleapis.com"])

    def test_refresh_failure_aborts(self):
        ex = self._executor()
        bad_token = OutboundResponse(401, {}, b'{"error": "invalid_grant"}')
        with _public_dns(), mock.patch.object(integ, "guarded_open", side_effect=_by_host(token=bad_token)):
            outcome = ex.invoke("google_calendar", "list_events", {}, tenant_id="tenant-a.test")
        self.assertEqual(outcome.result_class, "refresh_failed")

    def test_no_credentials_blocks_before_egress(self):
        ex = IntegrationExecutor(integrations=register({}), secret_resolver=lambda t, i: None)
        outcome = ex.invoke("google_calendar", "list_events", {}, tenant_id="tenant-a.test")
        self.assertEqual(outcome.result_class, "no_credentials")

    def test_guard_blocks_if_host_resolves_private(self):
        # Defense-in-depth: even Google's host, if it somehow resolved to a
        # private IP (rebinding), is refused — permit_private_hosts is empty. The
        # refresh call is the first to hit the guard, so it is blocked first.
        ex = self._executor()
        with _private_dns(), mock.patch.object(integ, "guarded_open") as go:
            outcome = ex.invoke("google_calendar", "list_events", {}, tenant_id="tenant-a.test")
        self.assertEqual(outcome.result_class, "blocked:private_ip")
        go.assert_not_called()


class TestCredentialScopingUnderLoad(unittest.TestCase):
    """M7b isolation-under-load: under concurrent multi-tenant invokes, each
    tenant's per-(tenant,integration) OAuth token cache must hold ONLY its own
    minted token — no cross-tenant credential bleed."""

    def test_concurrent_token_cache_isolation(self):
        import threading
        import urllib.parse

        # Each tenant's secret carries a DISTINCT refresh token (rt-<tenant>).
        ex = IntegrationExecutor(
            integrations=register({}),
            secret_resolver=lambda t, i: (
                {"CLIENT_ID": "c", "CLIENT_SECRET": "s", "REFRESH_TOKEN": f"rt-{t}"}
                if i == "google_calendar" else None),
        )

        def _mint_by_tenant(target, **kw):
            # For the token endpoint, mint an access token that ENCODES the
            # tenant (derived from the refresh_token in the request body) so any
            # cross-tenant bleed is directly observable in the cache.
            if target.host == "oauth2.googleapis.com":
                body = kw.get("body", b"") or b""
                rt = urllib.parse.parse_qs(body.decode()).get("refresh_token", [""])[0]
                return OutboundResponse(
                    200, {"content-type": "application/json"},
                    f'{{"access_token": "AT-{rt[3:]}", "expires_in": 3600}}'.encode())
            return _CAL_RESP

        tenants = [f"tenant-{i}.test" for i in range(6)]
        errors: list = []

        def worker(t):
            try:
                out = ex.invoke("google_calendar", "list_events", {}, tenant_id=t)
                if out.result_class != "success":
                    errors.append((t, out.result_class))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with _public_dns(), mock.patch.object(integ, "guarded_open", side_effect=_mint_by_tenant):
            threads = [threading.Thread(target=worker, args=(t,)) for t in tenants]
            for th in threads:
                th.start()
            for th in threads:
                th.join()

        self.assertEqual(errors, [], f"integration invoke failures under load: {errors}")
        # Every tenant's cached access token is its OWN — no cross-tenant bleed.
        for t in tenants:
            cached = ex._token_cache.get((t, "google_calendar"))
            self.assertIsNotNone(cached, f"no token cached for {t}")
            self.assertEqual(cached[0], f"AT-{t}", f"cross-tenant token bleed for {t}")


if __name__ == "__main__":
    unittest.main()
