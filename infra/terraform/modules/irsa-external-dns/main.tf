# IRSA role for external-dns.
#
# external-dns watches Ingresses and syncs their hostnames into Route53, so the
# UI's public DNS record follows the ALB lifecycle automatically (no manual or
# drift-prone Terraform alias to a k8s-managed ALB).
#
# Permissions follow external-dns's documented minimum: change records only in
# the project's hosted zone(s); list zones/records account-wide (required by the
# AWS provider and not scopable to a single zone).

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.42"
    }
  }
}

locals {
  name = "${var.project_name}-${var.environment}-external-dns"
}

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

resource "aws_iam_role" "this" {
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags               = merge(var.tags, { Name = local.name })
}

data "aws_iam_policy_document" "external_dns" {
  statement {
    sid       = "ChangeRecordsInProjectZones"
    effect    = "Allow"
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = [for z in var.hosted_zone_ids : "arn:aws:route53:::hostedzone/${z}"]
  }

  statement {
    sid    = "ListZonesAndRecords"
    effect = "Allow"
    actions = [
      "route53:ListHostedZones",
      "route53:ListResourceRecordSets",
      "route53:ListTagsForResources",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "external_dns" {
  name   = "external-dns"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.external_dns.json
}
