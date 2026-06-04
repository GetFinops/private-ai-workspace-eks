output "db_instance_id" {
  description = "RDS instance identifier."
  value       = aws_db_instance.this.identifier
}

output "db_instance_address" {
  description = "RDS instance hostname."
  value       = aws_db_instance.this.address
}

output "db_instance_port" {
  description = "RDS instance port."
  value       = aws_db_instance.this.port
}

output "db_security_group_id" {
  description = "Security group ID of the RDS instance (allow in EKS node SG rules)."
  value       = aws_security_group.rds.id
}

output "credentials_secret_arn" {
  description = "Secrets Manager ARN for the RDS master credentials JSON."
  value       = aws_secretsmanager_secret.db_credentials.arn
}

output "credentials_secret_name" {
  description = "Secrets Manager secret name for the RDS master credentials."
  value       = aws_secretsmanager_secret.db_credentials.name
}
