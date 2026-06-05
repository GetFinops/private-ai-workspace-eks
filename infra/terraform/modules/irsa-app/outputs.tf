output "role_arn" {
  description = "IAM role ARN to annotate on the control-plane ServiceAccount (IRSA)."
  value       = aws_iam_role.app.arn
}

output "role_name" {
  description = "IAM role name."
  value       = aws_iam_role.app.name
}
