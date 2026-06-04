# Deployment Packaging

This directory contains Kubernetes-facing deployment artifacts.

## Contents

| Path | Purpose | Milestone |
|------|---------|-----------|
| `helm/private-ai-workspace/` | CPU control-plane Helm chart | M1 |
| `helm/vllm/` | vLLM inference-plane Helm chart | M4 |
| `helm/observability/` | Prometheus + Grafana umbrella chart | M5 |
| `values/dev/` | Per-chart value overrides for the dev environment | M2+ |
| `values/staging/` | Per-chart value overrides for staging | M7 |
| `values/prod/` | Per-chart value overrides for production | M8 |

## Deploy Pattern

```bash
# Control plane
helm upgrade --install private-ai-workspace deploy/helm/private-ai-workspace \
  --namespace app --create-namespace \
  -f deploy/values/<env>/app.yaml

# vLLM inference (M4 — requires GPU nodes)
helm upgrade --install vllm deploy/helm/vllm \
  --namespace inference --create-namespace \
  -f deploy/values/<env>/vllm.yaml

# Observability (M5)
helm dependency update deploy/helm/observability
helm upgrade --install observability deploy/helm/observability \
  --namespace monitoring --create-namespace \
  -f deploy/values/<env>/observability.yaml
```

## Boundary

Deployment packaging defines Kubernetes objects and runtime wiring. Cloud
resources (VPCs, EKS clusters, RDS, S3, IAM) belong in `infra/`.
