# Private AI Workspace on EKS

`private-ai-workspace-eks` is an open-source bootstrap for a self-hosted, multi-user AI workspace designed for private organizational deployment on AWS EKS.

## Positioning

This project is intended for:

- SMB and enterprise teams running a dedicated deployment inside their own environment
- private model serving via vLLM or compatible inference backends
- maintainer-controlled open-source development

This project is not presented as the official Odysseus project or an AWS-endorsed derivative.

## Current Status

This repository is currently in bootstrap mode. It includes:

- governance and contribution policy files
- an initial docs baseline
- CI and pull request scaffolding
- empty implementation directories for `app/`, `infra/`, `deploy/`, `scripts/`, and `tests/`

## Planned Scope

- control plane for users, sessions, model routing, and orchestration
- EKS deployment packaging
- externalized state and managed secret handling
- local model inference integration via vLLM
- observability and scaling hooks for production deployments

## Repository Layout

```text
.
├── .github/
├── app/
├── deploy/
├── docs/
├── infra/
├── scripts/
└── tests/
```

## Governance

- pull requests are required for default-branch changes
- at least one maintainer review is required
- force-pushes to the protected branch are disabled
- contributors are expected to use DCO sign-off

See `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `NOTICE`.

## Licensing

The repository is released under the MIT License. Attribution expectations for upstream-inspired work are documented in `NOTICE`.
