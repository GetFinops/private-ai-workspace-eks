# Helm Chart

This chart deploys the CPU control-plane skeleton. It intentionally exposes only
a `ClusterIP` service by default and expects secrets to be provided by managed
secret integration such as External Secrets Operator or AWS Secrets Manager CSI.

## Required Secret Keys

Set `externalSecrets.existingSecret` to a Kubernetes Secret containing:

- `DATABASE_URL`
- `AUTH_ISSUER_URL`
- `AUTH_AUDIENCE`
- `AUTH_ADMIN_GROUP`

`OBJECT_STORAGE_BUCKET` and `INFERENCE_BASE_URL` are non-secret configuration
values supplied through `values.yaml`.
