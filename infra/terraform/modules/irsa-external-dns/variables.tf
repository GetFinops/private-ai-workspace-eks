variable "project_name" {
  description = "Project name used for resource naming and tags."
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
}

variable "oidc_provider_arn" {
  description = "EKS cluster OIDC provider ARN for IRSA."
  type        = string
}

variable "oidc_provider_url" {
  description = "EKS cluster OIDC provider URL (without https://)."
  type        = string
}

variable "service_account_namespace" {
  description = "Namespace of the external-dns ServiceAccount."
  type        = string
  default     = "kube-system"
}

variable "service_account_name" {
  description = "Name of the external-dns ServiceAccount."
  type        = string
  default     = "external-dns"
}

variable "hosted_zone_ids" {
  description = "Route53 hosted-zone ids external-dns may change records in."
  type        = list(string)
}

variable "tags" {
  description = "Additional tags to apply."
  type        = map(string)
  default     = {}
}
