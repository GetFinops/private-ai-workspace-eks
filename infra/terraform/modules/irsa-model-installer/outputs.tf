output "role_arn" {
  description = "IRSA role ARN for the model-installer reconciler (scoped: scale GPU nodegroups only)."
  value       = aws_iam_role.installer.arn
}
