# GitHub Actions OIDC deploy role for private-ai-workspace.
#
# Creates a single IAM role trusted by GitHub Actions OIDC, scoped to this
# repository, with permissions to:
#   - Push images to the control-plane ECR repository.
#   - Describe the EKS cluster (for aws eks update-kubeconfig).
#   - Obtain EKS tokens (aws eks get-token) so Helm can deploy.
#
# The role is registered as an EKS access entry so the GitHub Actions
# runner can manage Kubernetes resources without a static kubeconfig.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.42"
    }
  }
}

locals {
  name           = "${var.project_name}-${var.environment}"
  oidc_issuer    = "token.actions.githubusercontent.com"
  oidc_issuer_url = "https://token.actions.githubusercontent.com"
}

# ── GitHub Actions OIDC provider ─────────────────────────────────────────────
# AWS validates the JWT signature against GitHub's published JWKS; the
# thumbprint list is kept as a placeholder — AWS uses the upstream CA for
# token.actions.githubusercontent.com.

data "tls_certificate" "github_actions" {
  url = "${local.oidc_issuer_url}/.well-known/openid-configuration"
}

resource "aws_iam_openid_connect_provider" "github_actions" {
  url             = local.oidc_issuer_url
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github_actions.certificates[0].sha1_fingerprint]

  tags = var.tags
}

# ── Deploy IAM role ───────────────────────────────────────────────────────────

data "aws_iam_policy_document" "assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.oidc_issuer}:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "${local.oidc_issuer}:sub"
      # Allow any ref (branch, tag, PR) in the repository.
      # Tighten to "repo:${var.github_repository}:ref:refs/heads/main"
      # for production environments.
      values = ["repo:${var.github_repository}:*"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "${local.name}-gha-deploy"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags               = var.tags
}

# ── ECR push permissions ──────────────────────────────────────────────────────

data "aws_iam_policy_document" "ecr_push" {
  statement {
    sid    = "ECRAuth"
    effect = "Allow"
    actions = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "ECRPush"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
    ]
    # Scope to every repository under the project's ECR namespace
    # (e.g. .../private-ai-workspace-dev/*) so the deploy role can push all
    # service images — control-plane, ui, and future ones — not just one repo.
    resources = ["${dirname(var.ecr_repository_arn)}/*"]
  }
}

resource "aws_iam_role_policy" "ecr_push" {
  name   = "ecr-push"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.ecr_push.json
}

# ── EKS describe + token permissions ─────────────────────────────────────────

data "aws_iam_policy_document" "eks_deploy" {
  statement {
    sid    = "EKSDescribe"
    effect = "Allow"
    actions = [
      "eks:DescribeCluster",
      "eks:ListClusters",
    ]
    resources = [var.eks_cluster_arn]
  }
}

resource "aws_iam_role_policy" "eks_deploy" {
  name   = "eks-deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.eks_deploy.json
}
