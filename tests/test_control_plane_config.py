from unittest import TestCase

from app.control_plane.config import ControlPlaneConfig


class ControlPlaneConfigTests(TestCase):
    def test_defaults_do_not_assume_local_state(self) -> None:
        config = ControlPlaneConfig.from_env({})

        self.assertEqual(
            config.service_name,
            "private-ai-workspace-control-plane",
        )
        self.assertIsNone(config.database_url)
        self.assertIsNone(config.object_storage_bucket)
        self.assertFalse(config.is_ready())

    def test_readiness_requires_external_state_configuration(self) -> None:
        config = ControlPlaneConfig.from_env(
            {
                "DATABASE_URL": "postgresql://db.example/workspace",
                "OBJECT_STORAGE_BUCKET": "workspace-artifacts",
                "SECRETS_PROVIDER": "aws-secrets-manager",
                "INFERENCE_BASE_URL": "http://vllm.inference.svc:8000",
                "AUTH_ISSUER_URL": "https://issuer.example.com",
                "AUTH_AUDIENCE": "private-ai-workspace",
                "AUTH_ADMIN_GROUP": "workspace-admins",
            }
        )

        self.assertTrue(config.is_ready())
        self.assertEqual(
            config.readiness_checks(),
            {
                "database_configured": True,
                "object_storage_configured": True,
                "secrets_provider_configured": True,
                "inference_configured": True,
                "auth_configured": True,
            },
        )
