"""Tests for PostgresTenantIntegrationState.

Uses a fake connection pool that records SQL, so no live database is required
(mirrors tests/test_m3_session_postgres.py).
"""
import unittest
from unittest.mock import MagicMock

from app.control_plane.integrations import (
    PostgresTenantIntegrationState,
    TenantIntegrationState,
)


def _make_pool(fetchone_return=None):
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.execute.return_value = MagicMock(fetchone=MagicMock(return_value=fetchone_return))
    pool = MagicMock()
    pool.connection.return_value = conn
    return pool, conn


class TestIsEnabled(unittest.TestCase):
    def test_default_enabled_when_no_row(self):
        pool, _ = _make_pool(fetchone_return=None)
        store = PostgresTenantIntegrationState(pool)
        self.assertTrue(store.is_enabled("tenant-a.test", "calendar"))

    def test_disabled_when_row_false(self):
        pool, _ = _make_pool(fetchone_return=(False,))
        store = PostgresTenantIntegrationState(pool)
        self.assertFalse(store.is_enabled("tenant-a.test", "calendar"))

    def test_enabled_when_row_true(self):
        pool, _ = _make_pool(fetchone_return=(True,))
        store = PostgresTenantIntegrationState(pool)
        self.assertTrue(store.is_enabled("tenant-a.test", "calendar"))

    def test_query_scopes_by_tenant_and_integration(self):
        pool, conn = _make_pool(fetchone_return=None)
        store = PostgresTenantIntegrationState(pool)
        store.is_enabled("tenant-a.test", "calendar")
        sql, params = conn.execute.call_args.args
        self.assertIn("FROM integration_tenant_state", sql)
        self.assertEqual(params, ("tenant-a.test", "calendar"))


class TestDisableEnable(unittest.TestCase):
    def test_disable_upserts_false_and_commits(self):
        pool, conn = _make_pool()
        store = PostgresTenantIntegrationState(pool)
        store.disable("tenant-a.test", "calendar")
        sql, params = conn.execute.call_args.args
        self.assertIn("INSERT INTO integration_tenant_state", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertEqual(params[0], "tenant-a.test")
        self.assertEqual(params[1], "calendar")
        self.assertFalse(params[2])
        conn.commit.assert_called_once()

    def test_enable_upserts_true(self):
        pool, conn = _make_pool()
        store = PostgresTenantIntegrationState(pool)
        store.enable("tenant-a.test", "calendar")
        _sql, params = conn.execute.call_args.args
        self.assertTrue(params[2])


class TestProtocol(unittest.TestCase):
    def test_satisfies_protocol(self):
        pool, _ = _make_pool()
        self.assertIsInstance(PostgresTenantIntegrationState(pool), TenantIntegrationState)


if __name__ == "__main__":
    unittest.main()
