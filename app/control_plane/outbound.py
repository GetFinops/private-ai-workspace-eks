"""Hardened outbound-URL validation and SSRF-safe HTTP for M13 integrations.

This is the **M3 hardened URL-validation layer of record** (see
``docs/m13-shared-harness-escalation.md`` Decision A). It did not previously
exist in code; M13 personal-information integrations cannot route outbound calls
through a layer that was never built, so it is built here, deny-by-default, as
the single chokepoint every integration egress must pass through.

Standard library only — no third-party HTTP client. Guarantees:

- **Scheme allow-list.** ``https`` only by default; ``http`` requires an explicit
  opt-in (used solely by the in-cluster loopback test fixture).
- **No embedded credentials.** URLs with userinfo are rejected.
- **Host allow-list, deny-by-default.** A host not in ``allowed_hosts`` is
  rejected before any DNS lookup.
- **Private/loopback/link-local/reserved and cloud-metadata blocking.** Every
  address the host resolves to is checked; if *any* resolves into a blocked
  range (RFC1918, loopback, link-local, ULA, reserved) or the cloud metadata
  address (``169.254.169.254`` / ``fd00:ec2::254``), the URL is rejected.
- **DNS-rebinding defense.** The host is resolved once; the connection is then
  pinned to the validated IP literal (the socket connects to that exact IP),
  while TLS SNI / certificate validation and the ``Host`` header continue to use
  the original hostname. The client never re-resolves, so a name that validates
  as public cannot be swapped for a private target between check and connect.

Nothing here logs URL values, paths, queries, headers, or bodies — callers audit
*shape* (host, method, response class, latency, decision, reject reason) per the
M5 content policy.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

__all__ = [
    "OutboundReject",
    "ValidatedTarget",
    "validate_outbound_url",
    "guarded_open",
    "OutboundResponse",
]


# ──────────────────────────────────────────────────────────────────────────────
# Errors and value types
# ──────────────────────────────────────────────────────────────────────────────

# Reject reasons are a closed set so callers can audit *why* without ever
# recording the offending URL value.
REJECT_SCHEME = "scheme"
REJECT_CREDENTIALS = "credentials"
REJECT_HOST_NOT_ALLOWED = "host_not_allowed"
REJECT_DNS = "dns"
REJECT_METADATA = "metadata"
REJECT_PRIVATE_IP = "private_ip"
REJECT_MALFORMED = "malformed"


class OutboundReject(Exception):
    """Raised when a URL fails outbound validation.

    ``reason`` is one of the ``REJECT_*`` constants — a closed vocabulary safe to
    surface in audit logs. The message never contains the rejected URL value.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"outbound rejected: {reason}" + (f" ({detail})" if detail else ""))


@dataclass(frozen=True)
class ValidatedTarget:
    """A validated, ready-to-connect outbound target.

    ``ip`` is the pinned, validated address the socket will connect to; ``host``
    is the original hostname used for SNI, certificate validation, and the
    ``Host`` header.
    """

    scheme: str
    host: str
    ip: str
    port: int
    path: str  # path + query, ready to put on the request line


@dataclass(frozen=True)
class OutboundResponse:
    """A minimal, content-safe view of an outbound HTTP response."""

    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def result_class(self) -> str:
        """Coarse status class (``2xx``/``4xx``/...) for shape-only auditing."""
        return f"{self.status // 100}xx"


# Cloud instance-metadata addresses, blocked explicitly so the audit reason is
# ``metadata`` rather than the generic ``private_ip`` (link-local). IPv4 is the
# IMDS address; the IPv6 form is AWS IMDSv6.
_METADATA_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})

_DEFAULT_PORTS = {"http": 80, "https": 443}


# ──────────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────────


def _classify_ip(raw_ip: str) -> str | None:
    """Return a ``REJECT_*`` reason if ``raw_ip`` is unsafe, else ``None``."""
    if raw_ip in _METADATA_IPS:
        return REJECT_METADATA
    try:
        ip = ipaddress.ip_address(raw_ip)
    except ValueError:
        return REJECT_MALFORMED
    # IMDS may also be reached via the IPv4-mapped form; unwrap before judging.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        mapped = ip.ipv4_mapped
        if str(mapped) in _METADATA_IPS:
            return REJECT_METADATA
        ip = mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return REJECT_PRIVATE_IP
    return None


