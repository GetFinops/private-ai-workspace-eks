variable "project_name" {
  description = "Project name used for resource naming and tags."
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
}

variable "enable_versioning" {
  description = "Enable S3 object versioning."
  type        = bool
  default     = true
}

variable "force_destroy" {
  description = "Allow bucket destruction even when non-empty (set false in prod)."
  type        = bool
  default     = false
}

variable "lifecycle_noncurrent_days" {
  description = "Days after which non-current object versions are expired."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Additional tags to apply to all resources."
  type        = map(string)
  default     = {}
}
