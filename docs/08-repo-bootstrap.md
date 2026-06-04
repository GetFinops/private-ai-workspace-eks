# Repo Bootstrap Plan

## Goal

Create the initial public repository skeleton so implementation can begin cleanly, with legal, governance, infrastructure, and application boundaries already defined.

## Recommended Top-Level Structure

```text
project-root/
  README.md
  LICENSE
  NOTICE
  CONTRIBUTING.md
  CODE_OF_CONDUCT.md
  SECURITY.md
  CODEOWNERS
  .github/
    workflows/
    ISSUE_TEMPLATE/
    PULL_REQUEST_TEMPLATE.md
  docs/
    01-licensing-and-policy.md
    02-review-summary.md
    03-implementation-plan.md
    04-governance-and-contribution.md
    05-build-readiness-checklist.md
    06-cloud-architecture.md
    07-observability.md
    09-aws-service-decision-matrix.md
  app/
  infra/
  deploy/
  scripts/
  tests/
```

## Bootstrap Sequence

### Phase 0: project identity

- choose project name
- choose repository owner
- choose top-level license
- define attribution and provenance policy

### Phase 1: governance baseline

- add `LICENSE`
- add `NOTICE`
- add `CONTRIBUTING.md`
- add `CODE_OF_CONDUCT.md`
- add `SECURITY.md`
- add `CODEOWNERS`
- enable branch protection
- enable DCO enforcement if used

### Phase 2: documentation baseline

- move this planning bundle into `docs/`
- add architecture overview to root `README.md`
- define MVP scope and non-goals

### Phase 3: implementation skeleton

- create `app/` for the control-plane service
- create `infra/` for cloud and cluster provisioning
- create `deploy/` for Kubernetes packaging
- create `tests/` for integration and architecture tests

### Phase 4: delivery baseline

- add CI for lint and test
- add image build pipeline
- add dependency and image scanning
- add dev deployment target

## Suggested Ownership Model

### `app/`

Contains:

- API server
- orchestration logic
- auth and user/session logic
- provider routing
- worker integration

### `infra/`

Contains:

- VPC and networking definitions
- EKS cluster definitions
- database and storage provisioning
- IAM and secrets integration
- managed observability service configuration

### `deploy/`

Contains:

- Helm charts or Kubernetes manifests
- environment values
- service and ingress definitions
- autoscaling configuration

## First Milestone Bootstrap Deliverables

The repo is considered ready for real build work once it has:

- governance files
- docs bundle
- empty but structured `app/`, `infra/`, and `deploy/` directories
- CI placeholder workflow
- issue and PR templates
- branch protection enabled

## Suggested First README Sections

- project purpose
- what is inspired by Odysseus and what is newly built
- licensing and attribution note
- architecture summary
- current status
- contribution model

## Risk Controls During Bootstrap

- do not copy code into the repo until provenance rules are defined
- do not add AGPL-sensitive optional features in the first milestone
- do not let infrastructure and application code blur together
- do not expose merge rights broadly before branch protections are active
