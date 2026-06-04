variable "aws_region" {
  description = "AWS region for the first EKS deployment baseline."
  type        = string
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name used for AWS tags and resource prefixes."
  type        = string
  default     = "private-ai-workspace"
}

variable "control_plane_node_instance_types" {
  description = "CPU instance types for the application control plane node group."
  type        = list(string)
  default     = ["m7i.large"]
}

variable "gpu_node_instance_types" {
  description = "GPU instance types for the isolated inference plane node group."
  type        = list(string)
  default     = ["g5.xlarge"]
}
