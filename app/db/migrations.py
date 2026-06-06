"""Minimal SQL migration runner for the control plane.

Applies a single bundled schema file (schema.sql) idempotently using
``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS`` guards.
No migration-version table is needed at this stage; all statements are
safe to re-run.

Concurrent-replica note
-----------------------
``apply_migrations`` runs on every pod startup.  The current schema uses
``IF NOT EXISTS`` guards which are safe under concurrent execution by
multiple replicas.  This pattern does **not** generalize to destructive or
ordered migrations.

Before introducing any non-idempotent migration, replace this runner with
either:
  - a dedicated pre-deploy Kubernetes Job that runs migrations once before
    the Deployment is rolled out, or
  - a proper migration tool with a versioning table (Alembic, Flyway, etc.)
    and an advisory-lock-based concurrency guard.

Either choice requires maintainer review per the M3 escalation triggers
(database schema and migration strategy).
"""

from __future__ import annotations

import logging
import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_SCHEMA_FILE = pathlib.Path(__file__).parent / "schema.sql"


def apply_migrations(pool: "ConnectionPool") -> None:
    """Apply the bundled schema SQL to the connected database.

    Idempotent — safe to call on every startup.  All statements use
    ``IF NOT EXISTS`` so they are no-ops on an already-provisioned database.

    Raises ``RuntimeError`` if the schema file is missing or the SQL fails.
    """
    if not _SCHEMA_FILE.exists():
        raise RuntimeError(f"Schema file not found: {_SCHEMA_FILE}")

    sql = _SCHEMA_FILE.read_text(encoding="utf-8")
    logger.info("Applying database migrations from %s", _SCHEMA_FILE.name)

    with pool.connection() as conn:
        conn.execute(sql)
        conn.commit()

    logger.info("Database migrations complete.")


def purge_expired_sessions(pool: "ConnectionPool") -> int:
    """Delete expired sessions from the sessions table.

    Returns the number of rows deleted.  Call periodically (e.g. on a
    background thread) to keep the table from growing unboundedly.
    """
    with pool.connection() as conn:
        result = conn.execute(
            "DELETE FROM sessions WHERE expires_at <= now() RETURNING session_id"
        )
        conn.commit()
        count = result.rowcount
    if count:
        logger.info("Purged %d expired session(s).", count)
    return count
