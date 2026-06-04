"""Session domain primitives and session-store interface for the control plane.

WorkspaceSession is an immutable value object suitable for external persistence.

SessionStore is a structural Protocol that any store backend must satisfy.
InMemorySessionStore is a development-only implementation — it is not safe for
production use because it does not survive process restarts and is not shared
across replicas.  A production implementation would back this with Redis,
PostgreSQL, or a managed session service (M3).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4


DEFAULT_SESSION_TTL = timedelta(hours=8)


# ──────────────────────────────────────────────────────────────────────────────
# Value object
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkspaceSession:
    """User session metadata intended for external persistence."""

    session_id: UUID
    subject: str
    created_at: datetime
    expires_at: datetime

    @classmethod
    def create(
        cls,
        *,
        subject: str,
        now: datetime | None = None,
        ttl: timedelta = DEFAULT_SESSION_TTL,
    ) -> "WorkspaceSession":
        subject = subject.strip()
        if not subject:
            raise ValueError("session subject is required")
        if ttl <= timedelta(0):
            raise ValueError("session ttl must be positive")

        created_at = now or datetime.now(UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        return cls(
            session_id=uuid4(),
            subject=subject,
            created_at=created_at,
            expires_at=created_at + ttl,
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        return current >= self.expires_at


# ──────────────────────────────────────────────────────────────────────────────
# Store interface
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class SessionStore(Protocol):
    """Structural interface for session storage backends.

    Concrete implementations must be safe for concurrent access.
    A production backend (M3) would delegate to Redis or PostgreSQL.
    """

    def create(self, *, subject: str, ttl: timedelta = DEFAULT_SESSION_TTL) -> WorkspaceSession:
        """Create and persist a new session for *subject*."""

    def get(self, session_id: UUID) -> WorkspaceSession | None:
        """Return the session if it exists and has not expired, else None."""

    def delete(self, session_id: UUID) -> None:
        """Remove a session.  No-op if the session does not exist."""


# ──────────────────────────────────────────────────────────────────────────────
# Development-only in-memory implementation
# ──────────────────────────────────────────────────────────────────────────────


class InMemorySessionStore:
    """Thread-safe in-memory session store.

    DEVELOPMENT ONLY — state is lost on process restart and is not shared
    across replicas.  Replace with an externalized backend (Redis, PostgreSQL)
    before deploying to staging or production (M3).
    """

    def __init__(self) -> None:
        self._sessions: dict[UUID, WorkspaceSession] = {}
        self._lock = threading.Lock()

    def create(self, *, subject: str, ttl: timedelta = DEFAULT_SESSION_TTL) -> WorkspaceSession:
        session = WorkspaceSession.create(subject=subject, ttl=ttl)
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: UUID) -> WorkspaceSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None or session.is_expired():
            return None
        return session

    def delete(self, session_id: UUID) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
