"""Tests for the M13 per-tenant secret resolver (integration_secrets.py).

Stdlib-only: the boto3-backed fetch is never exercised here — ``build_resolver``
takes an injected ``fetch`` and ``clock``, so secret-id construction, per-tenant
isolation, JSON parsing, TTL caching, and rotation-without-restart are all
tested offline and deterministically.
"""
import unittest

from app.control_plane.integration_secrets import (
    DEFAULT_PROJECT,
    IntegrationSecretError,
    build_resolver,
    build_secret_id,
)


class TestBuildSecretId(unittest.TestCase):
    def test_layout(self):
        sid = build_secret_id("dev", "tenant-a.test", "calendar")
        self.assertEqual(sid, f"{DEFAULT_PROJECT}/dev/integrations/tenant-a.test/calendar")

    def test_per_user_component(self):
        sid = build_secret_id("dev", "tenant-a.test", "mail", user="abc-123")
        self.assertEqual(
            sid, f"{DEFAULT_PROJECT}/dev/integrations/tenant-a.test/mail/abc-123"
        )

    def test_tenants_get_distinct_ids(self):
        a = build_secret_id("dev", "tenant-a.test", "calendar")
        b = build_secret_id("dev", "tenant-b.test", "calendar")
        self.assertNotEqual(a, b)

    def test_rejects_traversal_and_wildcard(self):
        # A component that could escape the tenant prefix or widen the IRSA
        # wildcard must be rejected, not sanitized away.
        for bad in ("../tenant-b.test", "tenant/../b", "*", "a/b", "a*", "", " "):
            with self.assertRaises(IntegrationSecretError):
                build_secret_id("dev", bad, "calendar")
            with self.assertRaises(IntegrationSecretError):
                build_secret_id("dev", "tenant-a.test", bad)


class _FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestResolver(unittest.TestCase):
    def _resolver(self, store, *, ttl=300):
        clock = _FakeClock()
        calls = []

        def fetch(secret_id):
            calls.append(secret_id)
            return store.get(secret_id)

        resolver = build_resolver(fetch, env="dev", ttl_seconds=ttl, clock=clock)
        return resolver, calls, clock

    def test_returns_parsed_mapping(self):
        sid = build_secret_id("dev", "tenant-a.test", "calendar")
        resolver, _, _ = self._resolver({sid: '{"TOKEN": "abc", "HOST": "x"}'})
        self.assertEqual(resolver("tenant-a.test", "calendar"), {"TOKEN": "abc", "HOST": "x"})

    def test_non_json_wrapped_under_value(self):
        sid = build_secret_id("dev", "tenant-a.test", "calendar")
        resolver, _, _ = self._resolver({sid: "raw-token"})
        self.assertEqual(resolver("tenant-a.test", "calendar"), {"value": "raw-token"})

    def test_missing_secret_returns_none(self):
        resolver, _, _ = self._resolver({})
        self.assertIsNone(resolver("tenant-a.test", "calendar"))

    def test_cross_tenant_cannot_read_other_secret(self):
        sid_a = build_secret_id("dev", "tenant-a.test", "calendar")
        resolver, _, _ = self._resolver({sid_a: '{"TOKEN": "a-secret"}'})
        # tenant-b resolves its own (absent) id, never tenant-a's value.
        self.assertIsNone(resolver("tenant-b.test", "calendar"))
        self.assertEqual(resolver("tenant-a.test", "calendar"), {"TOKEN": "a-secret"})

    def test_cache_hit_within_ttl(self):
        sid = build_secret_id("dev", "tenant-a.test", "calendar")
        resolver, calls, _ = self._resolver({sid: '{"TOKEN": "v1"}'})
        resolver("tenant-a.test", "calendar")
        resolver("tenant-a.test", "calendar")
        self.assertEqual(len(calls), 1)  # second call served from cache

    def test_rotation_propagates_after_ttl(self):
        sid = build_secret_id("dev", "tenant-a.test", "calendar")
        store = {sid: '{"TOKEN": "v1"}'}
        resolver, calls, clock = self._resolver(store, ttl=300)
        self.assertEqual(resolver("tenant-a.test", "calendar"), {"TOKEN": "v1"})
        # Rotate the secret in "Secrets Manager".
        store[sid] = '{"TOKEN": "v2"}'
        # Still cached just before expiry.
        clock.t += 299
        self.assertEqual(resolver("tenant-a.test", "calendar"), {"TOKEN": "v1"})
        # After the TTL lapses, the next call refetches — no restart needed.
        clock.t += 2
        self.assertEqual(resolver("tenant-a.test", "calendar"), {"TOKEN": "v2"})
        self.assertEqual(len(calls), 2)

    def test_missing_is_cached_too(self):
        resolver, calls, clock = self._resolver({})
        self.assertIsNone(resolver("tenant-a.test", "calendar"))
        self.assertIsNone(resolver("tenant-a.test", "calendar"))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
