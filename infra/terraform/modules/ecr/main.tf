# ECR repositories for private-ai-workspace container images.
#
# New implementation. All first-party images are stored in ECR, image
# scanning is enabled on push, and tag mutability defaults to IMMUTABLE
# to prevent overwriting deployed image tags.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.42"
    }
  }
}

locals {
  name = "${var.project_name}-${var.environment}"
}

resource "aws_ecr_repository" "control_plane" {
  name                 = "${local.name}/control-plane"
  image_tag_mutability = var.image_tag_mutability

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
  }

  tags = merge(var.tags, { Name = "${local.name}/control-plane" })
}

resource "aws_ecr_lifecycle_policy" "control_plane" {
  repository = aws_ecr_repository.control_plane.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after 14 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }
      action = { type = "expire" }
    }]
  })
}
