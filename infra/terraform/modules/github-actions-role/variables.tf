variable "project_name" {
  description = "Project name used for resource naming and tags."
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
}

variable "github_repository" {
  description = "GitHub repository in org/repo format trusted by the deploy role (e.g. GetFinops/private-ai-workspace-eks)."
  type        = string
}

variable "ecr_repository_arn" {
  description = "ARN of the ECR repository the deploy role may push images to."
  type        = string
}

variable "eks_cluster_arn" {
  description = "ARN of the EKS cluster the deploy role may describe and obtain tokens for."
  type        = string
}

variable "tags" {
  description = "Additional tags to apply to all resources."
  type        = map(string)
  default     = {}
}
