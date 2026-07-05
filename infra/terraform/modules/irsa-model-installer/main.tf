# IRSA role for the model-installer reconciler (design Phase 3).
#
# Least-privilege: the reconciler may ONLY scale GPU managed node groups of this
# cluster (eks:UpdateNodegroupConfig / DescribeNodegroup on nodegroups matching
# "*gpu*"). It gets NO secret access, NO cluster-admin, and NO write to any other
# nodegroup (the CPU node group "*cpu*" is not matched). Kubernetes mutation
# (patch the vLLM ConfigMap + Deployment in the inference namespace) is granted
# separately by a namespace-scoped Role in the model-installer Helm chart, NOT by
# this IAM role. The trust policy is pinned to the specific service account.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.42"
    }
  }
}

locals {
  name = "${var.project_name}-${var.environment}"
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:${var.service_account_namespace}:${var.service_account_name}"]
    }
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "installer" {
  name               = "${local.name}-model-installer"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags               = var.tags
}

data "aws_iam_policy_document" "installer" {
  # Scale ONLY GPU node groups of this cluster (name matches "*gpu*"); the CPU
  # node group is deliberately out of scope.
  # Scale ONLY GPU node groups (name contains "gpu"); the CPU node group is not
  # matched. The reconciler is given the exact GPU_NODEGROUP name by config, so no
  # enumerate/list grant is needed. Pin the exact nodegroup ARN if you prefer to
  # drop the wildcard entirely.
  statement {
    sid    = "ScaleGpuNodegroupOnly"
    effect = "Allow"
    actions = [
      "eks:UpdateNodegroupConfig",
      "eks:DescribeNodegroup",
    ]
    resources = [
      "arn:aws:eks:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:nodegroup/${var.cluster_name}/*gpu*/*",
    ]
  }
}

resource "aws_iam_role_policy" "installer" {
  name   = "${local.name}-model-installer"
  role   = aws_iam_role.installer.id
  policy = data.aws_iam_policy_document.installer.json
}
