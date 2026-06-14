output "role_arn" {
  description = "IAM role ARN for the external-dns IRSA service account."
  value       = aws_iam_role.this.arn
}
