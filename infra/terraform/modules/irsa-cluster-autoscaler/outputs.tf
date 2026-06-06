output "role_arn" {
  description = "ARN of the IAM role to annotate on the cluster-autoscaler ServiceAccount."
  value       = aws_iam_role.cluster_autoscaler.arn
}

output "role_name" {
  description = "Name of the IAM role."
  value       = aws_iam_role.cluster_autoscaler.name
}
