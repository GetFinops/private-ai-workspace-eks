# M2 — EKS Baseline Deployment

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

Scaffolded, not exercised. The Terraform modules
(`infra/terraform/modules/`) and Helm charts
(`deploy/helm/private-ai-workspace/`) exist but have not been deployed end to
end.

## Objective

Deploy the control plane to AWS EKS on CPU nodes as a single replica, with
managed-persistence foundations reachable from the cluster.

## Primary workstreams

- platform-infra
- product-app

## Prerequisites

- M1 complete.

## In scope

- provisioning the base AWS infrastructure from `infra/terraform/`
- creating the EKS cluster and CPU node group
- ingress, DNS, and TLS for the control plane
- deploying the control-plane chart to a development environment
- connecting the deployed app to the managed database and object storage at the
  configuration level

## Non-goals

- moving application persistence onto managed services in code (M3)
- GPU capacity and vLLM (M4)
- full observability instrumentation (M5)
- autoscaling (M6)

## Build tasks

1. Review and complete the Terraform baseline in `infra/terraform/`: VPC, EKS,
   ECR, RDS, and S3. Keep the application pods in private subnets and the
   database private-only.
2. Keep public exposure limited to the load balancer and ingress for the
   control plane. Do not expose internal services publicly.
3. Provision per-environment values from the example tfvars under
   `infra/terraform/tfvars/`. Do not commit real secrets or environment-specific
   credentials.
4. Deploy the control-plane Helm chart
   (`deploy/helm/private-ai-workspace/`) to the development environment with a
   single replica.
5. Wire configuration and secret references through managed secret storage and
   environment configuration, not plaintext chart values.
6. Confirm the running app can read its database and object-storage
   configuration and reports readiness accordingly.

## Provenance and licensing checkpoints

- Terraform adapted from permissive AWS samples must keep its provenance record
  in `NOTICE`.
- Review any new Terraform modules or providers for license compatibility
  before adoption.

## Security checkpoints

- Use role-based pod access to AWS services rather than static credentials
  where possible.
- Keep database and signing secrets in managed secret storage.
- Apply network boundaries: public traffic reaches only ingress; app-to-internal
  traffic stays private.
- Do not commit cloud credentials or environment secrets.

## Testing and validation

- Validate Terraform formatting and planning in a non-destructive way before
  apply.
- After deployment, confirm health and readiness through the public ingress.
- Confirm the deployment is reproducible from the infrastructure and deployment
  definitions.
- Capture evidence: a successful plan, the running replica, and a successful
  health and readiness check through ingress.

## Exit criteria

- One app replica runs in EKS.
- Public ingress to the control plane works.
- The app can read its managed-persistence configuration.
- The deployment is reproducible from infrastructure and deployment
  definitions.

## Escalation triggers

- production networking exposure decisions
- IAM and secret-access design
- any change that could expose internal services publicly
