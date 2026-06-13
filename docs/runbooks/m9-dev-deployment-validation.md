# Runbook — M9 dev-deployment validation

Operator steps to satisfy the M9 exit criterion: *"The dev-deployment smoke
test passes against a freshly-deployed dev cluster"*
([`../milestones/m9-product-surface.md`](../milestones/m9-product-surface.md)).
The output of step 5 is what gets pasted into the M9 PR before it is marked
ready for review.

This is operator-run because it needs cluster + identity-provider access that
CI does not have. Everything in-repo (the charts, the pipeline, the smoke
test) is already wired; the steps below supply the cloud-side inputs.

## Prerequisites

- AWS CLI configured for the dev account; Terraform ≥ 1.6; `kubectl`; `helm`
  ≥ 3.16; `python3`; `curl`. (The dev container in `.devcontainer/` bundles
  these.)
- A dev OIDC user pool (Cognito or compatible) with **two test users whose
  emails are in different domains** — tenant identity is derived from the
  email domain, so `alice@tenant-a.test` and `bob@tenant-b.test` are two
  tenants.

## 1. Provision the cluster

```bash
./scripts/bootstrap-infra.sh --apply --env dev
```

Two-phase apply (VPC + EKS first, then RDS/S3/ECR/IRSA/Helm). On success it
prints the Terraform outputs — note the ECR repo URIs, the EKS cluster name,
and the IRSA role ARNs for the next step.

## 2. Configure the Deploy workflow's repo vars/secrets

The `Deploy` workflow (`.github/workflows/deploy.yml`) authenticates to AWS via
OIDC and reads its targets from repo-level **Actions variables** (and one
secret). Set these under *Settings → Secrets and variables → Actions*:

| Name | Kind | Used for |
|------|------|----------|
| `AWS_DEPLOY_ROLE_ARN` | secret | OIDC role the workflow assumes |
| `AWS_REGION` | var | region for all AWS calls |
| `ECR_CONTROL_PLANE_URL` | var | control-plane image repo |
| `EKS_CLUSTER_NAME` | var | `kubectl` context |
| `IRSA_APP_ROLE_ARN` | var | control-plane IRSA |
| `APP_CONFIG_SECRET_NAME` | var | ExternalSecrets source for app config |
| `ECR_UI_URL` | var | **M9** — UI image repo |
| `OIDC_ISSUER` | var | **M9** — UI sign-in issuer URL |
| `OIDC_CLIENT_ID` | var | **M9** — UI public client id |
| `UI_REDIRECT_URI` | var | **M9** — registered PKCE callback URL |
| `UI_INGRESS_HOST` | var | **M9** — UI hostname on the ALB |
| `ACM_CERT_ARN` | var | **M9** — TLS cert for the UI ingress |

The `OIDC_ISSUER` / `OIDC_CLIENT_ID` here must match the user pool + app
client backing `APP_CONFIG_SECRET_NAME`'s `AUTH_ISSUER_URL` / `AUTH_AUDIENCE`,
so the token the UI obtains verifies against the control plane.

## 3. Deploy the control plane + UI

Run the **Deploy** workflow (*Actions → Deploy → Run workflow*) with
`environment=dev` and `deploy_ui=true`. It builds + pushes both images, helm-
installs the control plane and the `private-ai-ui` chart, waits for both to be
Available, and runs the **post-deploy smoke test** (the token-free public
subset) automatically.

Manual equivalent:

```bash
aws eks update-kubeconfig --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION"
helm upgrade --install private-ai-workspace deploy/helm/private-ai-workspace \
  --namespace app --create-namespace -f deploy/values/dev/app.yaml --wait
helm upgrade --install private-ai-ui deploy/helm/private-ai-ui \
  --namespace app \
  --set image.repository="$ECR_UI_URL" --set image.tag=<tag> \
  --set oidc.issuer="$OIDC_ISSUER" --set oidc.clientId="$OIDC_CLIENT_ID" \
  --set oidc.redirectUri="$UI_REDIRECT_URI" \
  --set ingress.host="$UI_INGRESS_HOST" \
  -f deploy/values/dev/ui.yaml --wait
```

## 4. Obtain two bearer tokens (two tenants)

The most reliable way is to capture exactly what the SPA sends:

1. Open `https://$UI_INGRESS_HOST`, sign in as user A.
2. In browser devtools → Network, open any `/v1/...` request and copy the
   `Authorization: Bearer <token>` value → `TOKEN_A`.
3. Repeat in a private window as user B → `TOKEN_B`.

CLI alternative (Cognito, if `ADMIN_USER_PASSWORD_AUTH` is enabled on the app
client):

```bash
aws cognito-idp admin-initiate-auth --user-pool-id <pool> \
  --client-id "$OIDC_CLIENT_ID" --auth-flow ADMIN_USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=<user>,PASSWORD=<pass> \
  --query 'AuthenticationResult.AccessToken' --output text
```

Use whichever token type the UI sends as its `Authorization` header (it must
carry the `email` claim, which selects the tenant).

## 5. Run the smoke test against the deployment

Point `--base` at the control-plane API (port-forward, or its ingress):

```bash
kubectl -n app port-forward \
  deployment/private-ai-workspace-private-ai-workspace 8080:8080 &

./scripts/smoke-test.sh --base http://localhost:8080 \
  --token "$TOKEN_A" --token-b "$TOKEN_B"
```

This drives the same control-plane API the UI calls: auth gating, the
authenticated chat path, the notification round trip (publish → list →
mark-read/dismiss), the content-policy guards, and — because `--token-b` is
supplied — the **cross-tenant isolation probe** (B must not see A's
notification and must get 404 trying to mark it read).

Expected: `All smoke tests passed.`

## 6. Record and unblock the PR

Paste the step-5 output (and a note of the deployed image tag + cluster) into
[PR #19](https://github.com/GetFinops/private-ai-workspace-eks/pull/19), then:

```bash
gh pr ready 19
```

That clears the last M9 exit criterion.

## Troubleshooting

- **`401` on the authenticated checks** — the token's audience/issuer does not
  match the control plane's `AUTH_AUDIENCE`/`AUTH_ISSUER_URL`. Confirm
  `OIDC_*` vars and the app-config secret point at the same pool + client.
- **UI pod not Available** — almost always an unpullable image
  (`ECR_UI_URL`/tag) or a failing `/healthz`; check
  `kubectl -n app logs deploy/private-ai-ui`.
- **Cross-tenant probe shows a leak** — stop and treat as a release blocker;
  cross-check against `tests/test_notifications.py` isolation cases.
