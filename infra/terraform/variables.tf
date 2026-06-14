variable "aws_region" {
  description = "AWS region for the EKS deployment."
  type        = string
}

variable "project_name" {
  description = "Project name used for AWS tags and resource prefixes."
  type        = string
  default     = "private-ai-workspace"
}

variable "environment" {
  description = "Deployment environment name (dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "enable_single_nat_gateway" {
  description = "Use a single NAT gateway (lower cost). Set false for multi-AZ NAT in prod."
  type        = bool
  default     = true
}

variable "kubernetes_version" {
  description = "Kubernetes version for the EKS cluster."
  type        = string
  default     = "1.35"
}

variable "control_plane_node_instance_types" {
  description = "CPU instance types for the application control-plane node group."
  type        = list(string)
  default     = ["m7i.large", "m6i.large"]
}

variable "control_plane_desired_size" {
  description = "Desired number of control-plane nodes."
  type        = number
  default     = 2
}

variable "control_plane_min_size" {
  description = "Minimum number of control-plane nodes."
  type        = number
  default     = 1
}

variable "control_plane_max_size" {
  description = "Maximum number of control-plane nodes."
  type        = number
  default     = 4
}

variable "gpu_node_instance_types" {
  description = "GPU instance types for the isolated inference-plane node group."
  type        = list(string)
  default     = ["g5.xlarge", "g5.2xlarge"]
}

variable "gpu_desired_size" {
  description = "Desired number of GPU nodes. 0 = start cold, scale on demand."
  type        = number
  default     = 0
}

variable "gpu_min_size" {
  description = "Minimum number of GPU inference nodes."
  type        = number
  default     = 0
}

variable "gpu_max_size" {
  description = "Maximum number of GPU inference nodes."
  type        = number
  default     = 4
}

variable "gpu_capacity_type" {
  description = "Capacity type for GPU nodes: SPOT or ON_DEMAND."
  type        = string
  default     = "SPOT"
}

variable "postgres_version" {
  description = "PostgreSQL engine version."
  type        = string
  default     = "16"
}

variable "rds_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t3.medium"
}

variable "rds_multi_az" {
  description = "Enable Multi-AZ for RDS. Recommended true in staging/prod."
  type        = bool
  default     = false
}

variable "rds_backup_retention_days" {
  description = "RDS automated backup retention window in days."
  type        = number
  default     = 7
}

variable "app_namespace" {
  description = "Kubernetes namespace where the control-plane chart is deployed."
  type        = string
  default     = "app"
}

variable "app_service_account_name" {
  description = "Kubernetes ServiceAccount name for the control-plane pod."
  type        = string
  default     = "private-ai-workspace-control-plane"
}

variable "github_repository" {
  description = "GitHub repository (org/repo) trusted by the GitHub Actions deploy role, e.g. GetFinops/private-ai-workspace-eks."
  type        = string
  default     = "GetFinops/private-ai-workspace-eks"
}

variable "inference_namespace" {
  description = "Kubernetes namespace where the vLLM inference chart is deployed."
  type        = string
  default     = "inference"
}

variable "vllm_service_account_name" {
  description = "Kubernetes ServiceAccount name for the vLLM pod."
  type        = string
  default     = "vllm-inference"
}

# ── Dev OIDC (Cognito) + UI TLS ──────────────────────────────────────────────

variable "enable_dev_cognito" {
  description = "Create a dev Cognito user pool + app client + hosted-UI domain for OIDC sign-in. Dev-only; production uses an external IdP."
  type        = bool
  default     = false
}

variable "cognito_hosted_ui_domain_prefix" {
  description = "Domain prefix for the Cognito hosted UI (must be globally unique, e.g. private-ai-dev-xxxx). Required when enable_dev_cognito = true."
  type        = string
  default     = ""
}

variable "cognito_test_user_emails" {
  description = "Test user emails to seed in the dev pool (tenant identity = email domain). Passwords are set out-of-band."
  type        = set(string)
  default     = []
}

variable "ui_host" {
  description = "Public hostname for the UI ingress (e.g. ai-dev.getfinops.cloud). Drives Cognito callback/logout URLs and the ACM certificate domain."
  type        = string
  default     = ""
}

variable "acm_route53_zone_id" {
  description = "Route53 hosted-zone id used to DNS-validate the UI ACM certificate. Set together with ui_host to create the certificate."
  type        = string
  default     = ""
}

variable "external_dns_zone_id" {
  description = "Route53 hosted-zone id external-dns may manage (e.g. the UI domain's zone). Empty disables the external-dns IRSA role."
  type        = string
  default     = ""
}