def validate_outbound_url(
    url: str,
    *,
    allowed_hosts,
    allow_http: bool = False,
    permit_hosts=frozenset(),
) -> ValidatedTarget:
    """Validate ``url`` and return a ``ValidatedTarget`` pinned to a safe IP.

    ``allowed_hosts`` is the deny-by-default host allow-list (any iterable of
    hostnames; matched case-insensitively). ``allow_http`` permits the ``http``
    scheme and is intended only for the in-cluster loopback fixture — production
    integrations leave it ``False``.

    ``permit_hosts`` is a narrow, opt-in escape hatch: for these explicitly
    trusted hostnames the **private-IP** classification is waived, so an
    in-cluster service (e.g. the dev loopback fixture, which necessarily resolves
    to a private cluster IP) can be reached. It does NOT waive the
    cloud-metadata block — ``169.254.169.254`` / ``fd00:ec2::254`` stay refused
    even for a permitted host — nor malformed addresses. ``permit_hosts`` is
    empty by default; production integrations leave it empty so the full guard
    applies. A host here must still be in ``allowed_hosts``.

    Raises ``OutboundReject`` (with a closed-vocabulary ``reason``) on any
    failure. Resolves the host exactly once and pins the result, so the returned
    target cannot be rebound to a private address.
    """
    try:
        parts = urlsplit(url.strip())
    except (ValueError, AttributeError):
        raise OutboundReject(REJECT_MALFORMED)

    scheme = parts.scheme.lower()
    allowed_schemes = {"https", "http"} if allow_http else {"https"}
    if scheme not in allowed_schemes:
        raise OutboundReject(REJECT_SCHEME, scheme or "<none>")

    if parts.username or parts.password:
        raise OutboundReject(REJECT_CREDENTIALS)

    host = parts.hostname
    if not host:
        raise OutboundReject(REJECT_MALFORMED, "no host")
    host = host.lower()

    allowed = {h.lower() for h in allowed_hosts}
    if host not in allowed:
        raise OutboundReject(REJECT_HOST_NOT_ALLOWED)

    try:
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError:
        raise OutboundReject(REJECT_MALFORMED, "bad port")

    # Resolve once. Validate *every* returned address; reject if any is unsafe so
    # a host that mixes a public and a private record cannot slip through.
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise OutboundReject(REJECT_DNS)
    if not infos:
        raise OutboundReject(REJECT_DNS, "no records")

    permitted = {h.lower() for h in permit_hosts}
    resolved = [info[4][0] for info in infos]
    for raw_ip in resolved:
        reason = _classify_ip(raw_ip)
        if reason is None:
            continue
        # The cloud-metadata block is absolute and can never be waived; only the
        # generic private-IP classification is waivable, and only for an
        # explicitly permitted (trusted internal) host.
        if reason == REJECT_PRIVATE_IP and host in permitted:
            continue
        raise OutboundReject(reason)

    # All records are safe; pin to the first.
    pinned_ip = resolved[0]

    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"

    return ValidatedTarget(scheme=scheme, host=host, ip=pinned_ip, port=port, path=path)


# ──────────────────────────────────────────────────────────────────────────────
# Pinned connections (connect to the validated IP, present the hostname)
# ──────────────────────────────────────────────────────────────────────────────


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection pinned to a pre-validated IP, keeping the hostname for ``Host``."""

    def __init__(self, host: str, ip: str, port: int, *, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:  # pragma: no cover - exercised via guarded_open
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to a validated IP; SNI + cert use the hostname."""

    def __init__(
        self, host: str, ip: str, port: int, *, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = ip

    def connect(self) -> None:  # pragma: no cover - exercised via guarded_open
        sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        # Validate the certificate against the original hostname (self.host), not
        # the pinned IP — that is the whole point of presenting the hostname.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def guarded_open(
    target: ValidatedTarget,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 10.0,
    max_response_bytes: int = 1024 * 1024,
) -> OutboundResponse:
    """Perform a single HTTP request against a validated, pinned ``target``.

    The socket connects to ``target.ip`` (defeating DNS rebinding); SNI,
    certificate validation, and the ``Host`` header use ``target.host``. The
    response body is capped at ``max_response_bytes``. Returns a content-safe
    ``OutboundResponse``; never logs request or response values.
    """
    if target.scheme == "https":
        conn: http.client.HTTPConnection = _PinnedHTTPSConnection(
            target.host,
            target.ip,
            target.port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    else:
        conn = _PinnedHTTPConnection(target.host, target.ip, target.port, timeout=timeout)

    try:
        conn.request(method, target.path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read(max_response_bytes + 1)
        if len(data) > max_response_bytes:
            data = data[:max_response_bytes]
        return OutboundResponse(
            status=resp.status,
            headers={k.lower(): v for k, v in resp.getheaders()},
            body=data,
        )
    finally:
        conn.close()
