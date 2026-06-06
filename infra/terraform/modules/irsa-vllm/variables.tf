variable "project_name" {
  description = "Project name used for resource naming and tags."
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN of the EKS cluster OIDC provider."
  type        = string
}

variable "oidc_provider_url" {
  description = "OIDC provider URL without https:// (from modules/eks cluster_oidc_provider_url output)."
  type        = string
}

variable "service_account_namespace" {
  description = "Kubernetes namespace where the vLLM ServiceAccount lives."
  type        = string
  default     = "inference"
}

variable "service_account_name" {
  description = "Kubernetes ServiceAccount name that will assume this role."
  type        = string
  default     = "vllm-inference"
}

variable "hf_token_secret_arn" {
  description = "Secrets Manager ARN for the Hugging Face Hub token secret."
  type        = string
}

variable "tags" {
  description = "Additional tags to apply to all resources."
  type        = map(string)
  default     = {}
}
