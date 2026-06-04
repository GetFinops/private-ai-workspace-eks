# Build Readiness Checklist

## Start Here

Before implementation begins, confirm these decisions:

- the new project will be a **new repository**, not a public mirrored fork
- the top-level license will be permissive
- AGPL-sensitive optional features stay out of the first release
- contribution model will be maintainer-controlled with DCO sign-off

## Repo Foundation Checklist

- choose project name and branding
- create the new repository
- add `LICENSE`
- add `NOTICE` or `ATTRIBUTION`
- add `CONTRIBUTING.md`
- add `CODE_OF_CONDUCT.md`
- add `SECURITY.md`
- enable branch protection
- add `CODEOWNERS`

## Provenance Checklist

For every file or subsystem copied or adapted from Odysseus:

- record source path
- record upstream license
- record whether copied, adapted, or rewritten
- preserve any required header or notice

## MVP Build Checklist

- define the control-plane scope
- define the inference-plane contract
- pick database and object storage
- choose vector store strategy
- define secret management and config model
- define observability baseline

## EKS Readiness Checklist

- decide packaging format: manifests, Helm, or both
- define GPU node-pool policy for spot usage
- define autoscaling metrics for inference
- define warm-capacity versus cold-start policy
- define cost and failure fallback behavior

## Security Checklist

- normalize outbound integration URL validation
- use encrypted or managed-secret storage from day one
- avoid plaintext credential persistence
- keep admin-only operations isolated and reviewed
- make startup and tests independent from local ad hoc paths

## Immediate Next Build Sequence

1. create the new repo skeleton and governance files
2. define the minimal app control plane
3. externalize persistence
4. connect to a standalone vLLM service
5. deploy a single-replica EKS baseline
6. add GPU autoscaling and hardening after the baseline works
