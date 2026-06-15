"""Tests for the M13 hardened outbound-URL guard (app/control_plane/outbound.py).

DNS is faked via a patched ``getaddrinfo`` so the truth-table runs offline and
deterministically. Covers: scheme allow-list, credential rejection,
deny-by-default host allow-list, private/loopback/link-local/reserved blocking,
explicit cloud-metadata blocking (v4, v6, and IPv4-mapped), DNS-rebinding
(mixed public+private records), DNS failure, port handling, and IP pinning.
"""
import socket
import unittest
from unittest import mock

from app.control_plane import outbound
from app.control_plane.outbound import (
    OutboundReject,
    ValidatedTarget,
    validate_outbound_url,
)

_HOSTS = frozenset({"api.example.com", "localhost.fixture"})


def _addrinfo(*ips):
    """Build a getaddrinfo-shaped return value for the given IP strings."""
    out = []
    for ip in ips:
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        sockaddr = (ip, 443, 0, 0) if family == socket.AF_INET6 else (ip, 443)
        out.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
    return out


def _resolve(*ips):
    return mock.patch.object(outbound.socket, "getaddrinfo", return_value=_addrinfo(*ips))


def _validate(url, **kw):
    kw.setdefault("allowed_hosts", _HOSTS)
    return validate_outbound_url(url, **kw)


class TestSchemeAndShape(unittest.TestCase):
    def test_https_public_host_ok(self):
        with _resolve("93.184.216.34"):
            target = _validate("https://api.example.com/v1/cal?x=1")
        self.assertIsInstance(target, ValidatedTarget)
        self.assertEqual(target.host, "api.example.com")
        self.assertEqual(target.ip, "93.184.216.34")
        self.assertEqual(target.port, 443)
        self.assertEqual(target.path, "/v1/cal?x=1")

    def test_http_rejected_without_optin(self):
        with self.assertRaises(OutboundReject) as cm:
            _validate("http://api.example.com/")
        self.assertEqual(cm.exception.reason, outbound.REJECT_SCHEME)

    def test_http_allowed_with_optin(self):
        with _resolve("93.184.216.34"):
            target = _validate("http://localhost.fixture/", allow_http=True)
        self.assertEqual(target.scheme, "http")
        self.assertEqual(target.port, 80)

    def test_non_web_scheme_rejected(self):
        for url in ("ftp://api.example.com/", "file:///etc/passwd", "gopher://api.example.com/"):
            with self.assertRaises(OutboundReject) as cm:
                _validate(url)
            self.assertEqual(cm.exception.reason, outbound.REJECT_SCHEME)

    def test_embedded_credentials_rejected(self):
        with self.assertRaises(OutboundReject) as cm:
            _validate("https://user:pass@api.example.com/")
        self.assertEqual(cm.exception.reason, outbound.REJECT_CREDENTIALS)

    def test_missing_host_rejected(self):
        with self.assertRaises(OutboundReject) as cm:
            _validate("https:///path")
        self.assertEqual(cm.exception.reason, outbound.REJECT_MALFORMED)

    def test_default_path_is_root(self):
        with _resolve("93.184.216.34"):
            target = _validate("https://api.example.com")
        self.assertEqual(target.path, "/")


class TestHostAllowList(unittest.TestCase):
    def test_host_not_in_allowlist_rejected_before_dns(self):
        with mock.patch.object(outbound.socket, "getaddrinfo") as gai:
            with self.assertRaises(OutboundReject) as cm:
                _validate("https://evil.example.net/")
        self.assertEqual(cm.exception.reason, outbound.REJECT_HOST_NOT_ALLOWED)
        gai.assert_not_called()  # deny-by-default: never resolve a disallowed host

    def test_host_match_is_case_insensitive(self):
        with _resolve("93.184.216.34"):
            target = _validate("https://API.Example.COM/")
        self.assertEqual(target.host, "api.example.com")


class TestPrivateAndMetadataBlocking(unittest.TestCase):
    def test_private_ranges_rejected(self):
        for ip in ("10.0.0.1", "172.16.0.1", "192.168.1.1", "127.0.0.1", "0.0.0.0"):
            with _resolve(ip):
                with self.assertRaises(OutboundReject) as cm:
                    _validate("https://api.example.com/")
            self.assertEqual(cm.exception.reason, outbound.REJECT_PRIVATE_IP, ip)

    def test_link_local_rejected(self):
        with _resolve("169.254.10.10"):
            with self.assertRaises(OutboundReject) as cm:
                _validate("https://api.example.com/")
        self.assertEqual(cm.exception.reason, outbound.REJECT_PRIVATE_IP)

    def test_ipv4_metadata_rejected_as_metadata(self):
        with _resolve("169.254.169.254"):
            with self.assertRaises(OutboundReject) as cm:
                _validate("https://api.example.com/")
        self.assertEqual(cm.exception.reason, outbound.REJECT_METADATA)

    def test_ipv6_metadata_rejected_as_metadata(self):
        with _resolve("fd00:ec2::254"):
            with self.assertRaises(OutboundReject) as cm:
                _validate("https://api.example.com/")
        self.assertEqual(cm.exception.reason, outbound.REJECT_METADATA)

    def test_ipv4_mapped_metadata_rejected_as_metadata(self):
        with _resolve("::ffff:169.254.169.254"):
            with self.assertRaises(OutboundReject) as cm:
                _validate("https://api.example.com/")
        self.assertEqual(cm.exception.reason, outbound.REJECT_METADATA)

    def test_ipv6_loopback_and_ula_rejected(self):
        for ip in ("::1", "fc00::1", "fe80::1"):
            with _resolve(ip):
                with self.assertRaises(OutboundReject) as cm:
                    _validate("https://api.example.com/")
            self.assertEqual(cm.exception.reason, outbound.REJECT_PRIVATE_IP, ip)


class TestDnsRebinding(unittest.TestCase):
    def test_mixed_public_and_private_records_rejected(self):
        # A host that returns one public and one private A record must be
        # rejected wholesale — an attacker could otherwise race the connection
        # onto the private one.
        with _resolve("93.184.216.34", "10.0.0.5"):
            with self.assertRaises(OutboundReject) as cm:
                _validate("https://api.example.com/")
        self.assertEqual(cm.exception.reason, outbound.REJECT_PRIVATE_IP)

    def test_all_public_records_pin_first(self):
        with _resolve("93.184.216.34", "93.184.216.35"):
            target = _validate("https://api.example.com/")
        self.assertEqual(target.ip, "93.184.216.34")


class TestDnsFailure(unittest.TestCase):
    def test_resolution_failure_rejected_as_dns(self):
        with mock.patch.object(outbound.socket, "getaddrinfo", side_effect=socket.gaierror):
            with self.assertRaises(OutboundReject) as cm:
                _validate("https://api.example.com/")
        self.assertEqual(cm.exception.reason, outbound.REJECT_DNS)

    def test_empty_resolution_rejected_as_dns(self):
        with mock.patch.object(outbound.socket, "getaddrinfo", return_value=[]):
            with self.assertRaises(OutboundReject) as cm:
                _validate("https://api.example.com/")
        self.assertEqual(cm.exception.reason, outbound.REJECT_DNS)


class TestResponseShape(unittest.TestCase):
    def test_result_class_is_coarse(self):
        self.assertEqual(outbound.OutboundResponse(204, {}, b"").result_class, "2xx")
        self.assertEqual(outbound.OutboundResponse(404, {}, b"").result_class, "4xx")
        self.assertEqual(outbound.OutboundResponse(503, {}, b"").result_class, "5xx")


if __name__ == "__main__":
    unittest.main()
