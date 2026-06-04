locals {
  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  starting_stack = {
    kubernetes_platform    = "EKS"
    ingress                = "AWS Load Balancer Controller"
    relational_database    = "RDS PostgreSQL"
    object_storage         = "S3"
    secrets                = "AWS Secrets Manager"
    packaging              = "Helm"
    control_plane_capacity = "CPU managed node group"
    inference_capacity     = "isolated GPU managed node group"
    inference_runtime      = "vLLM OpenAI-compatible API"
  }
}
