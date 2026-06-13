# Deployment Packaging

This directory contains Kubernetes-facing deployment artifacts.

## Contents

| Path | Purpose | Milestone |
|------|---------|-----------|
| `helm/private-ai-workspace/` | CPU control-plane Helm chart | M1 |
| `helm/vllm/` | vLLM inference-plane Helm chart | M4 |
| `helm/observability/` | Prometheus + Grafana umbrella chart | M5 |
| `helm/private-ai-ui/` | Product-surface web UI Helm chart | M9 |
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

# Product-surface UI (M9 — set image, OIDC, and ingress host)
helm upgrade --install private-ai-ui deploy/helm/private-ai-ui \
  --namespace app \
  --set image.repository=<ecr-ui-uri> --set image.tag=<tag> \
  --set oidc.issuer=<issuer> --set oidc.clientId=<client-id> \
  --set oidc.redirectUri=<https://.../callback> \
  --set ingress.host=<ai-dev.example.com> \
  -f deploy/values/<env>/ui.yaml
```

The `Deploy` GitHub Actions workflow automates all of the above; set its
`deploy_ui` input to `true` to build + deploy the UI image (requires the
`ECR_UI_URL` and `OIDC_*` repo vars).

## Validation

After a deploy, exercise the control-plane API the UI consumes:

```bash
# Token-free public-surface probe (what the deploy pipeline runs):
scripts/smoke-test.sh --base http://localhost:8080 --public-only

# Full authenticated round trip + cross-tenant isolation probe:
scripts/smoke-test.sh --base https://<control-plane> \
  --token "$TOKEN_A" --token-b "$TOKEN_B"
```

The end-to-end M9 dev-deployment validation flow is documented in
[`../docs/runbooks/m9-dev-deployment-validation.md`](../docs/runbooks/m9-dev-deployment-validation.md).

## Boundary

Deployment packaging defines Kubernetes objects and runtime wiring. Cloud
resources (VPCs, EKS clusters, RDS, S3, IAM) belong in `infra/`.
