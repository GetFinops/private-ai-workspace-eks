from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class RoadmapArtifactTests(TestCase):
    def test_planning_bundle_is_published_under_docs(self) -> None:
        expected = [
            "01-licensing-and-policy.md",
            "03-implementation-plan.md",
            "06-cloud-architecture.md",
            "07-observability.md",
            "09-aws-service-decision-matrix.md",
            "10-delivery-roadmap.md",
        ]

        for name in expected:
            with self.subTest(name=name):
                self.assertTrue((ROOT / "docs" / name).is_file())

    def test_helm_chart_defaults_to_internal_service(self) -> None:
        values = (
            ROOT
            / "deploy"
            / "helm"
            / "private-ai-workspace"
            / "values.yaml"
        ).read_text()

        self.assertIn("type: ClusterIP", values)
        self.assertIn("existingSecret:", values)
        self.assertNotIn("DATABASE_URL: postgresql://", values)

    def test_terraform_baseline_records_required_stack_decisions(self) -> None:
        main_tf = (ROOT / "infra" / "terraform" / "main.tf").read_text()

        self.assertIn('relational_database    = "RDS PostgreSQL"', main_tf)
        self.assertIn('object_storage         = "S3"', main_tf)
        self.assertIn('secrets                = "AWS Secrets Manager"', main_tf)
        self.assertIn('inference_runtime      = "vLLM OpenAI-compatible API"', main_tf)
