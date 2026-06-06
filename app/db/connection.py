"""PostgreSQL connection pool for the control plane.

Wraps psycopg.pool.ConnectionPool (psycopg>=3.1, LGPL-2.1).

Usage
-----
    from app.db.connection import open_pool, ConnectionPool

    pool = open_pool(database_url)
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    pool.close()

The pool is intentionally created once at server startup and closed on
shutdown.  Do not create a pool per-request.

Connection strings must be a valid libpq connection URI:
    postgresql://user:pass@host:5432/dbname
Never embed credentials in source; read DATABASE_URL from the environment or
Secrets Manager.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool


def open_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> "ConnectionPool":
    """Open and return a psycopg connection pool.

    Raises ``ImportError`` if psycopg is not installed (should not happen in
    production; the image always installs psycopg[binary]).

    Parameters
    ----------
    database_url:
        libpq-compatible connection URI.
    min_size:
        Minimum number of connections kept open.
    max_size:
        Maximum number of connections in the pool.
    """
    try:
        from psycopg_pool import ConnectionPool
    except ImportError as exc:
        raise ImportError(
            "psycopg[binary]>=3.1 is required for database connectivity. "
            "Install it with: pip install 'psycopg[binary]>=3.1'"
        ) from exc

    return ConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        open=True,
    )
