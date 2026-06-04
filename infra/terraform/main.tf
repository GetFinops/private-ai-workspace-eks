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

  project_name     = var.project_name
  environment      = var.environment
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

  postgres_version         = var.postgres_version
  instance_class           = var.rds_instance_class
  multi_az                 = var.rds_multi_az
  deletion_protection      = var.environment == "prod"
  skip_final_snapshot      = var.environment != "prod"
  backup_retention_days    = var.rds_backup_retention_days

  tags = local.tags
}

module "s3" {
  source = "./modules/s3"

  project_name  = var.project_name
  environment   = var.environment
  force_destroy = var.environment != "prod"
  tags          = local.tags
}
