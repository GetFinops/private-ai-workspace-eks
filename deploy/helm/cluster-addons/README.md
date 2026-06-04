# Cluster Add-ons Chart

Installs cluster-level prerequisites before application charts are deployed.

## Components

- **External Secrets Operator** — syncs secrets from AWS Secrets Manager (and other stores) into Kubernetes Secrets via `ExternalSecret` / `SecretStore` CRDs.

## Install order

```bash
# 1. Install cluster add-ons first (creates ESO CRDs)
helm dependency update deploy/helm/cluster-addons
helm upgrade --install cluster-addons deploy/helm/cluster-addons \
  --namespace kube-system --create-namespace

# 2. Then install the control-plane chart (uses ExternalSecret CRDs)
helm upgrade --install private-ai-workspace deploy/helm/private-ai-workspace \
  --namespace app --create-namespace \
  -f deploy/values/<env>/app.yaml
```

## How secrets flow

```
AWS Secrets Manager
  └── <project>/<env>/app  (JSON: DATABASE_URL, AUTH_ISSUER_URL, AUTH_AUDIENCE, AUTH_ADMIN_GROUP)
        │
        │  ExternalSecret (in deploy/helm/private-ai-workspace)
        │  authenticates via pod's IRSA service account (JWT auth)
        ▼
  Kubernetes Secret  (<release>-app-secrets)
        │
        │  envFrom.secretRef in Deployment
        ▼
  Control-plane pod environment variables
```

The Secrets Manager secret name is set from Terraform output:
```bash
terraform -chdir=infra/terraform output -raw app_config_secret_name
```
