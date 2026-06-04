"""Session domain primitives for the control plane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4


DEFAULT_SESSION_TTL = timedelta(hours=8)


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
