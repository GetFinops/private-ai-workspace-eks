# Installation guide

End-to-end install of `private-ai-workspace-eks` on AWS — from `terraform apply`
to a running workspace, including monitoring and every optional component. For
what each piece is, see [`components.md`](components.md).

> **Scope.** This is an infrastructure deployment (VPC, EKS, RDS, GPU nodes) that
> incurs AWS cost. Everything beyond the core control plane is **opt-in and
> off by default**. To just run and test the control-plane logic with no cloud,
> jump to [Local development](#local-development-no-cloud).

## Prerequisites

- An AWS account with admin-level Terraform permissions. Dev reference:
  `export AWS_PROFILE=personal AWS_REGION=us-west-2` (account `069133419519`).
- `terraform` ≥ 1.6, `aws` CLI, `kubectl`, `helm` ≥ 3.17, `gh` CLI, `docker`.
- A Route53 hosted zone (for the UI hostname + ACM cert), if deploying the UI.

## Namespaces

| Namespace | Contents |
| --- | --- |
| `app` | control plane (core), UI |
| `inference` | vLLM, embeddings, media, model-installer |
| `agent-jobs` | tool-runner dispatcher + sandboxed runner Jobs |
| `monitoring` | observability stack |
| `kube-system` | cluster-addons (ESO, device plugin, DCGM), external-dns, scaling |

## The path in brief

```
1. terraform apply         → VPC, EKS, RDS, S3, ECR, IRSA, Cognito, ACM, Secret placeholders
2. outputs → GitHub vars   → the Deploy workflow reads them
3. Secrets Manager + K8s   → app config, HF token (if gated), reconciler/dispatcher tokens
4. cluster-addons          → ESO, NVIDIA device plugin, DCGM
5. deploy core             → control-plane chart (always)
6. opt-in add-ons          → UI, embedding, inference, model-installer, observability, ...
7. verify                  → /healthz, /readyz, smoke test
```

---

## Step 1 — Provision AWS (Terraform)

```bash
cd infra/terraform
export AWS_PROFILE=personal AWS_REGION=us-west-2

cp tfvars/dev.tfvars.example tfvars/dev.tfvars   # then edit — real tfvars are gitignored
terraform init
```

Fill in `tfvars/dev.tfvars`: `aws_region`, node sizing, `rds_*`, and (for the UI
sign-in) `enable_dev_cognito`, a **globally-unique** `cognito_hosted_ui_domain_prefix`,
`ui_host`, `acm_route53_zone_id`, `external_dns_zone_id`.

> **Gotcha:** pre-import the GitHub Actions OIDC provider before the first apply,
> or it fails `EntityAlreadyExists` if the account already has one:
> ```bash
> terraform import module.github_actions_role.aws_iam_openid_connect_provider.github_actions \
>   arn:aws:iam::<acct>:oidc-provider/token.actions.githubusercontent.com
> ```

```bash
terraform plan  -var-file=tfvars/dev.tfvars
terraform apply -var-file=tfvars/dev.tfvars
```

This creates: VPC, **EKS 1.35** (CPU control-plane NG + GPU inference NG at
`desired=0`), **RDS PostgreSQL 16**, **S3** artifacts bucket, **ECR** repos, all
**IRSA** roles, the GitHub Actions deploy role (+ EKS cluster-admin access
entry), dev **Cognito**, the **ACM** cert, and two **empty** Secrets Manager
placeholders (`<project>/<env>/app`, `<project>/<env>/hf-token`).

> State is **local** (`terraform.tfstate` in-tree). Wire an S3/DynamoDB backend
> before any shared/prod use.

## Step 2 — Wire the Deploy workflow (GitHub vars/secrets)

Capture outputs and set them so `deploy.yml` can read them. There is exactly one
**secret**; everything else is a repo **variable**.

```bash
# The one secret:
gh secret set AWS_DEPLOY_ROLE_ARN --body "$(terraform output -raw github_actions_deploy_role_arn)"

# Always-needed variables:
gh variable set AWS_REGION             --body us-west-2
gh variable set ECR_CONTROL_PLANE_URL  --body "$(terraform output -raw ecr_control_plane_url)"
gh variable set EKS_CLUSTER_NAME       --body "$(terraform output -raw eks_cluster_name)"
gh variable set IRSA_APP_ROLE_ARN      --body "$(terraform output -raw irsa_app_role_arn)"
gh variable set APP_CONFIG_SECRET_NAME --body "$(terraform output -raw app_config_secret_name)"
gh variable set EMBEDDING_BASE_URL     --body ""    # empty = deterministic dev embedding
gh variable set EMBEDDING_MODEL        --body ""
```

Set the extra variables only for the toggles you plan to enable — see the
[Reference: repo variables](#reference-repo-variables--secrets) table.

## Step 3 — Populate secrets (out-of-band, never committed)

**App config** (JSON synced into the control-plane pod by an ExternalSecret):

```bash
aws secretsmanager put-secret-value \
  --secret-id "$(terraform output -raw app_config_secret_name)" \
  --secret-string '{
    "DATABASE_URL": "postgresql://<user>:<pw>@<rds-endpoint>:5432/<db>",
    "AUTH_ISSUER_URL": "<cognito_issuer_url>",
    "AUTH_AUDIENCE": "<cognito_client_id>",
    "AUTH_ADMIN_GROUP": "admin"
  }'
```

Add `"MODEL_INSTALLER_TOKEN": "<random>"` to that JSON if you will run the
model-installer (Step 6). The **HF token** secret is only needed for a *gated*
model (dev uses ungated Qwen, so skip it):

```bash
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw hf_token_secret_name)" --secret-string '<hf-hub-token>'
```

Update kubeconfig, then create the **K8s-only** token Secrets that the opt-in
charts reference:

```bash
aws eks update-kubeconfig --region us-west-2 --name "$(terraform output -raw eks_cluster_name)"

# model-installer (value MUST equal MODEL_INSTALLER_TOKEN in the app config JSON):
kubectl -n inference create secret generic model-installer-token --from-literal=MODEL_INSTALLER_TOKEN=<random>
# tool-runner (value MUST equal the control plane's AGENT_TOOLS_DISPATCHER_TOKEN):
kubectl -n app create secret generic tool-runner-dispatcher-token --from-literal=token=<random>
```

## Step 4 — Cluster add-ons (bootstrap)

Install External Secrets Operator, the NVIDIA device plugin, and DCGM exporter
(all enabled by default). The Deploy workflow does this automatically, or run:

```bash
helm dependency update deploy/helm/cluster-addons
helm upgrade --install cluster-addons deploy/helm/cluster-addons \
  --namespace kube-system --create-namespace --wait
```

Leave `clusterAutoscaler`/`karpenter`/`prometheusAdapter` off until their
Terraform IRSA outputs are wired (Step 6 `deploy_scaling`).

## Step 5 — Deploy the core control plane

The Deploy workflow installs `cluster-addons` + the control-plane chart
**unconditionally**:

```bash
gh workflow run deploy.yml --ref main -f environment=dev
```

This builds the control-plane image (SHA-tagged) and `helm upgrade`s
`private-ai-workspace` into `app` with `deploy/values/dev/app.yaml`. In dev that
turns on agent tools / MCP / integrations / media / model-install-requests — all
**deny-by-default** and allow-listed to `tenant-a.test` only (with
`tenant-b.test` intentionally excluded to prove cross-tenant denial).

## Step 6 — Opt-in add-ons (Deploy workflow toggles)

All default `false`. Enable what you need:

```bash
gh workflow run deploy.yml --ref main -f environment=dev \
  -f deploy_ui=true \
  -f deploy_embedding=true \
  -f deploy_inference=true \
  -f deploy_installer=true \
  -f deploy_observability=true \
  -f deploy_external_dns=true \
  -f deploy_scaling=true
```

| Toggle | Deploys | Extra repo vars needed |
| --- | --- | --- |
| `deploy_ui` | Web UI (`app`) | `ECR_UI_URL`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `UI_REDIRECT_URI`, `UI_INGRESS_HOST`, `ACM_CERT_ARN` |
| `deploy_embedding` | TEI embeddings (`inference`) | set `EMBEDDING_BASE_URL`/`EMBEDDING_MODEL` on the control plane to use it |
| `deploy_inference` | vLLM (`inference`) | `IRSA_VLLM_ROLE_ARN`, `HF_TOKEN_SECRET_NAME` (gated models only) |
| `deploy_installer` | model-installer reconciler (`inference`) | `ECR_MODEL_INSTALLER_URL`, `MODEL_INSTALLER_ROLE_ARN`, `GPU_NODEGROUP_NAME` |
| `deploy_observability` | Prometheus/Grafana/Alertmanager (`monitoring`) | — |
| `deploy_external_dns` | external-dns (`kube-system`) | `EXTERNAL_DNS_ROLE_ARN` |
| `deploy_scaling` | cluster-autoscaler + Karpenter + prometheus-adapter | `IRSA_CLUSTER_AUTOSCALER_ROLE_ARN`, `KARPENTER_CONTROLLER_ROLE_ARN`, `KARPENTER_NODE_ROLE_NAME` |

For the UI, register the app as a **public OIDC client with PKCE (S256)** at the
IdP and add the redirect URI (`<origin>/callback`) and post-logout URI
(`<origin>/login.html`); the browser performs the PKCE token exchange directly
against the token endpoint, so **CORS from the UI origin must be allowed**.

vLLM dev serves ungated `Qwen/Qwen2.5-1.5B-Instruct` under the alias `default`;
`modelInstaller.enabled=true` sources the model id from the
`vllm-inference-model` ConfigMap so the reconciler can swap it. The
model-installer needs `INSTALLER_ENABLED=true` **and** a non-empty allow-list to
install anything (see the model-management design + follow-up issue for the
supply-chain caveats before enabling it beyond dev).

## Step 7 — Manual charts (not wired into the workflow)

**Media services** — one release per family (SDXL needs a license-reviewed,
digest-pinned image supplied first):

```bash
helm upgrade --install whisper-stt deploy/helm/media-service \
  --namespace inference -f deploy/values/dev/media-whisper.yaml
helm upgrade --install sdxl-image  deploy/helm/media-service \
  --namespace inference -f deploy/values/dev/media-sdxl.yaml
```

**Tool-runner** (M11 Job sandbox; `sharedToken` must equal the control plane's
`AGENT_TOOLS_DISPATCHER_TOKEN`):

```bash
helm upgrade --install tool-runner deploy/helm/tool-runner \
  --set image.repository=<CONTROL_PLANE_ECR> --set image.tag=<GIT_SHA> \
  --set sharedToken=<TOKEN> -f deploy/values/dev/tool-runner.yaml
```

---

## Monitoring (observability stack)

Prometheus + Grafana + Alertmanager, plus GPU (DCGM) and vLLM dashboards.

**Enable it** (opt-in, defaults off — installs into `monitoring`):

```bash
gh workflow run deploy.yml --ref main -f environment=dev -f deploy_observability=true
# or manually:
helm dependency update deploy/helm/observability
helm upgrade --install observability deploy/helm/observability \
  --namespace monitoring --create-namespace \
  -f deploy/values/dev/observability.yaml --wait --timeout 10m
```

> The release name **must** be `observability` — the control-plane and vLLM
> ServiceMonitors carry `additionalLabels: release=observability` so the
> Prometheus Operator selects them.

**Enable control-plane scraping** (already set in `deploy/values/dev/app.yaml`):
`metrics.serviceMonitor.enabled=true` with `additionalLabels.release=observability`.

**Enable tracing** (optional): set `config.otelEndpoint` (→
`OTEL_EXPORTER_OTLP_ENDPOINT`) to an OTel Collector ClusterIP; empty = no-op.

**Reach the dashboards** (no Ingress by default — use port-forward):

```bash
# Grafana (login: admin / <adminPassword>; 'admin' in dev only)
kubectl port-forward svc/observability-grafana 3000:80 -n monitoring
# → http://localhost:3000  (dashboards auto-load under GPU / Inference / Control Plane)

# Prometheus
kubectl port-forward svc/observability-kube-prometheus-stack-prometheus 9090:9090 -n monitoring
```

Dev disables Alertmanager and trims retention (3d/2GB) to save resources. GPU
metrics come from the DCGM exporter installed by `cluster-addons`. In prod, set
`kube-prometheus-stack.grafana.adminPassword` via ExternalSecrets/Secrets Manager
and bump retention.

Shipped alert rules: `InferenceQueueDepthHigh`, `InferenceReplicaUnavailable`,
`ControlPlaneNotReady`. Dashboards: DCGM GPU, vLLM Performance, vLLM Query,
Control Plane Retrieval.

---

## Verify

The deploy job self-verifies (`kubectl wait` Available + a port-forwarded
`scripts/smoke-test.sh --public-only`). Operator checks:

```bash
kubectl -n app port-forward deployment/private-ai-workspace-private-ai-workspace 18080:8080 &

curl -s localhost:18080/healthz          # {"status":"ok"}
curl -s localhost:18080/readyz           # {"status":"ready", checks all true} once configured
curl -s "localhost:18080/v1/inference/status?probe=1"

# Authenticated round trip + cross-tenant isolation (mint a Cognito ID token):
TOK=$(aws cognito-idp admin-initiate-auth --region us-west-2 \
  --user-pool-id <pool> --client-id <client> --auth-flow ADMIN_USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=alice@tenant-a.test,PASSWORD=<pw> \
  --query AuthenticationResult.IdToken --output text)
./scripts/smoke-test.sh --base http://localhost:18080 --token "$TOK" \
  --token-b "<other-tenant-token>" --token-c "<same-tenant-user2-token>"
```

The control plane verifies the **ID token** (the access token lacks `aud`+`email`);
tenant = email domain. Reset seeded test-user passwords out-of-band with
`aws cognito-idp admin-set-user-password --permanent` — **never commit credentials**.

---

## Local development (no cloud)

The control-plane app logic is stdlib-only — no `pip install`, no AWS.

```bash
# Same checks as CI:
python3 -m compileall app tests
python3 -m unittest discover -s tests

# Dev server (binds 0.0.0.0:8080):
python3 -m app.control_plane

# Self-contained smoke (spins up a dev-token control plane, exercises the
# authenticated surface incl. success + denial paths):
./scripts/smoke-test.sh
```

Local mode sets `ENVIRONMENT=development` + `DEV_AUTH_TOKEN` and toggles
`AGENT_TOOLS_ENABLED`/`MCP_ENABLED` with a localhost allow-list. Cross-tenant
probes are skipped locally (the dev verifier maps every token to one principal).

---

## Reference: deploy toggles

`gh workflow run deploy.yml --ref <branch> -f environment=dev|staging [-f <toggle>=true]`.
Core control plane + `cluster-addons` (ESO) deploy **unconditionally**; all
toggles below default `false`:

`deploy_ui`, `deploy_inference`, `deploy_embedding`, `deploy_installer`,
`deploy_observability`, `deploy_external_dns`, `deploy_scaling`.

## Reference: repo variables & secrets

| Kind | Name | Source (Terraform output) |
| --- | --- | --- |
| secret | `AWS_DEPLOY_ROLE_ARN` | `github_actions_deploy_role_arn` |
| var (always) | `AWS_REGION`, `ECR_CONTROL_PLANE_URL`, `EKS_CLUSTER_NAME`, `IRSA_APP_ROLE_ARN`, `APP_CONFIG_SECRET_NAME`, `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL` | `ecr_control_plane_url`, `eks_cluster_name`, `irsa_app_role_arn`, `app_config_secret_name` |
| var (`deploy_ui`) | `ECR_UI_URL`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `UI_REDIRECT_URI`, `UI_INGRESS_HOST`, `ACM_CERT_ARN` | `ecr_ui_url`, `cognito_issuer_url`, `cognito_client_id`, `ui_acm_certificate_arn` |
| var (`deploy_inference`) | `IRSA_VLLM_ROLE_ARN`, `HF_TOKEN_SECRET_NAME` | `irsa_vllm_role_arn`, `hf_token_secret_name` |
| var (`deploy_installer`) | `ECR_MODEL_INSTALLER_URL`, `MODEL_INSTALLER_ROLE_ARN`, `GPU_NODEGROUP_NAME` | `model_installer_role_arn` |
| var (`deploy_external_dns`) | `EXTERNAL_DNS_ROLE_ARN` | `external_dns_role_arn` |
| var (`deploy_scaling`) | `IRSA_CLUSTER_AUTOSCALER_ROLE_ARN`, `KARPENTER_CONTROLLER_ROLE_ARN`, `KARPENTER_NODE_ROLE_NAME` | `irsa_cluster_autoscaler_role_arn`, `karpenter_controller_role_arn`, `karpenter_node_role_name` |

## Reference: key control-plane env vars

Supplied by the Helm chart / Secrets Manager. All capabilities are
**deny-by-default**.

| Purpose | Vars |
| --- | --- |
| Auth | `AUTH_ISSUER_URL`, `AUTH_AUDIENCE`, `AUTH_ADMIN_GROUP` (or `ENVIRONMENT=development` + `DEV_AUTH_TOKEN`) |
| Persistence | `DATABASE_URL` (switches every store to Postgres), `OBJECT_STORAGE_BUCKET` |
| Inference | `INFERENCE_BASE_URL`, `EMBEDDING_BASE_URL`, `MODELS` |
| Agent tools | `AGENT_TOOLS_ENABLED`, `AGENT_TOOLS_ALLOWLIST`, `AGENT_TOOLS_DISPATCHER_URL`/`_TOKEN` |
| MCP / Integrations / Media | `MCP_ENABLED`+`MCP_ALLOWLIST`; `INTEGRATIONS_ENABLED`+`INTEGRATIONS_ALLOWLIST`; `MEDIA_ENABLED`+`MEDIA_ALLOWLIST`+`MEDIA_SERVICES` |
| Model install | `MODEL_INSTALL_ENABLED`, `MODEL_INSTALL_ALLOWLIST`, `MODEL_INSTALL_ALLOW_ALL_USERS`/`MODEL_INSTALL_GROUP`, `MODEL_INSTALLER_TOKEN` |
| Observability | `LOG_FORMAT`, `LOG_LEVEL`, `OTEL_EXPORTER_OTLP_ENDPOINT` |

See [`14-user-permissions.md`](14-user-permissions.md) for the permission model
and [`m11-followups/04-model-management.md`](m11-followups/04-model-management.md)
for the model-install pipeline.
