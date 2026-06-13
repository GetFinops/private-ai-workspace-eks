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
            # Platform baseline (Phase 1)
            "m0-project-bootstrap.md",
            "m1-control-plane-skeleton.md",
            "m2-eks-baseline-deployment.md",
            "m3-state-externalization.md",
            "m4-inference-plane-mvp.md",
            "m5-observability-baseline.md",
            "m6-elastic-gpu-scaling.md",
            # Platform hardening (pre-Phase 2)
            "m7-staging-hardening.md",
            "m7a-platform-hardening-minimal.md",
            # Phase 2 feature track (M9-M14, adoption-gated)
            "m9-product-surface.md",
            "m10-retrieval.md",
            "m11-agent-tool-framework.md",
            "m12-mcp-integration.md",
            "m13-personal-info-integrations.md",
            "m14-media-services.md",
            # Closeout (post-Phase 2)
            "m7b-full-staging-hardening.md",
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
        # ESO 0.20+ serves SecretStore/ExternalSecret only at v1 (v1beta1 is
        # served:false), so the chart must use the GA apiVersion.
        self.assertIn("apiVersion: external-secrets.io/v1\n", content)
        self.assertNotIn("external-secrets.io/v1beta1", content)
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

    # ── M7a artifact tests ───────────────────────────────────────────────────

    def test_m7a_report_is_published(self) -> None:
        self.assertTrue((ROOT / "docs" / "m7a-report.md").is_file())

    def test_m7a_harness_scripts_exist_and_are_executable(self) -> None:
        m7a = ROOT / "scripts" / "m7a"
        expected = [
            "README.md",
            "license-sweep.sh",
            "governance-check.sh",
            "rollback-drill.sh",
            "backup-restore-drill.sh",
        ]
        for name in expected:
            with self.subTest(name=name):
                path = m7a / name
                self.assertTrue(path.is_file(), f"missing: {path}")
                if name.endswith(".sh"):
                    import os
                    self.assertTrue(
                        os.access(path, os.X_OK),
                        f"script is not executable: {path}",
                    )

    def test_m7a_report_references_all_harness_scripts(self) -> None:
        report = (ROOT / "docs" / "m7a-report.md").read_text()
        for script in (
            "scripts/m7a/license-sweep.sh",
            "scripts/m7a/governance-check.sh",
            "scripts/m7a/rollback-drill.sh",
            "scripts/m7a/backup-restore-drill.sh",
        ):
            with self.subTest(script=script):
                self.assertIn(script, report)

    def test_m7a_notice_remediation_records_present(self) -> None:
        notice = (ROOT / "NOTICE").read_text()
        self.assertIn("M7a license-sweep remediations", notice)
        self.assertIn("cryptography", notice)
        self.assertIn("external-secrets", notice)

    # ── M9 — Product Surface ────────────────────────────────────────────────

    def test_m9_notifications_module_exists(self) -> None:
        self.assertTrue(
            (ROOT / "app" / "control_plane" / "notifications.py").is_file()
        )

    def test_m9_notifications_schema_migration_present(self) -> None:
        schema = (ROOT / "app" / "db" / "schema.sql").read_text()
        self.assertIn("notifications", schema)
        self.assertIn("tenant_id", schema)
        self.assertIn("user_id", schema)
        self.assertIn("event_class", schema)

    def test_m9_ui_static_assets_exist(self) -> None:
        ui_static = ROOT / "app" / "ui" / "static"
        expected = ["index.html", "login.html", "app.js", "style.css",
                    "sw.js", "manifest.json"]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue((ui_static / name).is_file(), f"missing: {name}")

    def test_m9_ui_docker_artifacts_exist(self) -> None:
        ui = ROOT / "app" / "ui"
        for name in ["Dockerfile", "nginx.conf", "docker-entrypoint.sh"]:
            with self.subTest(name=name):
                self.assertTrue((ui / name).is_file(), f"missing: {name}")

    def test_m9_ui_helm_chart_exists(self) -> None:
        chart_dir = ROOT / "deploy" / "helm" / "private-ai-ui"
        expected = [
            "Chart.yaml",
            "values.yaml",
            "templates/deployment.yaml",
            "templates/service.yaml",
            "templates/ingress.yaml",
            "templates/configmap.yaml",
            "templates/_helpers.tpl",
        ]
        for name in expected:
            with self.subTest(name=name):
                self.assertTrue((chart_dir / name).is_file(), f"missing: {name}")

    def test_m9_dev_values_exist(self) -> None:
        self.assertTrue(
            (ROOT / "deploy" / "values" / "dev" / "ui.yaml").is_file()
        )

    def test_m9_notice_records_ui_adaptation(self) -> None:
        notice = (ROOT / "NOTICE").read_text()
        self.assertIn("M9 UI adaptation", notice)
        self.assertIn("app/ui/static/style.css", notice)
        self.assertIn("app/ui/static/app.js", notice)
        self.assertIn("Fira Code", notice)

    def test_m9_css_uses_odysseus_variable_names(self) -> None:
        css = (ROOT / "app" / "ui" / "static" / "style.css").read_text()
        for var in ("--bg", "--fg", "--panel", "--border", "--brand-color"):
            with self.subTest(var=var):
                self.assertIn(var, css)

    def test_m9_app_js_uses_textcontent_for_user_content(self) -> None:
        """app.js must render message bubbles via textContent, never innerHTML."""
        js = (ROOT / "app" / "ui" / "static" / "app.js").read_text()
        self.assertIn("textContent", js)

    def test_m9_app_js_has_no_innerhtml_writes(self) -> None:
        """After M9 closeout, app.js must contain no innerHTML write expressions.

        Comments mentioning 'innerHTML' (e.g. negative assertions) are fine.
        We strip them out before checking.
        """
        js = (ROOT / "app" / "ui" / "static" / "app.js").read_text()
        # Strip // line comments before scanning for innerHTML usage.
        stripped_lines = []
        for line in js.splitlines():
            idx = line.find("//")
            stripped_lines.append(line[:idx] if idx >= 0 else line)
        code_only = "\n".join(stripped_lines)
        self.assertNotIn("innerHTML", code_only,
                         "innerHTML write found in app.js code (comments are excluded)")

    def test_m9_csp_allows_oidc_token_endpoint(self) -> None:
        """The SPA must be able to reach the OIDC token endpoint cross-origin."""
        nginx = (ROOT / "app" / "ui" / "nginx.conf").read_text()
        self.assertIn("CONNECT_SRC_OIDC", nginx)
        self.assertIn("form-action", nginx)

    def test_m9_entrypoint_renders_csp_origin(self) -> None:
        entry = (ROOT / "app" / "ui" / "docker-entrypoint.sh").read_text()
        self.assertIn("CONNECT_SRC_OIDC", entry)
        self.assertIn("OIDC_AUTHORIZE_ENDPOINT", entry)
        self.assertIn("OIDC_TOKEN_ENDPOINT", entry)

    def test_m9_security_review_published(self) -> None:
        self.assertTrue((ROOT / "docs" / "m9-security-review.md").is_file())

    def test_m9_notifications_api_routes_wired(self) -> None:
        server = (ROOT / "app" / "control_plane" / "server.py").read_text()
        self.assertIn("/v1/notifications", server)
        self.assertIn("build_notifications_list_response", server)
        self.assertIn("build_notification_publish_response", server)
        self.assertIn("build_notification_read_response", server)
