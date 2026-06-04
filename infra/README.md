# Infrastructure

This directory contains cloud and cluster provisioning assets.

## Current Contents

- `terraform/`: baseline Terraform configuration documenting the first AWS
  stack decisions.

## Boundary

Infrastructure code should manage AWS and cluster foundations: networking, EKS,
IAM, RDS PostgreSQL, S3, Secrets Manager, node groups, and observability
plumbing. Application code belongs in `app/`; Kubernetes packaging belongs in
`deploy/`.
