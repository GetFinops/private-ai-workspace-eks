output "role_arn" {
  description = "IAM role ARN for the GitHub Actions deploy role. Set as AWS_DEPLOY_ROLE_ARN in GitHub Actions secrets."
  value       = aws_iam_role.deploy.arn
}

output "role_name" {
  description = "IAM role name."
  value       = aws_iam_role.deploy.name
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub Actions OIDC provider."
  value       = aws_iam_openid_connect_provider.github_actions.arn
}
