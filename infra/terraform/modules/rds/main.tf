# RDS PostgreSQL module for private-ai-workspace.
#
# New implementation following the architecture direction in docs/09-aws-service-decision-matrix.md:
# RDS PostgreSQL as the managed relational database for M2/M3 milestones.
# Credentials are generated randomly and stored in AWS Secrets Manager.
# The module does not accept a plaintext password input; callers read the
# connection string from the Secrets Manager secret ARN output.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.42"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.4"
    }
  }
}

locals {
  name = "${var.project_name}-${var.environment}"
}

resource "random_password" "db_password" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}|"
}

resource "aws_secretsmanager_secret" "db_credentials" {
  name                    = "${local.name}/rds/master-credentials"
  description             = "RDS master credentials for ${local.name}"
  recovery_window_in_days = var.environment == "prod" ? 30 : 0

  tags = var.tags
}

resource "aws_secretsmanager_secret_version" "db_credentials" {
  secret_id = aws_secretsmanager_secret.db_credentials.id

  secret_string = jsonencode({
    username            = "aiworkspace"
    password            = random_password.db_password.result
    dbname              = var.database_name
    engine              = "postgres"
    host                = aws_db_instance.this.address
    port                = aws_db_instance.this.port
    dbInstanceIdentifier = aws_db_instance.this.identifier
  })
}

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = var.private_subnet_ids
  tags       = var.tags
}

resource "aws_security_group" "rds" {
  name        = "${local.name}-rds"
  description = "Security group for ${local.name} RDS instance"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${local.name}-rds" })
}

resource "aws_vpc_security_group_ingress_rule" "rds_postgres" {
  for_each = toset(var.allowed_security_group_ids)

  security_group_id            = aws_security_group.rds.id
  description                  = "PostgreSQL from allowed security groups"
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  referenced_security_group_id = each.value
}

resource "aws_vpc_security_group_egress_rule" "rds_all_outbound" {
  security_group_id = aws_security_group.rds.id
  description       = "Allow all outbound"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_db_instance" "this" {
  identifier = local.name

  engine         = "postgres"
  engine_version = var.postgres_version
  instance_class = var.instance_class

  allocated_storage     = var.allocated_storage_gb
  max_allocated_storage = var.max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.database_name
  username = "aiworkspace"
  password = random_password.db_password.result

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false

  multi_az                = var.multi_az
  deletion_protection     = var.deletion_protection
  skip_final_snapshot     = var.skip_final_snapshot
  backup_retention_period = var.backup_retention_days
  apply_immediately       = var.environment != "prod"

  performance_insights_enabled = true

  tags = merge(var.tags, { Name = local.name })

  lifecycle {
    ignore_changes = [password]
  }
}
