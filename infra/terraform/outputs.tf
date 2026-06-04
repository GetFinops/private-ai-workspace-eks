output "starting_stack" {
  description = "Documented first-pass AWS stack decisions for implementation."
  value       = local.starting_stack
}

output "control_plane_node_instance_types" {
  description = "Selected CPU node types for the control-plane baseline."
  value       = var.control_plane_node_instance_types
}

output "gpu_node_instance_types" {
  description = "Selected GPU node types for the inference-plane baseline."
  value       = var.gpu_node_instance_types
}

output "vpc_id" {
  description = "VPC ID for the cluster."
  value       = module.vpc.vpc_id
}

output "eks_cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "ecr_control_plane_url" {
  description = "ECR repository URL for the control-plane image."
  value       = module.ecr.control_plane_repository_url
}

output "rds_credentials_secret_arn" {
  description = "Secrets Manager ARN for the RDS master credentials."
  value       = module.rds.credentials_secret_arn
}

output "app_config_secret_arn" {
  description = "Secrets Manager ARN for the application config secret (set values out-of-band)."
  value       = aws_secretsmanager_secret.app_config.arn
}

output "app_config_secret_name" {
  description = "Secrets Manager secret name for the application config (for ExternalSecrets reference)."
  value       = aws_secretsmanager_secret.app_config.name
}

output "s3_artifacts_bucket" {
  description = "S3 artifacts bucket name."
  value       = module.s3.bucket_name
}

output "irsa_app_role_arn" {
  description = "IAM role ARN to set on the control-plane ServiceAccount annotation."
  value       = module.irsa_app.role_arn
}
