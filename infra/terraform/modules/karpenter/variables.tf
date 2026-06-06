variable "project_name" {
  description = "Project name used for resource naming and tags."
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
}

variable "cluster_name" {
  description = "EKS cluster name."
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
  description = "Kubernetes namespace where the Karpenter controller ServiceAccount lives."
  type        = string
  default     = "kube-system"
}

variable "service_account_name" {
  description = "Kubernetes ServiceAccount name that will assume the controller role."
  type        = string
  default     = "karpenter"
}

variable "tags" {
  description = "Additional tags to apply to all resources."
  type        = map(string)
  default     = {}
}
