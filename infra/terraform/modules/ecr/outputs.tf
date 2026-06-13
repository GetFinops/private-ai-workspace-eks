output "control_plane_repository_url" {
  description = "ECR repository URL for the control-plane image."
  value       = aws_ecr_repository.control_plane.repository_url
}

output "control_plane_repository_arn" {
  description = "ECR repository ARN for the control-plane image."
  value       = aws_ecr_repository.control_plane.arn
}

output "ui_repository_url" {
  description = "ECR repository URL for the UI image."
  value       = aws_ecr_repository.ui.repository_url
}

output "ui_repository_arn" {
  description = "ECR repository ARN for the UI image."
  value       = aws_ecr_repository.ui.arn
}
