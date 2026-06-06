"""M4 inference-plane tests.

Covers:
  1. NetworkPolicy chart template — enabled/disabled flag.
  2. ExternalSecret / SecretStore chart templates — enabled/disabled flag.
  3. Retry-After header on all three degraded-mode 503 paths.
  4. vLLM chart artifacts — chart directory structure and required templates.
  5. deploy.yml workflow — presence of deploy_inference input and vLLM step.

All tests are stdlib-only and run without a live cluster or network access.
"""

from __future__ import annotations

import os
import re
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent

# ──────────────────────────────────────────────────────────────────────────────
# 1. NetworkPolicy template gate
# ──────────────────────────────────────────────────────────────────────────────


class TestNetworkPolicyTemplate(unittest.TestCase):
    """The networkpolicy.yaml template must contain the enabled guard."""

    _template = ROOT / "deploy/helm/vllm/templates/networkpolicy.yaml"

    def test_template_exists(self) -> None:
        self.assertTrue(self._template.exists(), "networkpolicy.yaml template missing")

    def test_enabled_gate_present(self) -> None:
        text = self._template.read_text()
        self.assertIn("{{- if .Values.networkPolicy.enabled }}", text)

    def test_control_plane_namespace_reference(self) -> None:
        text = self._template.read_text()
        self.assertIn(".Values.networkPolicy.controlPlaneNamespace", text)

    def test_service_port_reference(self) -> None:
        text = self._template.read_text()
        self.assertIn(".Values.service.port", text)

    def test_network_policy_defaults_enabled(self) -> None:
        values_text = (ROOT / "deploy/helm/vllm/values.yaml").read_text()
        # Default in the base chart should be true for security-by-default.
        self.assertIn("enabled: true", values_text)

    def test_default_control_plane_namespace(self) -> None:
        values_text = (ROOT / "deploy/helm/vllm/values.yaml").read_text()
        self.assertIn("controlPlaneNamespace: app", values_text)


# ──────────────────────────────────────────────────────────────────────────────
# 2. ExternalSecret template gate
# ──────────────────────────────────────────────────────────────────────────────


class TestExternalSecretTemplate(unittest.TestCase):
    """The externalsecret.yaml template must contain the enabled guard and key refs."""

    _template = ROOT / "deploy/helm/vllm/templates/externalsecret.yaml"

    def test_template_exists(self) -> None:
        self.assertTrue(self._template.exists(), "externalsecret.yaml template missing")

    def test_enabled_gate_present(self) -> None:
        text = self._template.read_text()
        self.assertIn("{{- if .Values.externalSecrets.enabled }}", text)

    def test_secret_store_kind(self) -> None:
        text = self._template.read_text()
        self.assertIn("kind: SecretStore", text)

    def test_external_secret_kind(self) -> None:
        text = self._template.read_text()
        self.assertIn("kind: ExternalSecret", text)

    def test_hf_token_key_reference(self) -> None:
        text = self._template.read_text()
        self.assertIn("HUGGING_FACE_HUB_TOKEN", text)

    def test_hf_token_secret_name_reference(self) -> None:
        text = self._template.read_text()
        self.assertIn(".Values.externalSecrets.hfTokenSecretName", text)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Retry-After header on degraded 503 responses
# ──────────────────────────────────────────────────────────────────────────────


