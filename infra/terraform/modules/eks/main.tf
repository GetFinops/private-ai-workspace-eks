# EKS cluster module for private-ai-workspace.
#
# Adapted from aws-samples/sample-genai-on-eks-starter-kit (MIT-0).
# Source: https://github.com/aws-samples/sample-genai-on-eks-starter-kit/blob/main/terraform/modules/eks-standard-mode/eks.tf
# Source: https://github.com/aws-samples/sample-genai-on-eks-starter-kit/blob/main/terraform/modules/eks-standard-mode/eks-addons.tf
# Modifications: two explicit managed node groups (CPU control-plane + GPU inference);
# removed EFS/LWS; simplified Karpenter to optional data source only; added
# AWS Load Balancer Controller; added gpu_capacity_type variable; scoped to our
# project naming convention; removed CLI-specific local-exec provisioners.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.20"
    }
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.10"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  name = "${var.project_name}-${var.environment}"
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.3.1"

  name               = local.name
  kubernetes_version = var.kubernetes_version

  vpc_id     = var.vpc_id
  subnet_ids = var.private_subnet_ids

  endpoint_public_access = true

  enable_cluster_creator_admin_permissions = true

  cluster_addons = {
    coredns                = { most_recent = true }
    eks-pod-identity-agent = { most_recent = true }
    kube-proxy             = { most_recent = true }
    vpc-cni = {
      before_compute = true
      most_recent    = true
      configuration_values = jsonencode({
        env = {
          ENABLE_PREFIX_DELEGATION = "true"
          WARM_PREFIX_TARGET       = "1"
        }
      })
    }
  }

  node_security_group_additional_rules = {
    intra_vpc = {
      description = "Allow all traffic within the VPC (node-to-node and node-to-cluster)"
      type        = "ingress"
      from_port   = 0
      to_port     = 0
      protocol    = "-1"
      cidr_blocks = [var.vpc_cidr_block]
    }
  }

  eks_managed_node_groups = {
    control_plane = {
      name            = "${local.name}-cpu"
      description     = "CPU nodes for the application control plane"
      instance_types  = var.control_plane_instance_types
      capacity_type   = "ON_DEMAND"
      desired_size    = var.control_plane_desired_size
      min_size        = var.control_plane_min_size
      max_size        = var.control_plane_max_size
      disk_size       = 50

      labels = {
        "private-ai-workspace/plane" = "control"
      }
    }

    gpu_inference = {
      name            = "${local.name}-gpu"
      description     = "GPU nodes for the isolated inference plane (vLLM)"
      instance_types  = var.gpu_instance_types
      capacity_type   = var.gpu_capacity_type
      desired_size    = var.gpu_desired_size
      min_size        = var.gpu_min_size
      max_size        = var.gpu_max_size
      disk_size       = 200

      ami_type = "AL2_x86_64_GPU"

      labels = {
        "private-ai-workspace/plane"  = "inference"
        "nvidia.com/gpu.present"       = "true"
      }

      taints = [
        {
          key    = "nvidia.com/gpu"
          value  = "true"
          effect = "NO_SCHEDULE"
        }
      ]
    }
  }

  tags = merge(var.tags, {
    "karpenter.sh/discovery" = local.name
  })
}

# AWS Load Balancer Controller — routes public ALB traffic into the cluster.
# Required for Kubernetes Ingress resources backed by Application Load Balancers.
resource "aws_iam_role" "aws_lb_controller" {
  name = "${local.name}-aws-lb-controller"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = module.eks.oidc_provider_arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${replace(module.eks.oidc_provider_arn, "/^(.*provider/)/", "")}:sub" = "system:serviceaccount:kube-system:aws-load-balancer-controller"
          "${replace(module.eks.oidc_provider_arn, "/^(.*provider/)/", "")}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = var.tags
}

data "aws_iam_policy" "aws_lb_controller" {
  name = "AWSLoadBalancerControllerIAMPolicy"
}

resource "aws_iam_role_policy_attachment" "aws_lb_controller" {
  role       = aws_iam_role.aws_lb_controller.name
  policy_arn = data.aws_iam_policy.aws_lb_controller.arn
}

resource "helm_release" "aws_lb_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = "1.12.0"
  wait       = true

  set {
    name  = "clusterName"
    value = module.eks.cluster_name
  }

  set {
    name  = "serviceAccount.create"
    value = "true"
  }

  set {
    name  = "serviceAccount.name"
    value = "aws-load-balancer-controller"
  }

  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.aws_lb_controller.arn
  }

  depends_on = [module.eks]
}
