# Karpenter IAM scaffolding for GPU dynamic provisioning (M6).
#
# Karpenter requires two distinct identities:
#
#   1. controller role (IRSA) — assumed by the karpenter controller pod;
#      permits launching EC2 instances and managing related resources.
#
#   2. node role (instance profile) — assumed by EC2 instances that
#      Karpenter provisions; allows the kubelet to join the cluster and pull
#      from ECR.  This role is referenced by the Karpenter `EC2NodeClass`
#      custom resource (deployed via the cluster-addons chart).
#
# The Karpenter controller chart and EC2NodeClass / NodePool custom resources
# are installed by the `cluster-addons` Helm chart.  This module only creates
# the AWS identities they need.
#
# Reference: https://karpenter.sh/docs/reference/cloudformation/
# Karpenter license: Apache-2.0.

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

# ─────────────────────────────────────────────────────────────────────────────
# 1. Controller IAM role (IRSA)
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "controller_assume_role" {
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

resource "aws_iam_role" "controller" {
  name               = "${local.name}-karpenter-controller"
  assume_role_policy = data.aws_iam_policy_document.controller_assume_role.json
  tags               = var.tags
}

# Controller permissions per
# https://karpenter.sh/docs/reference/cloudformation/#controllerpolicy
data "aws_iam_policy_document" "controller" {
  statement {
    sid    = "EC2Actions"
    effect = "Allow"
    actions = [
      "ec2:CreateFleet",
      "ec2:CreateLaunchTemplate",
      "ec2:CreateTags",
      "ec2:DeleteLaunchTemplate",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeImages",
      "ec2:DescribeInstanceTypeOfferings",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeInstances",
      "ec2:DescribeLaunchTemplates",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSpotPriceHistory",
      "ec2:DescribeSubnets",
      "ec2:RunInstances",
      "ec2:TerminateInstances",
      "pricing:GetProducts",
      "ssm:GetParameter",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "PassNodeIAMRole"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.node.arn]
  }

  statement {
    sid    = "EKSClusterEndpointLookup"
    effect = "Allow"
    actions = [
      "eks:DescribeCluster",
    ]
    resources = ["arn:aws:eks:*:*:cluster/${var.cluster_name}"]
  }

  # Allow Karpenter to manage the EC2 instance profile it attaches to nodes.
  statement {
    sid    = "ManageInstanceProfile"
    effect = "Allow"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:GetInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "controller" {
  name   = "${local.name}-karpenter-controller"
  role   = aws_iam_role.controller.id
  policy = data.aws_iam_policy_document.controller.json
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Node IAM role (assumed by EC2 instances Karpenter provisions)
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "node_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${local.name}-karpenter-node"
  assume_role_policy = data.aws_iam_policy_document.node_assume_role.json
  tags               = var.tags
}

# Standard EKS worker node policies.
resource "aws_iam_role_policy_attachment" "node_worker" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "node_cni" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "node_ecr" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy_attachment" "node_ssm" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. EKS access entry — let the node role join the cluster
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_eks_access_entry" "karpenter_node" {
  cluster_name  = var.cluster_name
  principal_arn = aws_iam_role.node.arn
  type          = "EC2_LINUX"
}
