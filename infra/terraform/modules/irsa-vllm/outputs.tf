output "role_arn" {
  description = "IAM role ARN to annotate on the vLLM ServiceAccount (IRSA). Set as serviceAccount.irsaRoleArn in vllm chart values."
  value       = aws_iam_role.vllm.arn
}

output "role_name" {
  description = "IAM role name."
  value       = aws_iam_role.vllm.name
}
