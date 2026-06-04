"""Authentication domain primitives for the control plane.

This module defines the initial auth contract without implementing provider-
specific login flows. Hosted deployments must configure a real external
identity provider before auth endpoints are added.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class Role(StrEnum):
    """Coarse-grained roles for initial authorization checks."""

    ADMIN = "admin"
    USER = "user"


@dataclass(frozen=True)
class Principal:
    """Authenticated user identity as seen by the control plane."""

    subject: str
    email: str
    roles: frozenset[Role]

    @classmethod
    def build(
        cls,
        *,
        subject: str,
        email: str,
        roles: Iterable[Role],
    ) -> "Principal":
        subject = subject.strip()
        email = email.strip().lower()
        role_set = frozenset(roles)

        if not subject:
            raise ValueError("principal subject is required")
        if not email:
            raise ValueError("principal email is required")
        if not role_set:
            raise ValueError("at least one principal role is required")

        return cls(subject=subject, email=email, roles=role_set)

    def has_role(self, role: Role) -> bool:
        return role in self.roles


@dataclass(frozen=True)
class AuthSettings:
    """Auth configuration required before hosted traffic is accepted."""

    issuer_url: str | None = None
    audience: str | None = None
    admin_group: str | None = None

    def is_configured(self) -> bool:
        return bool(self.issuer_url and self.audience and self.admin_group)
