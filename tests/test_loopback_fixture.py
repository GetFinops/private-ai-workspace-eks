"""End-to-end test of the M13 loopback fixture through the real harness + guard.

Starts the synthetic fixture server on an ephemeral loopback port and drives the
LoopbackIntegration through the IntegrationExecutor. This exercises the WHOLE
egress path for real — per-tenant credential resolution, request building, the
URL guard (whose private-IP block is waived only for the permitted fixture
host), and the pinned guarded_open socket send — without a real provider.
"""
import threading
import unittest

from app.control_plane.integrations import IntegrationExecutor
from app.integration_fixtures.loopback_integration import (
    LoopbackIntegration,
    build_fixture_registry,
)
from app.integration_fixtures.loopback_server import make_server

_TOKEN = "secret-fixture-token"


class _FixtureServer:
    def __init__(self, token=_TOKEN):
        # Bind to an ephemeral port on loopback.
        self.server = make_server("127.0.0.1", 0, token)
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _executor(base_url, *, token=_TOKEN):
    return IntegrationExecutor(
        integrations=build_fixture_registry(base_url),
        secret_resolver=lambda t, i: {"TOKEN": token} if (t, i) == ("tenant-a.test", "loopback") else None,
    )


class TestLoopbackRoundTrip(unittest.TestCase):
    def test_success_through_guard(self):
        with _FixtureServer() as fx:
            ex = _executor(fx.base_url)
            outcome = ex.invoke("loopback", "list_events", {}, tenant_id="tenant-a.test")
        self.assertEqual(outcome.result_class, "success")
        self.assertEqual(outcome.status, 200)
        self.assertEqual(len(outcome.result["data"]["events"]), 2)

    def test_wrong_credential_yields_upstream_401(self):
        with _FixtureServer() as fx:
            # Resolver returns a token the fixture will reject.
            ex = _executor(fx.base_url, token="wrong-token")
            outcome = ex.invoke("loopback", "list_events", {}, tenant_id="tenant-a.test")
        # The exchange succeeds at the transport layer; the upstream returns 401.
        self.assertEqual(outcome.result_class, "success")
        self.assertEqual(outcome.status, 401)

    def test_missing_credential_blocks_before_egress(self):
        with _FixtureServer() as fx:
            ex = _executor(fx.base_url)
            # A tenant with no resolved secret never reaches the network.
            outcome = ex.invoke("loopback", "list_events", {}, tenant_id="tenant-b.test")
        self.assertEqual(outcome.result_class, "no_credentials")

    def test_unknown_operation(self):
        with _FixtureServer() as fx:
            ex = _executor(fx.base_url)
            outcome = ex.invoke("loopback", "delete_everything", {}, tenant_id="tenant-a.test")
        self.assertEqual(outcome.result_class, "unknown_operation")


class TestLoopbackIntegrationUnit(unittest.TestCase):
    def test_declares_fixture_host_and_permit(self):
        integ = LoopbackIntegration("http://127.0.0.1:8099")
        self.assertEqual(integ.allowed_hosts, frozenset({"127.0.0.1"}))
        # The fixture host is trusted past the private-IP check (dev only).
        self.assertEqual(integ.permit_private_hosts, frozenset({"127.0.0.1"}))
        self.assertTrue(integ.requires_secret)


if __name__ == "__main__":
    unittest.main()
