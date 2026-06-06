"""M6 elastic-GPU-scaling tests.

Stdlib-only; runs without a live cluster or AWS credentials.

Covers:
  1. Scaling-policy doc presence and required content.
  2. README mentions degrade-only fallback + links the policy doc.
  3. NOTICE includes provenance for cluster-autoscaler, Karpenter,
     prometheus-adapter, and the M6 fallback-decision record.
  4. cluster-addons chart wires the three M6 dependencies (Chart.yaml +
     values.yaml + Chart.lock).
  5. Karpenter EC2NodeClass + NodePool template renders the required spec
     fields (capacity types, GPU taint, GPU limit, consolidation).
  6. vLLM chart ships HPA, ServiceMonitor, and PodDisruptionBudget templates
     wired to the right values toggles.
  7. Terraform: IRSA modules exist with the expected files and at least the
     required permission actions for each controller.
  8. deploy.yml exposes a `deploy_scaling` input and renders all expected
     --set wiring lines.
  9. Per-env values: dev enables HPA, prod example sets warm-pool semantics
     (replicaCount + PDB + min-available).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# 1. Scaling-policy doc
# ─────────────────────────────────────────────────────────────────────────────


class TestScalingPolicyDoc(unittest.TestCase):
    _path = ROOT / "docs/09-scaling-policy.md"

    def test_doc_exists(self) -> None:
        self.assertTrue(self._path.exists())

    def test_doc_documents_warm_pool(self) -> None:
        text = self._path.read_text()
        self.assertIn("Warm pool", text)
        self.assertIn("gpu_warm_pool_size", text)

    def test_doc_documents_degrade_only_fallback(self) -> None:
        text = self._path.read_text()
        self.assertIn("degrade-only", text.lower())
        self.assertIn("does not call external inference providers", text)

    def test_doc_documents_retry_after_table(self) -> None:
        text = self._path.read_text()
        self.assertIn("Retry-After", text)
        self.assertIn("InferenceUnavailableError", text)
        self.assertIn("InferenceRoutingError", text)
        self.assertIn("TimeoutError", text)

    def test_doc_documents_karpenter_constraints(self) -> None:
        text = self._path.read_text()
        self.assertIn("Karpenter", text)
        self.assertIn("spot", text.lower())


# ─────────────────────────────────────────────────────────────────────────────
# 2. README updates
# ─────────────────────────────────────────────────────────────────────────────


class TestReadmeMentionsFallback(unittest.TestCase):
    _readme = ROOT / "README.md"

    def test_readme_states_no_external_fallback(self) -> None:
        text = self._readme.read_text()
        self.assertIn("external-provider", text.lower())
        self.assertIn("docs/09-scaling-policy.md", text)

    def test_readme_documentation_section_links_policy(self) -> None:
        text = self._readme.read_text()
        self.assertIn("Scaling and fallback policy", text)

    def test_readme_m6_status_in_progress_or_complete(self) -> None:
        text = self._readme.read_text()
        self.assertTrue(
            "M6 | Elastic GPU scaling" in text or "M6 | Elastic GPU scaling " in text,
            "M6 row must remain in the milestone table",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. NOTICE provenance
# ─────────────────────────────────────────────────────────────────────────────


class TestNoticeM6(unittest.TestCase):
    _notice = ROOT / "NOTICE"

    def test_cluster_autoscaler_entry(self) -> None:
        text = self._notice.read_text()
        self.assertIn("cluster-autoscaler", text)

    def test_karpenter_entry(self) -> None:
        text = self._notice.read_text()
        self.assertIn("Karpenter", text)

    def test_prometheus_adapter_entry(self) -> None:
        text = self._notice.read_text()
        self.assertIn("prometheus-adapter", text)

    def test_external_fallback_decision_record(self) -> None:
        text = self._notice.read_text()
        self.assertIn("External-provider fallback decision record", text)
        self.assertIn("NOT implemented", text)


# ─────────────────────────────────────────────────────────────────────────────
# 4. cluster-addons chart wiring
# ─────────────────────────────────────────────────────────────────────────────


class TestClusterAddonsChart(unittest.TestCase):
    _chart_yaml = ROOT / "deploy/helm/cluster-addons/Chart.yaml"
    _values_yaml = ROOT / "deploy/helm/cluster-addons/values.yaml"
    _chart_lock = ROOT / "deploy/helm/cluster-addons/Chart.lock"

    def test_chart_yaml_declares_all_m6_deps(self) -> None:
        text = self._chart_yaml.read_text()
        for dep in ("cluster-autoscaler", "karpenter", "prometheus-adapter"):
            self.assertIn(dep, text, f"missing dependency: {dep}")

    def test_chart_lock_pinned_after_dep_update(self) -> None:
        self.assertTrue(self._chart_lock.exists(), "Chart.lock must be committed")
        lock_text = self._chart_lock.read_text()
        for dep in ("cluster-autoscaler", "karpenter", "prometheus-adapter"):
            self.assertIn(dep, lock_text)

    def test_values_have_m6_toggles(self) -> None:
        text = self._values_yaml.read_text()
        for toggle in ("clusterAutoscaler:", "karpenter:", "prometheusAdapter:"):
            self.assertIn(toggle, text)

    def test_prometheus_adapter_rule_targets_vllm_metric(self) -> None:
        text = self._values_yaml.read_text()
        self.assertIn("vllm:num_requests_waiting", text)

    def test_cluster_autoscaler_runs_on_control_plane_nodes(self) -> None:
        text = self._values_yaml.read_text()
        self.assertIn("private-ai-workspace/plane: control", text)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Karpenter NodePool template
# ─────────────────────────────────────────────────────────────────────────────


class TestKarpenterNodePoolTemplate(unittest.TestCase):
    _path = ROOT / "deploy/helm/cluster-addons/templates/karpenter-gpu-nodepool.yaml"

    def test_template_exists(self) -> None:
        self.assertTrue(self._path.exists())

    def test_kinds_present(self) -> None:
        text = self._path.read_text()
        self.assertIn("kind: EC2NodeClass", text)
        self.assertIn("kind: NodePool", text)

    def test_template_is_gated(self) -> None:
        text = self._path.read_text()
        self.assertIn("{{- if .Values.karpenter.enabled }}", text)

    def test_template_applies_gpu_taint(self) -> None:
        text = self._path.read_text()
        self.assertIn("key: nvidia.com/gpu", text)
        self.assertIn("effect: NoSchedule", text)

    def test_template_enforces_gpu_limit(self) -> None:
        text = self._path.read_text()
        self.assertIn('"nvidia.com/gpu": {{ .Values.karpenter.nodePool.maxGpus }}', text)

    def test_template_includes_capacity_type_requirement(self) -> None:
        text = self._path.read_text()
        self.assertIn("karpenter.sh/capacity-type", text)


# ─────────────────────────────────────────────────────────────────────────────
# 6. vLLM HPA + ServiceMonitor + PDB
# ─────────────────────────────────────────────────────────────────────────────


class TestVllmChartScalingTemplates(unittest.TestCase):
    _hpa = ROOT / "deploy/helm/vllm/templates/hpa.yaml"
    _sm = ROOT / "deploy/helm/vllm/templates/servicemonitor.yaml"
    _pdb = ROOT / "deploy/helm/vllm/templates/poddisruptionbudget.yaml"
    _values = ROOT / "deploy/helm/vllm/values.yaml"

    def test_hpa_template_exists(self) -> None:
        self.assertTrue(self._hpa.exists())
        self.assertIn("vllm_num_requests_waiting", self._hpa.read_text())

    def test_servicemonitor_template_exists(self) -> None:
        self.assertTrue(self._sm.exists())
        text = self._sm.read_text()
        self.assertIn("kind: ServiceMonitor", text)
        self.assertIn("{{- if .Values.metrics.serviceMonitor.enabled }}", text)

    def test_pdb_template_exists(self) -> None:
        self.assertTrue(self._pdb.exists())
        text = self._pdb.read_text()
        self.assertIn("kind: PodDisruptionBudget", text)
        self.assertIn("{{- if .Values.podDisruptionBudget.enabled }}", text)

    def test_values_declare_new_blocks(self) -> None:
        text = self._values.read_text()
        for key in ("metrics:", "podDisruptionBudget:", "serviceMonitor:"):
            self.assertIn(key, text)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Terraform IRSA modules
# ─────────────────────────────────────────────────────────────────────────────


class TestTerraformIrsaModules(unittest.TestCase):
    _ca = ROOT / "infra/terraform/modules/irsa-cluster-autoscaler"
    _kp = ROOT / "infra/terraform/modules/karpenter"

    def test_ca_module_files(self) -> None:
        for f in ("main.tf", "variables.tf", "outputs.tf"):
            self.assertTrue((self._ca / f).exists(), f"missing {f}")

    def test_karpenter_module_files(self) -> None:
        for f in ("main.tf", "variables.tf", "outputs.tf"):
            self.assertTrue((self._kp / f).exists(), f"missing {f}")

    def test_ca_module_has_required_actions(self) -> None:
        text = (self._ca / "main.tf").read_text()
        for action in (
            "autoscaling:DescribeAutoScalingGroups",
            "autoscaling:SetDesiredCapacity",
            "ec2:DescribeInstanceTypes",
        ):
            self.assertIn(action, text)

    def test_karpenter_module_has_required_actions(self) -> None:
        text = (self._kp / "main.tf").read_text()
        for action in ("ec2:RunInstances", "ec2:CreateFleet", "iam:PassRole"):
            self.assertIn(action, text)

    def test_karpenter_module_attaches_node_worker_policies(self) -> None:
        text = (self._kp / "main.tf").read_text()
        self.assertIn("AmazonEKSWorkerNodePolicy", text)
        self.assertIn("AmazonEC2ContainerRegistryReadOnly", text)

    def test_root_outputs_expose_new_role_arns(self) -> None:
        text = (ROOT / "infra/terraform/outputs.tf").read_text()
        for name in (
            "irsa_cluster_autoscaler_role_arn",
            "karpenter_controller_role_arn",
            "karpenter_node_role_name",
        ):
            self.assertIn(name, text)

    def test_eks_module_tags_node_groups_for_ca(self) -> None:
        text = (ROOT / "infra/terraform/modules/eks/main.tf").read_text()
        self.assertIn("k8s.io/cluster-autoscaler/enabled", text)


# ─────────────────────────────────────────────────────────────────────────────
# 8. deploy.yml workflow
# ─────────────────────────────────────────────────────────────────────────────


class TestDeployWorkflowM6(unittest.TestCase):
    _wf = ROOT / ".github/workflows/deploy.yml"

    def test_deploy_scaling_input_present(self) -> None:
        text = self._wf.read_text()
        self.assertIn("deploy_scaling", text)

    def test_scaling_step_is_conditional(self) -> None:
        text = self._wf.read_text()
        self.assertIn("deploy_scaling == 'true'", text)

    def test_scaling_step_sets_required_flags(self) -> None:
        text = self._wf.read_text()
        for flag in (
            "clusterAutoscaler.enabled=true",
            "karpenter.enabled=true",
            "prometheusAdapter.enabled=true",
            "cluster-autoscaler.autoDiscovery.clusterName",
            "karpenter.settings.clusterName",
            "karpenter.nodePool.nodeRoleName",
        ):
            self.assertIn(flag, text, f"missing flag: {flag}")

    def test_scaling_verify_step_present(self) -> None:
        text = self._wf.read_text()
        self.assertIn("Verify scaling health", text)
        self.assertIn("v1beta1.custom.metrics.k8s.io", text)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Per-env values
# ─────────────────────────────────────────────────────────────────────────────


class TestEnvValues(unittest.TestCase):
    def test_dev_enables_hpa_and_servicemonitor(self) -> None:
        text = (ROOT / "deploy/values/dev/vllm.yaml").read_text()
        self.assertIn("autoscaling:", text)
        self.assertIn("enabled: true", text)
        self.assertIn("metrics:", text)

    def test_prod_example_demonstrates_warm_pool_pattern(self) -> None:
        path = ROOT / "deploy/values/prod/vllm.yaml.example"
        self.assertTrue(path.exists(), "prod placeholder values file missing")
        text = path.read_text()
        self.assertIn("podDisruptionBudget:", text)
        self.assertIn("minAvailable: 1", text)
        # warm replica
        self.assertIn("replicaCount: 1", text)


if __name__ == "__main__":
    unittest.main()
