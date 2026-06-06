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
      version = ">= 6.42"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.20"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.0"
    }
    http = {
      source  = "hashicorp/http"
      version = ">= 3.0"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  name = "${var.project_name}-${var.environment}"
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = local.name
  kubernetes_version = var.kubernetes_version

  vpc_id     = var.vpc_id
  subnet_ids = var.private_subnet_ids

  endpoint_public_access = true

  enable_cluster_creator_admin_permissions = true

  addons = {
    coredns = {
      most_recent                 = true
      resolve_conflicts_on_create = "OVERWRITE"
    }
    eks-pod-identity-agent = {
      most_recent                 = true
      resolve_conflicts_on_create = "OVERWRITE"
    }
    kube-proxy = {
      most_recent                 = true
      resolve_conflicts_on_create = "OVERWRITE"
    }
    vpc-cni = {
      before_compute              = true
      most_recent                 = true
      resolve_conflicts_on_create = "OVERWRITE"
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
      name                     = "${local.name}-cpu"
      description              = "CPU nodes for the application control plane"
      instance_types           = var.control_plane_instance_types
      capacity_type            = "ON_DEMAND"
      desired_size             = var.control_plane_desired_size
      min_size                 = var.control_plane_min_size
      max_size                 = var.control_plane_max_size
      disk_size                = 50
      iam_role_name            = "${local.name}-cpu-ng"
      iam_role_use_name_prefix = false

      labels = {
        "private-ai-workspace/plane" = "control"
      }
    }

    gpu_inference = {
      name                     = "${local.name}-gpu"
      description              = "GPU nodes for the isolated inference plane (vLLM)"
      instance_types           = var.gpu_instance_types
      capacity_type            = var.gpu_capacity_type
      desired_size             = var.gpu_desired_size
      min_size                 = var.gpu_min_size
      max_size                 = var.gpu_max_size
      disk_size                = 200
      iam_role_name            = "${local.name}-gpu-ng"
      iam_role_use_name_prefix = false

      ami_type = "AL2023_x86_64_NVIDIA"

      labels = {
        "private-ai-workspace/plane"  = "inference"
        "nvidia.com/gpu.present"       = "true"
      }

      taints = {
        nvidia_gpu = {
          key    = "nvidia.com/gpu"
          value  = "true"
          effect = "NO_SCHEDULE"
        }
      }
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

data "http" "aws_lb_controller_policy" {
  url = "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v2.9.0/docs/install/iam_policy.json"
}

resource "aws_iam_policy" "aws_lb_controller" {
  name   = "${local.name}-aws-lb-controller"
  policy = data.http.aws_lb_controller_policy.response_body
}

resource "aws_iam_role_policy_attachment" "aws_lb_controller" {
  role       = aws_iam_role.aws_lb_controller.name
  policy_arn = aws_iam_policy.aws_lb_controller.arn
}

resource "helm_release" "aws_lb_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version         = "3.4.0"
  wait            = true
  timeout         = 600
  cleanup_on_fail = true

  set = [
    {
      name  = "clusterName"
      value = module.eks.cluster_name
    },
    {
      name  = "serviceAccount.create"
      value = "true"
    },
    {
      name  = "serviceAccount.name"
      value = "aws-load-balancer-controller"
    },
    {
      name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
      value = aws_iam_role.aws_lb_controller.arn
    },
  ]

  depends_on = [module.eks]
}
