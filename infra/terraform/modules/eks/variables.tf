variable "project_name" {
  description = "Project name used for resource naming and tags."
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes version for the EKS cluster."
  type        = string
  default     = "1.32"
}

variable "vpc_id" {
  description = "VPC ID where the EKS cluster will be created."
  type        = string
}

variable "vpc_cidr_block" {
  description = "VPC CIDR block, used for node security group ingress."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for EKS node groups."
  type        = list(string)
}

variable "control_plane_instance_types" {
  description = "Instance types for the CPU control-plane node group."
  type        = list(string)
  default     = ["m7i.large", "m6i.large"]
}

variable "control_plane_desired_size" {
  description = "Desired number of nodes in the control-plane node group."
  type        = number
  default     = 2
}

variable "control_plane_min_size" {
  description = "Minimum number of nodes in the control-plane node group."
  type        = number
  default     = 1
}

variable "control_plane_max_size" {
  description = "Maximum number of nodes in the control-plane node group."
  type        = number
  default     = 4
}

variable "gpu_instance_types" {
  description = "Instance types for the GPU inference-plane node group."
  type        = list(string)
  default     = ["g5.xlarge", "g5.2xlarge"]
}

variable "gpu_desired_size" {
  description = "Desired number of GPU nodes. Set 0 to start cold."
  type        = number
  default     = 0
}

variable "gpu_min_size" {
  description = "Minimum number of GPU nodes."
  type        = number
  default     = 0
}

variable "gpu_max_size" {
  description = "Maximum number of GPU nodes."
  type        = number
  default     = 4
}

variable "gpu_capacity_type" {
  description = "Capacity type for GPU nodes: ON_DEMAND or SPOT."
  type        = string
  default     = "SPOT"

  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.gpu_capacity_type)
    error_message = "gpu_capacity_type must be ON_DEMAND or SPOT."
  }
}

variable "tags" {
  description = "Additional tags to apply to all resources."
  type        = map(string)
  default     = {}
}
