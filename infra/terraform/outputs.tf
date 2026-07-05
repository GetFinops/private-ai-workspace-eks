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

output "github_actions_deploy_role_arn" {
  description = "IAM role ARN for GitHub Actions CI/CD. Set as AWS_DEPLOY_ROLE_ARN in GitHub Actions secrets."
  value       = module.github_actions_role.role_arn
}

output "hf_token_secret_name" {
  description = "Secrets Manager secret name for the Hugging Face Hub token. Populate value out-of-band before deploying vLLM."
  value       = aws_secretsmanager_secret.hf_token.name
}

output "irsa_vllm_role_arn" {
  description = "IAM role ARN to annotate on the vLLM ServiceAccount (IRSA)."
  value       = module.irsa_vllm.role_arn
}

# ── M6 — Elastic GPU Scaling ────────────────────────────────────────────────

output "irsa_cluster_autoscaler_role_arn" {
  description = "IAM role ARN to annotate on the cluster-autoscaler ServiceAccount."
  value       = module.irsa_cluster_autoscaler.role_arn
}

output "karpenter_controller_role_arn" {
  description = "IAM role ARN to annotate on the karpenter ServiceAccount (controller)."
  value       = module.karpenter.controller_role_arn
}

output "karpenter_node_role_name" {
  description = "Name of the IAM role that Karpenter-provisioned EC2 instances assume. Reference this in EC2NodeClass.spec.role."
  value       = module.karpenter.node_role_name
}

output "ecr_ui_url" {
  description = "ECR repository URL for the UI image."
  value       = module.ecr.ui_repository_url
}

output "cognito_issuer_url" {
  description = "Dev Cognito OIDC issuer URL (AUTH_ISSUER_URL / OIDC_ISSUER), or null when disabled."
  value       = try(module.cognito[0].issuer_url, null)
}

output "cognito_client_id" {
  description = "Dev Cognito app client id (AUTH_AUDIENCE / OIDC_CLIENT_ID), or null when disabled."
  value       = try(module.cognito[0].client_id, null)
}

output "cognito_hosted_ui_domain" {
  description = "Dev Cognito hosted-UI domain (serves /oauth2/authorize and /oauth2/token), or null when disabled."
  value       = try(module.cognito[0].hosted_ui_domain, null)
}

output "ui_acm_certificate_arn" {
  description = "ACM certificate ARN for the UI ingress, or null when not created."
  value       = try(aws_acm_certificate.ui[0].arn, null)
}

output "external_dns_role_arn" {
  description = "IRSA role ARN for external-dns, or null when disabled."
  value       = try(module.irsa_external_dns[0].role_arn, null)
}

output "model_installer_role_arn" {
  description = "IRSA role ARN for the model-installer reconciler (scoped)."
  value       = module.irsa_model_installer.role_arn
}
