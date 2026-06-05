variable "project_name" {
  description = "Project name used for resource naming and tags."
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN of the EKS cluster OIDC provider (from modules/eks outputs)."
  type        = string
}

variable "oidc_provider_url" {
  description = "OIDC provider URL without https:// (from modules/eks cluster_oidc_provider_url output)."
  type        = string
}

variable "service_account_namespace" {
  description = "Kubernetes namespace where the control-plane ServiceAccount lives."
  type        = string
  default     = "app"
}

variable "service_account_name" {
  description = "Kubernetes ServiceAccount name that will assume this role."
  type        = string
  default     = "private-ai-workspace-control-plane"
}

variable "rds_secret_arn" {
  description = "Secrets Manager ARN for the RDS master credentials (from modules/rds)."
  type        = string
}

variable "app_config_secret_arn" {
  description = "Secrets Manager ARN for the application config secret (AUTH_ISSUER_URL etc.)."
  type        = string
}

variable "s3_bucket_arn" {
  description = "ARN of the S3 artifacts bucket (from modules/s3)."
  type        = string
}

variable "tags" {
  description = "Additional tags to apply to all resources."
  type        = map(string)
  default     = {}
}
