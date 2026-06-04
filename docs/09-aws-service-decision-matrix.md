# AWS Service Decision Matrix

## Purpose

This matrix compares the main AWS and deployment choices that matter for the first implementation.

## Database

### Option 1: `RDS PostgreSQL`

Pros:

- simpler and cheaper starting point
- enough for MVP and early production
- broad compatibility

Cons:

- fewer advanced scale and failover capabilities than Aurora

Recommended when:

- you want the fastest practical start

### Option 2: `Aurora PostgreSQL`

Pros:

- stronger scaling and failover posture
- better long-term production option for heavier growth

Cons:

- more complexity and potentially higher cost

Recommended when:

- you already expect higher scale or stronger HA needs

### Recommendation

Start with:

- `RDS PostgreSQL`

Upgrade later only if real scale or HA needs justify Aurora.

## Object Storage

### Option 1: `Amazon S3`

Pros:

- obvious fit for uploads and artifacts
- durable
- easy lifecycle policies

Cons:

- requires application changes if local filesystem assumptions still exist

### Recommendation

Use:

- `Amazon S3`

## Secrets

### Option 1: `Secrets Manager`

Pros:

- best for application secrets
- rotation support
- clear AWS integration

Cons:

- slightly more cost than simpler parameter storage

### Option 2: `SSM Parameter Store`

Pros:

- simpler for non-sensitive config
- lower friction for some settings

Cons:

- weaker fit for richer secret workflows

### Recommendation

Use:

- `Secrets Manager` for secrets
- `SSM Parameter Store` optionally for non-secret config

## Kubernetes Packaging

### Option 1: `Helm`

Pros:

- common for application deployment packaging
- easier value overrides by environment
- good for reusable charts

Cons:

- templating can become hard to reason about if overused

### Option 2: `Kustomize`

Pros:

- simpler overlays
- clearer raw Kubernetes manifests

Cons:

- less parameterized than Helm

### Recommendation

Use:

- `Helm` for application packaging
- optionally raw manifests or Kustomize for very small supporting components

## Infrastructure Provisioning

### Option 1: `Terraform`

Pros:

- strong ecosystem
- widely used for EKS and AWS infra
- good for shared infrastructure state

Cons:

- needs good state discipline

### Option 2: `AWS CDK`

Pros:

- code-first
- good if the team prefers TypeScript or Python infrastructure code

Cons:

- can be more opinionated
- generated CloudFormation may complicate some debugging

### Recommendation

Use:

- `Terraform` if the project aims for broad infra familiarity

## GPU Capacity Management

### Option 1: `Managed GPU node groups`

Pros:

- simpler operationally
- easier first implementation

Cons:

- less elastic than Karpenter
- more manual scaling behavior

### Option 2: `Karpenter`

Pros:

- dynamic provisioning
- stronger fit for spot-first inference
- better long-term elasticity

Cons:

- more moving parts
- requires stronger observability and policy tuning

### Recommendation

Use:

- managed GPU node groups for the first stable baseline if simplicity matters most
- `Karpenter` once the inference profile and scaling signals are understood

## Inference Autoscaling

### Option 1: `HPA`

Pros:

- native Kubernetes
- simple baseline

Cons:

- needs custom metrics setup for inference-aware scaling

### Option 2: `KEDA`

Pros:

- stronger event and queue-driven scaling patterns
- useful if inference queue depth is central

Cons:

- extra component and operational surface

### Recommendation

Use:

- `HPA` first if custom Prometheus metrics are enough
- `KEDA` if queue-driven or event-driven scaling becomes central

## Observability

### Option 1: `CloudWatch-first`

Pros:

- easier AWS-native operations
- less self-managed stack burden

Cons:

- less flexible dashboarding than Prometheus plus Grafana

### Option 2: `AMP + AMG`

Pros:

- managed Prometheus and Grafana
- good fit for Kubernetes and GPU metrics

Cons:

- still needs good metric design and remote-write setup

### Option 3: `Self-managed Prometheus + Grafana`

Pros:

- maximum flexibility
- easy to adapt community dashboards

Cons:

- more operational overhead

### Recommendation

Use:

- `CloudWatch` for logs and base infrastructure visibility
- `AMP + AMG` or `Prometheus + Grafana` for Kubernetes, vLLM, and GPU metrics

## Vector Store

### Option 1: `Postgres + pgvector`

Pros:

- fewer systems
- simplest early architecture

Cons:

- may not be the best long-term fit for larger retrieval workloads

### Option 2: dedicated vector store

Pros:

- better specialization
- clearer scaling path for retrieval

Cons:

- adds another system early

### Recommendation

Use:

- `Postgres + pgvector` if retrieval scope is modest at launch
- dedicated vector infrastructure later if scale demands it

## Search

### Option 1: omit search in MVP

Pros:

- lower complexity
- avoids unnecessary dependency surface

Cons:

- reduced feature completeness

### Option 2: external optional search service

Pros:

- preserves feature path without forcing it into the core platform

Cons:

- more integration work

### Recommendation

Use:

- optional external search service, not bundled by default in the first release

## Final Recommended Starting Stack

If the goal is to start cleanly and reach production safely, the best first-pass combination is:

- `RDS PostgreSQL`
- `S3`
- `Secrets Manager`
- `Terraform`
- `Helm`
- CPU node pool for app
- managed GPU node group first, then `Karpenter`
- `HPA` with custom metrics first
- `CloudWatch` plus `AMP/AMG` or Prometheus/Grafana
- `Postgres + pgvector` if vector scope stays moderate
