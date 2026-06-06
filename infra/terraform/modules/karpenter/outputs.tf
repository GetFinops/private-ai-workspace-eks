output "controller_role_arn" {
  description = "ARN of the IAM role to annotate on the Karpenter controller ServiceAccount."
  value       = aws_iam_role.controller.arn
}

output "node_role_arn" {
  description = "ARN of the IAM role assumed by EC2 instances Karpenter provisions. Reference this in EC2NodeClass.spec.role."
  value       = aws_iam_role.node.arn
}

output "node_role_name" {
  description = "Name of the node IAM role (some Karpenter resources need the name, not the ARN)."
  value       = aws_iam_role.node.name
}
