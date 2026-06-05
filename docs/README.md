# Documentation

This directory contains the public documentation baseline and planning bundle
for the project bootstrap.

## Planning Bundle

- [Licensing and policy review](01-licensing-and-policy.md)
- [Review summary](02-review-summary.md)
- [Implementation plan](03-implementation-plan.md)
- [Governance and contribution](04-governance-and-contribution.md)
- [Build readiness checklist](05-build-readiness-checklist.md)
- [Cloud architecture](06-cloud-architecture.md)
- [Observability](07-observability.md)
- [Repo bootstrap plan](08-repo-bootstrap.md)
- [AWS service decision matrix](09-aws-service-decision-matrix.md)
- [Delivery roadmap](10-delivery-roadmap.md)
- [Gap analysis](11-gap-analysis.md)
- [Phase 2 feature adoption track (M9+, proposed)](12-phase-2-feature-adoption.md)

## Milestone Instructions

Per-milestone build instructions for automated and human contributors live in
[`milestones/`](milestones/README.md). They expand the delivery roadmap into
build-ready guidance with scope, tasks, provenance and security checkpoints,
testing requirements, and exit criteria.

The committed roadmap covers milestones M0–M8 (the platform baseline). Proposed
product features that could layer on top of that baseline are analyzed,
licensing-reviewed, and sequenced as a candidate M9+ track in
[Phase 2 feature adoption](12-phase-2-feature-adoption.md). That track is
exploratory and gated by maintainer decision; it is not committed scope.

## Current Implementation Docs

- The root `README.md` summarizes project scope, non-goals, repository layout,
  and the initial AWS stack decisions.
- [Internal inference contract](inference-contract.md) documents the initial
  control-plane to vLLM boundary.
- `app/` contains the first control-plane skeleton with health/readiness
  endpoints and an internal vLLM-compatible inference contract.

## Documentation Rules

- Keep architecture and deployment assumptions explicit.
- Keep upstream provenance and licensing notes visible for review.
- Do not describe this project as the official Odysseus project or an
  AWS-endorsed derivative.
