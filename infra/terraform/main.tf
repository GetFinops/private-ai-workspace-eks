locals {
  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  # Documented first-pass AWS stack decisions; tested in tests/test_roadmap_artifacts.py.
  starting_stack = {
    kubernetes_platform    = "EKS"
    ingress                = "AWS Load Balancer Controller"
    relational_database    = "RDS PostgreSQL"
    object_storage         = "S3"
    secrets                = "AWS Secrets Manager"
    packaging              = "Helm"
    control_plane_capacity = "CPU managed node group"
    inference_capacity     = "isolated GPU managed node group"
    inference_runtime      = "vLLM OpenAI-compatible API"
  }
}

module "vpc" {
  source = "./modules/vpc"

  project_name              = var.project_name
  environment               = var.environment
  vpc_cidr                  = var.vpc_cidr
  enable_single_nat_gateway = var.enable_single_nat_gateway
  tags                      = local.tags
}

module "ecr" {
  source = "./modules/ecr"

  project_name = var.project_name
  environment  = var.environment
  tags         = local.tags
}

module "eks" {
  source = "./modules/eks"

  project_name       = var.project_name
  environment        = var.environment
  kubernetes_version = var.kubernetes_version

  vpc_id             = module.vpc.vpc_id
  vpc_cidr_block     = module.vpc.vpc_cidr_block
  private_subnet_ids = module.vpc.private_subnet_ids

  control_plane_instance_types = var.control_plane_node_instance_types
  control_plane_desired_size   = var.control_plane_desired_size
  control_plane_min_size       = var.control_plane_min_size
  control_plane_max_size       = var.control_plane_max_size

  gpu_instance_types = var.gpu_node_instance_types
  gpu_desired_size   = var.gpu_desired_size
  gpu_min_size       = var.gpu_min_size
  gpu_max_size       = var.gpu_max_size
  gpu_capacity_type  = var.gpu_capacity_type

  tags = local.tags
}

module "rds" {
  source = "./modules/rds"

  project_name       = var.project_name
  environment        = var.environment
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids

  postgres_version      = var.postgres_version
  instance_class        = var.rds_instance_class
  multi_az              = var.rds_multi_az
  deletion_protection   = var.environment == "prod"
  skip_final_snapshot   = var.environment != "prod"
  backup_retention_days = var.rds_backup_retention_days

  tags = local.tags
}

module "s3" {
  source = "./modules/s3"

  project_name  = var.project_name
  environment   = var.environment
  force_destroy = var.environment != "prod"
  tags          = local.tags
}

# App-level configuration secret: holds AUTH_ISSUER_URL, AUTH_AUDIENCE,
# AUTH_ADMIN_GROUP, and DATABASE_URL (constructed from RDS credentials).
# Values are set out-of-band by operators; Terraform creates the secret
# placeholder so the IRSA policy can reference its ARN.
resource "aws_secretsmanager_secret" "app_config" {
  name                    = "${var.project_name}/${var.environment}/app"
  description             = "Control-plane application configuration for ${var.project_name}/${var.environment}. Values set by operators."
  recovery_window_in_days = var.environment == "prod" ? 30 : 0

  tags = local.tags
}

module "irsa_app" {
  source = "./modules/irsa-app"

  project_name  = var.project_name
  environment   = var.environment

  oidc_provider_arn = module.eks.cluster_oidc_provider_arn
  oidc_provider_url = module.eks.cluster_oidc_provider_url

  service_account_namespace = var.app_namespace
  service_account_name      = var.app_service_account_name

  rds_secret_arn        = module.rds.credentials_secret_arn
  app_config_secret_arn = aws_secretsmanager_secret.app_config.arn
  s3_bucket_arn         = module.s3.bucket_arn

  tags = local.tags
}

# Hugging Face Hub token: operators set the secret value out-of-band via the
# AWS console or CLI.  The secret placeholder is created here so the vLLM
# IRSA policy can reference its ARN before the value is populated.
resource "aws_secretsmanager_secret" "hf_token" {
  name                    = "${var.project_name}/${var.environment}/hf-token"
  description             = "Hugging Face Hub token for gated model downloads (vLLM). Value set by operators."
  recovery_window_in_days = var.environment == "prod" ? 30 : 0

  tags = local.tags
}

module "irsa_vllm" {
  source = "./modules/irsa-vllm"

  project_name = var.project_name
  environment  = var.environment

  oidc_provider_arn = module.eks.cluster_oidc_provider_arn
  oidc_provider_url = module.eks.cluster_oidc_provider_url

  service_account_namespace = var.inference_namespace
  service_account_name      = var.vllm_service_account_name

  hf_token_secret_arn = aws_secretsmanager_secret.hf_token.arn

  tags = local.tags
}

module "github_actions_role" {
  source = "./modules/github-actions-role"

  project_name      = var.project_name
  environment       = var.environment
  github_repository = var.github_repository

  ecr_repository_arn = module.ecr.control_plane_repository_arn
  eks_cluster_arn    = module.eks.cluster_arn

  tags = local.tags
}

# Grant the GitHub Actions deploy role cluster-admin access so the CI runner
# can install cluster-addons (ESO) and deploy the control-plane Helm chart.
# Scope this down to a namespace-scoped role in production.
resource "aws_eks_access_entry" "github_actions" {
  cluster_name  = module.eks.cluster_name
  principal_arn = module.github_actions_role.role_arn
  type          = "STANDARD"
  tags          = local.tags
}

resource "aws_eks_access_policy_association" "github_actions_admin" {
  cluster_name  = module.eks.cluster_name
  principal_arn = module.github_actions_role.role_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [aws_eks_access_entry.github_actions]
}