class TestRetryAfterHeader(unittest.TestCase):
    """build_chat_response must include Retry-After on all degraded 503 paths."""

    def _make_config(self) -> object:
        from app.control_plane.config import ControlPlaneConfig

        return ControlPlaneConfig(
            service_name="test",
            environment="production",
            log_level="INFO",
            inference_base_url="http://vllm.inference.svc:8000",
        )

    def _make_verifier(self, succeed: bool = True) -> object:
        verifier = MagicMock()
        if not succeed:
            from app.control_plane.token_verifier import TokenVerificationError

            verifier.verify.side_effect = TokenVerificationError("bad token")
        return verifier

    def test_inference_unavailable_has_retry_after(self) -> None:
        from app.control_plane.routing import InferenceUnavailableError
        from app.control_plane.server import build_chat_response

        config = self._make_config()
        verifier = self._make_verifier()
        body = b'{"model": "mistralai/Mistral-7B-Instruct-v0.3", "messages": [{"role": "user", "content": "hi"}]}'

        with patch(
            "app.control_plane.server.VLLMInferenceClient.chat_completions",
            side_effect=InferenceUnavailableError("down", status_code=503),
        ):
            resp = build_chat_response(
                authorization="Bearer token",
                body=body,
                config=config,
                token_verifier=verifier,
            )

        self.assertEqual(resp.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertIsNotNone(resp.headers)
        self.assertIn("Retry-After", resp.headers)  # type: ignore[arg-type]
        self.assertIn("retry_after", resp.payload)

    def test_inference_routing_error_has_retry_after(self) -> None:
        from app.control_plane.routing import InferenceRoutingError
        from app.control_plane.server import build_chat_response

        config = self._make_config()
        verifier = self._make_verifier()
        body = b'{"model": "mistralai/Mistral-7B-Instruct-v0.3", "messages": [{"role": "user", "content": "hi"}]}'

        with patch(
            "app.control_plane.server.VLLMInferenceClient.chat_completions",
            side_effect=InferenceRoutingError("unreachable"),
        ):
            resp = build_chat_response(
                authorization="Bearer token",
                body=body,
                config=config,
                token_verifier=verifier,
            )

        self.assertEqual(resp.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertIsNotNone(resp.headers)
        self.assertIn("Retry-After", resp.headers)  # type: ignore[arg-type]

    def test_timeout_error_has_retry_after(self) -> None:
        from app.control_plane.server import build_chat_response

        config = self._make_config()
        verifier = self._make_verifier()
        body = b'{"model": "mistralai/Mistral-7B-Instruct-v0.3", "messages": [{"role": "user", "content": "hi"}]}'

        with patch(
            "app.control_plane.server.VLLMInferenceClient.chat_completions",
            side_effect=TimeoutError("timed out"),
        ):
            resp = build_chat_response(
                authorization="Bearer token",
                body=body,
                config=config,
                token_verifier=verifier,
            )

        self.assertEqual(resp.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertIsNotNone(resp.headers)
        self.assertIn("Retry-After", resp.headers)  # type: ignore[arg-type]
        self.assertIn("inference_timeout", resp.payload["error"])

    def test_capacity_exhausted_longer_retry(self) -> None:
        """429 from vLLM should produce a longer Retry-After than a plain 503."""
        from app.control_plane.routing import InferenceUnavailableError
        from app.control_plane.server import build_chat_response

        config = self._make_config()
        verifier = self._make_verifier()
        body = b'{"model": "mistralai/Mistral-7B-Instruct-v0.3", "messages": [{"role": "user", "content": "hi"}]}'

        with patch(
            "app.control_plane.server.VLLMInferenceClient.chat_completions",
            side_effect=InferenceUnavailableError("429 Too Many Requests", status_code=429),
        ):
            resp = build_chat_response(
                authorization="Bearer token",
                body=body,
                config=config,
                token_verifier=verifier,
            )

        self.assertEqual(resp.status_code, HTTPStatus.SERVICE_UNAVAILABLE)
        retry_after_503 = 30
        retry_after_429 = int(resp.headers["Retry-After"])  # type: ignore[index]
        self.assertGreater(retry_after_429, retry_after_503)


# ──────────────────────────────────────────────────────────────────────────────
# 4. vLLM chart artifact structure
# ──────────────────────────────────────────────────────────────────────────────


class TestVLLMChartArtifacts(unittest.TestCase):
    """The vllm Helm chart must contain required templates and Chart.yaml."""

    _chart = ROOT / "deploy/helm/vllm"

    def test_chart_yaml_exists(self) -> None:
        self.assertTrue((self._chart / "Chart.yaml").exists())

    def test_values_yaml_exists(self) -> None:
        self.assertTrue((self._chart / "values.yaml").exists())

    def test_deployment_template_exists(self) -> None:
        self.assertTrue((self._chart / "templates/deployment.yaml").exists())

    def test_service_template_exists(self) -> None:
        self.assertTrue((self._chart / "templates/service.yaml").exists())

    def test_serviceaccount_template_exists(self) -> None:
        self.assertTrue((self._chart / "templates/serviceaccount.yaml").exists())

    def test_networkpolicy_template_exists(self) -> None:
        self.assertTrue((self._chart / "templates/networkpolicy.yaml").exists())

    def test_externalsecret_template_exists(self) -> None:
        self.assertTrue((self._chart / "templates/externalsecret.yaml").exists())

    def test_irsa_annotation_in_serviceaccount(self) -> None:
        text = (self._chart / "templates/serviceaccount.yaml").read_text()
        self.assertIn("eks.amazonaws.com/role-arn", text)
        self.assertIn(".Values.serviceAccount.irsaRoleArn", text)

    def test_dev_values_model_is_mistral(self) -> None:
        dev_values = (ROOT / "deploy/values/dev/vllm.yaml").read_text()
        self.assertIn("mistralai/Mistral-7B-Instruct-v0.3", dev_values)

    def test_dev_values_external_secrets_enabled(self) -> None:
        dev_values = (ROOT / "deploy/values/dev/vllm.yaml").read_text()
        self.assertIn("enabled: true", dev_values)


# ──────────────────────────────────────────────────────────────────────────────
# 5. deploy.yml workflow — deploy_inference input + vLLM step
# ──────────────────────────────────────────────────────────────────────────────


class TestDeployWorkflow(unittest.TestCase):
    """deploy.yml must declare a deploy_inference workflow input and a vLLM step."""

    _workflow = ROOT / ".github/workflows/deploy.yml"

    def test_workflow_exists(self) -> None:
        self.assertTrue(self._workflow.exists())

    def test_deploy_inference_input_present(self) -> None:
        text = self._workflow.read_text()
        self.assertIn("deploy_inference", text)

    def test_vllm_helm_step_present(self) -> None:
        text = self._workflow.read_text()
        self.assertIn("deploy/helm/vllm", text)

    def test_vllm_step_is_conditional(self) -> None:
        text = self._workflow.read_text()
        self.assertIn("deploy_inference == 'true'", text)

    def test_irsa_vllm_var_referenced(self) -> None:
        text = self._workflow.read_text()
        self.assertIn("IRSA_VLLM_ROLE_ARN", text)

    def test_hf_token_var_referenced(self) -> None:
        text = self._workflow.read_text()
        self.assertIn("HF_TOKEN_SECRET_NAME", text)

    def test_inference_health_check_present(self) -> None:
        text = self._workflow.read_text()
        self.assertIn("Verify inference health", text)

    def test_inference_namespace_used(self) -> None:
        text = self._workflow.read_text()
        self.assertIn("--namespace inference", text)


if __name__ == "__main__":
    unittest.main()
