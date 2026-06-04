output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS cluster API endpoint."
  value       = module.eks.cluster_endpoint
}

output "cluster_ca_data" {
  description = "Base64-encoded cluster certificate authority data."
  value       = module.eks.cluster_certificate_authority_data
  sensitive   = true
}

output "cluster_oidc_provider_arn" {
  description = "OIDC provider ARN for IRSA."
  value       = module.eks.oidc_provider_arn
}

output "cluster_oidc_provider_url" {
  description = "OIDC provider URL (without https://) for IRSA role conditions."
  value       = replace(module.eks.oidc_provider_arn, "/^(.*provider/)/", "")
}

output "update_kubeconfig_command" {
  description = "AWS CLI command to update kubeconfig for this cluster."
  value       = "aws eks update-kubeconfig --region ${data.aws_caller_identity.current.id} --name ${module.eks.cluster_name}"
}
