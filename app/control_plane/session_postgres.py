"""PostgreSQL-backed session store for the control plane.

Implements the ``SessionStore`` Protocol defined in ``app.control_plane.session``
using a ``sessions`` table provisioned by ``app.db.migrations``.

This backend is safe for multi-replica deployments because all state lives in
the database rather than process memory.  It replaces ``InMemorySessionStore``
when ``DATABASE_URL`` is configured.

Concurrency model
-----------------
Each operation acquires a connection from the pool, executes a single
parameterised query, and releases the connection immediately.  The pool is
thread-safe; no additional locking is required here.

Expired sessions
----------------
``get`` treats an expired row as a cache miss and returns ``None``.
Rows are not deleted on read — call ``app.db.migrations.purge_expired_sessions``
periodically on a background thread to keep the table compact.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from app.control_plane.session import DEFAULT_SESSION_TTL, WorkspaceSession

logger = logging.getLogger(__name__)


class PostgresSessionStore:
    """Production session store backed by PostgreSQL.

    Parameters
    ----------
    pool:
        An open ``psycopg_pool.ConnectionPool`` obtained from
        ``app.db.connection.open_pool``.
    """

    def __init__(self, pool: object) -> None:
        self._pool = pool

    def create(
        self, *, subject: str, ttl: timedelta = DEFAULT_SESSION_TTL
    ) -> WorkspaceSession:
        """Create, persist, and return a new session."""
        session = WorkspaceSession.create(subject=subject, ttl=ttl)
        with self._pool.connection() as conn:  # type: ignore[union-attr]
            conn.execute(
                """
                INSERT INTO sessions (session_id, subject, created_at, expires_at)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    str(session.session_id),
                    session.subject,
                    session.created_at,
                    session.expires_at,
                ),
            )
            conn.commit()
        return session

    def get(self, session_id: UUID) -> WorkspaceSession | None:
        """Return the session if it exists and is not expired, else ``None``."""
        with self._pool.connection() as conn:  # type: ignore[union-attr]
            row = conn.execute(
                """
                SELECT session_id, subject, created_at, expires_at
                FROM sessions
                WHERE session_id = %s
                """,
                (str(session_id),),
            ).fetchone()

        if row is None:
            return None

        session = WorkspaceSession(
            session_id=UUID(row[0]) if isinstance(row[0], str) else row[0],
            subject=row[1],
            created_at=row[2],
            expires_at=row[3],
        )
        if session.is_expired():
            return None
        return session

    def delete(self, session_id: UUID) -> None:
        """Remove a session; no-op if it does not exist."""
        with self._pool.connection() as conn:  # type: ignore[union-attr]
            conn.execute(
                "DELETE FROM sessions WHERE session_id = %s",
                (str(session_id),),
            )
            conn.commit()
