# Terraform Baseline

This directory records the first AWS stack decisions from the planning roadmap
and provides a valid starting point for future EKS resources. It intentionally
does not create cloud resources yet.

## Starting Stack

- EKS for Kubernetes
- AWS Load Balancer Controller for ingress
- RDS PostgreSQL for relational state
- S3 for uploads and artifacts
- AWS Secrets Manager for managed secrets
- Helm for application packaging
- CPU managed node group for the control plane
- Isolated GPU managed node group for vLLM inference

Add resources in focused follow-up changes so networking, IAM, secrets, and
state boundaries remain reviewable.
