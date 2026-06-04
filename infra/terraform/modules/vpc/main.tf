# VPC module for private-ai-workspace EKS deployment.
#
# Adapted from aws-samples/sample-genai-on-eks-starter-kit (MIT-0).
# Source: https://github.com/aws-samples/sample-genai-on-eks-starter-kit/blob/main/terraform/vpc.tf
# Modifications: renamed variables, removed EFS subnet, added environment tag,
# parameterised single/multi NAT gateway, added project-scoped resource names.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

data "aws_availability_zones" "available" {}

locals {
  name = "${var.project_name}-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, min(length(data.aws_availability_zones.available.names), 3))
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.21.0"

  name = local.name
  cidr = var.vpc_cidr

  azs             = local.azs
  private_subnets = [for k, v in local.azs : cidrsubnet(var.vpc_cidr, 4, k)]
  public_subnets  = [for k, v in local.azs : cidrsubnet(var.vpc_cidr, 8, k + 64)]

  enable_nat_gateway   = true
  single_nat_gateway   = var.enable_single_nat_gateway
  enable_dns_hostnames = true
  enable_dns_support   = true

  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = 1
    "karpenter.sh/discovery"          = local.name
  }

  tags = merge(var.tags, {
    Name = local.name
  })
}
