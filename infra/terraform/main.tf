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

  # Allow the control-plane pods (which egress via the cluster/node security
  # groups under the VPC CNI) to reach PostgreSQL on 5432.
  allowed_security_group_ids = distinct(compact([
    module.eks.cluster_security_group_id,
    module.eks.node_security_group_id,
    module.eks.cluster_primary_security_group_id,
  ]))

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

  project_name = var.project_name
  environment  = var.environment

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

# IRSA for the model-installer reconciler (design Phase 3). Scoped to scale GPU
# node groups only; K8s mutation is granted by a namespace Role in the chart.
module "irsa_model_installer" {
  source = "./modules/irsa-model-installer"

  project_name = var.project_name
  environment  = var.environment
  cluster_name = module.eks.cluster_name

  oidc_provider_arn = module.eks.cluster_oidc_provider_arn
  oidc_provider_url = module.eks.cluster_oidc_provider_url

  service_account_namespace = var.inference_namespace
  service_account_name      = "model-installer"

  tags = local.tags
}

# ── M6 — Elastic GPU Scaling ────────────────────────────────────────────────
# Scaling controllers run on the CPU node group; the Helm charts are installed
# by the cluster-addons umbrella.  Terraform only creates the IAM identities
# the controllers and provisioned nodes need.

module "irsa_cluster_autoscaler" {
  source = "./modules/irsa-cluster-autoscaler"

  project_name = var.project_name
  environment  = var.environment
  cluster_name = module.eks.cluster_name

  oidc_provider_arn = module.eks.cluster_oidc_provider_arn
  oidc_provider_url = module.eks.cluster_oidc_provider_url

  tags = local.tags
}

module "karpenter" {
  source = "./modules/karpenter"

  project_name = var.project_name
  environment  = var.environment
  cluster_name = module.eks.cluster_name

  oidc_provider_arn = module.eks.cluster_oidc_provider_arn
  oidc_provider_url = module.eks.cluster_oidc_provider_url

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

# ── Dev OIDC (Cognito) ───────────────────────────────────────────────────────
# Backs the M9 product-surface sign-in. Dev-only: production points the control
# plane at a real IdP, so this is gated behind enable_dev_cognito.
module "cognito" {
  count  = var.enable_dev_cognito ? 1 : 0
  source = "./modules/cognito"

  pool_name               = "${var.project_name}-${var.environment}"
  client_name             = "private-ai-ui-${var.environment}"
  hosted_ui_domain_prefix = var.cognito_hosted_ui_domain_prefix
  callback_urls           = ["https://${var.ui_host}/callback"]
  logout_urls             = ["https://${var.ui_host}/"]
  test_user_emails        = var.cognito_test_user_emails

  tags = local.tags
}

# ── UI TLS certificate (ACM, DNS-validated via Route53) ──────────────────────
# Created only when both the UI hostname and its hosted-zone id are supplied.
locals {
  enable_ui_cert = var.ui_host != "" && var.acm_route53_zone_id != ""
}

resource "aws_acm_certificate" "ui" {
  count             = local.enable_ui_cert ? 1 : 0
  domain_name       = var.ui_host
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = local.tags
}

resource "aws_route53_record" "ui_cert_validation" {
  for_each = local.enable_ui_cert ? {
    for dvo in aws_acm_certificate.ui[0].domain_validation_options :
    dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  } : {}

  zone_id         = var.acm_route53_zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 300
  records         = [each.value.record]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "ui" {
  count                   = local.enable_ui_cert ? 1 : 0
  certificate_arn         = aws_acm_certificate.ui[0].arn
  validation_record_fqdns = [for r in aws_route53_record.ui_cert_validation : r.fqdn]
}

# ── external-dns IRSA (M10 follow-up) ────────────────────────────────────────
# Lets external-dns sync Ingress hostnames into Route53. Gated on a hosted-zone
# id so it's created only where DNS automation is wanted (dev).
module "irsa_external_dns" {
  count  = var.external_dns_zone_id != "" ? 1 : 0
  source = "./modules/irsa-external-dns"

  project_name = var.project_name
  environment  = var.environment

  oidc_provider_arn = module.eks.cluster_oidc_provider_arn
  oidc_provider_url = module.eks.cluster_oidc_provider_url

  service_account_namespace = "kube-system"
  service_account_name      = "external-dns"
  hosted_zone_ids           = [var.external_dns_zone_id]

  tags = local.tags
}
