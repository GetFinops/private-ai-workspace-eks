# IRSA role for the control-plane application pod.
#
# New implementation following the security checkpoints in
# docs/milestones/m2-eks-baseline-deployment.md:
# - Role-based pod access to AWS services (no static credentials).
# - Secrets Manager read access scoped to specific secret ARNs.
# - S3 read/write access scoped to the artifacts bucket only.
# - Trust policy scoped to the specific EKS ServiceAccount (namespace + name).

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

  # M13 per-tenant integration credentials live under a dedicated Secrets Manager
  # prefix, distinct from the platform's own RDS/app-config secrets. The IRSA
  # grant below is scoped to this prefix only — never all secrets.
  integrations_secret_prefix = "${var.project_name}/${var.environment}/integrations/"
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_partition" "current" {}

# ── Trust policy ──────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }

    actions = ["sts:AssumeRoleWithWebIdentity"]

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

# ── IAM role ──────────────────────────────────────────────────────────────────

resource "aws_iam_role" "app" {
  name               = "${local.name}-app"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags               = var.tags
}

# ── Permissions policy ────────────────────────────────────────────────────────

data "aws_iam_policy_document" "app" {
  # Read RDS and app-config secrets from Secrets Manager.
  statement {
    sid    = "SecretsManagerRead"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      var.rds_secret_arn,
      # Wildcard covers rotation staging suffixes added by Secrets Manager.
      "${var.rds_secret_arn}-*",
      var.app_config_secret_arn,
      "${var.app_config_secret_arn}-*",
    ]
  }

  # M13: read per-tenant integration credentials. Scoped to the integrations
  # prefix only (with the rotation-staging "-*" suffix), so the control plane can
  # never read another component's secrets via this grant. Per-tenant isolation
  # itself is enforced in app code (integration_secrets.build_secret_id), which
  # derives the secret id from the verified token's tenant.
  statement {
    sid    = "SecretsManagerIntegrationsRead"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret:${local.integrations_secret_prefix}*",
    ]
  }

  # Read and write artifacts to S3.
  statement {
    sid    = "S3Objects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${var.s3_bucket_arn}/*"]
  }

  statement {
    sid       = "S3ListBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.s3_bucket_arn]
  }
}

resource "aws_iam_role_policy" "app" {
  name   = "${local.name}-app"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app.json
}
