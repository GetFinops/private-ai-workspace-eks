"""Minimal SQL migration runner for the control plane.

Applies a single bundled schema file (schema.sql) idempotently using
``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS`` guards.
No migration-version table is needed at this stage; all statements are
safe to re-run.

Concurrent-replica safety
--------------------------
``apply_migrations`` runs on every pod startup.  A PostgreSQL transaction-level
advisory lock (``pg_advisory_xact_lock``) serializes concurrent callers, so
only one replica executes the migration body at a time.  Other replicas block
until the lock is released on transaction commit, then run the now-idempotent
statements as no-ops.

Limitation: this pattern is safe **only** for idempotent (``IF NOT EXISTS``)
statements.  It does **not** make destructive or ordered migrations safe to
run concurrently.

Before introducing any non-idempotent migration, replace this runner with
either:
  - a dedicated pre-deploy Kubernetes Job that runs migrations once before
    the Deployment is rolled out, or
  - a proper migration tool with a versioning table (Alembic, Flyway, etc.)
    and its own advisory-lock-based leader election.

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

# Stable PostgreSQL advisory lock key that serialises concurrent migration
# runners across replicas.  Derived once from:
#   struct.unpack(">q", hashlib.sha256(b"private-ai-workspace-migrations").digest()[:8])[0]
# Do not change without updating the lock key in all deployed versions first.
_MIGRATION_LOCK_KEY: int = 3138434057897065624


def apply_migrations(pool: "ConnectionPool") -> None:
    """Apply the bundled schema SQL to the connected database.

    Idempotent — safe to call concurrently from multiple replicas.
    A transaction-level advisory lock ensures only one caller runs the
    migration body at a time; others block and then complete as no-ops.

    Raises ``RuntimeError`` if the schema file is missing or the SQL fails.
    """
    if not _SCHEMA_FILE.exists():
        raise RuntimeError(f"Schema file not found: {_SCHEMA_FILE}")

    sql = _SCHEMA_FILE.read_text(encoding="utf-8")
    logger.info("Applying database migrations from %s", _SCHEMA_FILE.name)

    with pool.connection() as conn:
        # Acquire a transaction-level advisory lock.  Automatically released
        # on commit or rollback when the connection context exits.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
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
