"""The client-side integration that drives the loopback fixture (dev only).

This is a concrete ``Integration`` for the harness: a pure request builder. It
never opens a socket — the executor validates and sends through the hardened URL
guard. Because the fixture runs in-cluster (a private IP), this integration
declares its host in ``permit_private_hosts`` so the guard waives the
private-IP block for that one host (the cloud-metadata block is never waived).
Real integrations leave ``permit_private_hosts`` empty.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.control_plane.integrations import OutboundRequest, UnknownOperation


class LoopbackIntegration:
    name = "loopback"
    requires_secret = True

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        host = urlsplit(self._base).hostname or ""
        self.allowed_hosts = frozenset({host})
        # Dev-only: the fixture resolves to a private cluster IP, so trust this
        # one host past the private-IP check. Metadata is still blocked.
        self.permit_private_hosts = frozenset({host})

    def build_request(self, operation: str, params: dict, creds: "dict | None") -> OutboundRequest:
        if operation != "list_events":
            raise UnknownOperation()
        token = (creds or {}).get("TOKEN", "")
        return OutboundRequest(
            method="GET",
            url=f"{self._base}/calendar/events",
            headers={"Authorization": f"Bearer {token}"},
            allow_http=True,  # in-cluster fixture speaks http
        )


class BlockedProbeIntegration:
    """Targets a private IP with NO permit, so the URL guard must refuse it.

    Used by the dev smoke to prove the SSRF block end-to-end at the deployment
    layer: invoking it should never reach the network — the guard rejects it and
    the call is audited. Carries no secret.
    """

    name = "blocked_probe"
    requires_secret = False
    allowed_hosts = frozenset({"10.255.255.1"})
    permit_private_hosts = frozenset()  # deliberately NOT permitted

    def build_request(self, operation: str, params: dict, creds: "dict | None") -> OutboundRequest:
        return OutboundRequest(method="GET", url="http://10.255.255.1/probe", allow_http=True)


def build_fixture_registry(base_url: str) -> dict:
    """Registry for dev validation: the loopback fixture + the SSRF block probe."""
    return {
        LoopbackIntegration.name: LoopbackIntegration(base_url),
        BlockedProbeIntegration.name: BlockedProbeIntegration(),
    }
