from datetime import UTC, datetime, timedelta
from unittest import TestCase

from app.control_plane.auth import AuthSettings, Principal, Role
from app.control_plane.session import WorkspaceSession


class AuthAndSessionTests(TestCase):
    def test_principal_requires_identity_and_role(self) -> None:
        principal = Principal.build(
            subject="user-123",
            email="USER@example.com",
            roles=[Role.USER],
        )

        self.assertEqual(principal.email, "user@example.com")
        self.assertTrue(principal.has_role(Role.USER))

    def test_auth_settings_require_real_provider_configuration(self) -> None:
        self.assertFalse(AuthSettings().is_configured())
        self.assertTrue(
            AuthSettings(
                issuer_url="https://issuer.example.com",
                audience="private-ai-workspace",
                admin_group="workspace-admins",
            ).is_configured()
        )

    def test_session_expiration_is_explicit(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        session = WorkspaceSession.create(
            subject="user-123",
            now=now,
            ttl=timedelta(minutes=30),
        )

        self.assertFalse(session.is_expired(now + timedelta(minutes=29)))
        self.assertTrue(session.is_expired(now + timedelta(minutes=30)))
