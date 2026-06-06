# IRSA role for the vLLM inference pod.
#
# Grants the vLLM ServiceAccount permission to read the Hugging Face Hub
# token from Secrets Manager so the pod can download gated model weights
# without embedding credentials.
#
# The trust policy is scoped to the specific service account namespace and
# name so no other pod can assume this role.

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

# ── Trust policy ──────────────────────────────────────────────────────────────

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

# ── IAM role ──────────────────────────────────────────────────────────────────

resource "aws_iam_role" "vllm" {
  name               = "${local.name}-vllm"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags               = var.tags
}

# ── Permissions: read the HF token from Secrets Manager ──────────────────────

data "aws_iam_policy_document" "vllm" {
  statement {
    sid    = "ReadHFToken"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      var.hf_token_secret_arn,
      "${var.hf_token_secret_arn}-*",
    ]
  }
}

resource "aws_iam_role_policy" "vllm" {
  name   = "${local.name}-vllm"
  role   = aws_iam_role.vllm.id
  policy = data.aws_iam_policy_document.vllm.json
}
