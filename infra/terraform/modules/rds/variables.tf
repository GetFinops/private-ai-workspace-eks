variable "project_name" {
  description = "Project name used for resource naming and tags."
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where the RDS instance will be created."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for the RDS subnet group."
  type        = list(string)
}

variable "allowed_security_group_ids" {
  description = "Security group IDs that are allowed to access the database (typically the EKS node groups)."
  type        = list(string)
  default     = []
}

variable "postgres_version" {
  description = "PostgreSQL engine version."
  type        = string
  default     = "16"
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t3.medium"
}

variable "allocated_storage_gb" {
  description = "Allocated storage in GiB."
  type        = number
  default     = 20
}

variable "max_allocated_storage_gb" {
  description = "Maximum storage auto-scaling ceiling in GiB."
  type        = number
  default     = 100
}

variable "database_name" {
  description = "Name of the initial database to create."
  type        = string
  default     = "aiworkspace"
}

variable "multi_az" {
  description = "Enable Multi-AZ for HA. Recommended true in staging/prod."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Enable deletion protection. Recommended true in prod."
  type        = bool
  default     = false
}

variable "skip_final_snapshot" {
  description = "Skip final snapshot on deletion (set false in prod)."
  type        = bool
  default     = true
}

variable "backup_retention_days" {
  description = "Number of days to retain automated backups."
  type        = number
  default     = 7
}

variable "tags" {
  description = "Additional tags to apply to all resources."
  type        = map(string)
  default     = {}
}
