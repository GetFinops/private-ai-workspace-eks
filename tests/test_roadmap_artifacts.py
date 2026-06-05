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

    def test_gap_analysis_report_is_published(self) -> None:
        self.assertTrue((ROOT / "docs" / "11-gap-analysis.md").is_file())

    def test_phase_2_feature_adoption_track_is_published(self) -> None:
        self.assertTrue(
            (ROOT / "docs" / "12-phase-2-feature-adoption.md").is_file()
        )

    def test_milestone_instruction_files_are_published(self) -> None:
        milestones = ROOT / "docs" / "milestones"
        self.assertTrue((milestones / "README.md").is_file())

        expected = [
            "m0-project-bootstrap.md",
            "m1-control-plane-skeleton.md",
            "m2-eks-baseline-deployment.md",
            "m3-state-externalization.md",
            "m4-inference-plane-mvp.md",
            "m5-observability-baseline.md",
            "m6-elastic-gpu-scaling.md",
            "m7-staging-hardening.md",
            "m8-production-release.md",
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue((milestones / name).is_file())

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

    # ── M2 artifact tests ────────────────────────────────────────────────────

    def test_irsa_app_module_exists(self) -> None:
        irsa_dir = ROOT / "infra" / "terraform" / "modules" / "irsa-app"
        for name in ("main.tf", "variables.tf", "outputs.tf"):
            with self.subTest(name=name):
                self.assertTrue((irsa_dir / name).is_file())

    def test_irsa_app_module_scopes_trust_to_service_account(self) -> None:
        main_tf = (ROOT / "infra" / "terraform" / "modules" / "irsa-app" / "main.tf").read_text()
        self.assertIn("system:serviceaccount:", main_tf)
        self.assertIn("sts:AssumeRoleWithWebIdentity", main_tf)

    def test_helm_chart_has_ingress_template(self) -> None:
        ingress = ROOT / "deploy" / "helm" / "private-ai-workspace" / "templates" / "ingress.yaml"
        self.assertTrue(ingress.is_file())
        content = ingress.read_text()
        self.assertIn("alb.ingress.kubernetes.io/scheme", content)
        self.assertIn("ingress.enabled", content)

    def test_helm_chart_has_externalsecret_template(self) -> None:
        es = ROOT / "deploy" / "helm" / "private-ai-workspace" / "templates" / "externalsecret.yaml"
        self.assertTrue(es.is_file())
        content = es.read_text()
        self.assertIn("external-secrets.io/v1beta1", content)
        self.assertIn("ExternalSecret", content)

    def test_helm_values_ingress_disabled_by_default(self) -> None:
        values = (ROOT / "deploy" / "helm" / "private-ai-workspace" / "values.yaml").read_text()
        self.assertIn("enabled: false", values)

    def test_helm_values_nodeSelector_routes_to_control_plane(self) -> None:
        values = (ROOT / "deploy" / "helm" / "private-ai-workspace" / "values.yaml").read_text()
        self.assertIn("private-ai-workspace/plane: control", values)

    def test_cluster_addons_chart_exists(self) -> None:
        addons = ROOT / "deploy" / "helm" / "cluster-addons"
        self.assertTrue((addons / "Chart.yaml").is_file())
        self.assertTrue((addons / "values.yaml").is_file())

    def test_deploy_workflow_exists(self) -> None:
        wf = ROOT / ".github" / "workflows" / "deploy.yml"
        self.assertTrue(wf.is_file())
        content = wf.read_text()
        self.assertIn("amazon-ecr-login", content)
        self.assertIn("helm upgrade", content)
        self.assertIn("OIDC", content)

    def test_dev_app_values_exist(self) -> None:
        self.assertTrue((ROOT / "deploy" / "values" / "dev" / "app.yaml").is_file())
