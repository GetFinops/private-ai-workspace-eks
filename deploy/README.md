# Deployment Packaging

This directory contains Kubernetes-facing deployment artifacts.

## Current Contents

- `helm/private-ai-workspace/`: initial Helm chart for the CPU control-plane
  service.

## Boundary

Deployment packaging should define Kubernetes objects and runtime wiring. Cloud
resources such as VPCs, EKS clusters, RDS, S3, and IAM belong in `infra/`.
