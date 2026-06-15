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

    def test_integrations_disabled_by_default(self) -> None:
        config = ControlPlaneConfig.from_env({})
        # M13 kill-switch: deny by default — integrations off and no allow-list.
        self.assertFalse(config.integrations_enabled)
        self.assertIsNone(config.integrations_allowlist)
        self.assertEqual(config.integrations_rate_per_minute, 30)
        self.assertEqual(config.integrations_max_concurrency, 4)
        self.assertEqual(config.integrations_outbound_timeout_s, 10.0)

    def test_integrations_settings_parsed_from_env(self) -> None:
        config = ControlPlaneConfig.from_env(
            {
                "INTEGRATIONS_ENABLED": "true",
                "INTEGRATIONS_ALLOWLIST": '{"tenant-a.test": ["calendar"]}',
                "INTEGRATIONS_RATE_PER_MINUTE": "12",
                "INTEGRATIONS_MAX_CONCURRENCY": "2",
                "INTEGRATIONS_OUTBOUND_TIMEOUT_S": "5",
            }
        )
        self.assertTrue(config.integrations_enabled)
        self.assertEqual(config.integrations_allowlist, '{"tenant-a.test": ["calendar"]}')
        self.assertEqual(config.integrations_rate_per_minute, 12)
        self.assertEqual(config.integrations_max_concurrency, 2)
        self.assertEqual(config.integrations_outbound_timeout_s, 5.0)

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
