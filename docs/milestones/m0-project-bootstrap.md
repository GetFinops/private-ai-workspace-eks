# M0 — Project Bootstrap

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

Complete. This file documents the bootstrap bar so regressions are easy to
detect and so later structural changes preserve the foundation.

## Objective

Maintain a clean public repository with governance, licensing, documentation,
and structure in place before feature work proceeds.

## Primary workstreams

- governance-security
- platform-infra

## Prerequisites

None.

## In scope

- top-level governance and policy files
- top-level licensing and attribution files
- the published planning bundle under `docs/`
- contribution templates and CI structure checks
- the empty-but-structured implementation directories

## Non-goals

- application features
- live cloud provisioning
- deployment to any environment

## Build tasks

This milestone is already satisfied. Tasks here are maintenance and
regression-prevention rather than new build work.

1. Keep these root files present and accurate: `README.md`, `LICENSE`,
   `NOTICE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`,
   `CODEOWNERS`.
2. Keep `.github/PULL_REQUEST_TEMPLATE.md` and the issue templates aligned with
   the contribution model in `docs/04-governance-and-contribution.md`.
3. Keep the `app/`, `infra/`, `deploy/`, `scripts/`, and `tests/` directories
   present and within their documented boundaries.
4. Keep the planning bundle under `docs/` complete and indexed by
   `docs/README.md`.
5. When repository structure changes, update the CI structure checks in
   `.github/workflows/ci.yml` accordingly.

## Provenance and licensing checkpoints

- The top-level license is permissive and must stay permissive.
- `NOTICE` must remain the single source of truth for adapted-code provenance.
- Branding must not present the project as an official upstream project or an
  endorsed derivative.

## Security checkpoints

- Branch protection and maintainer-review controls must remain active on the
  default branch.
- No secrets may be committed to the repository.

## Testing and validation

- `python3 -m unittest discover -s tests` passes.
- The CI structure checks pass: required files and directories exist.

## Exit criteria

- The public repository exists with active merge protection.
- Project policies are visible from the root.
- Implementation directories exist and are structured.

## Escalation triggers

- any change to branch protection or governance controls
- any change to the top-level license or attribution policy
