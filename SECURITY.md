# Security Policy

## Scope

This repository is intended for private organizational deployment and is still
in an early bootstrap stage. Security reports are welcome, especially around:

- authentication and authorization
- secret handling
- deployment hardening
- network exposure
- data isolation
- supply-chain and dependency risk

## Reporting

Please do not open a public issue for a suspected security vulnerability.

Instead:

- use GitHub private vulnerability reporting if enabled
- or contact the maintainers privately through the organization security channel

## Disclosure

- we will validate and triage reports privately
- fixes for sensitive issues may be prepared before public disclosure
- once safe, affected users will be informed through a coordinated disclosure

## Deployment Guidance

Until the project matures, treat production rollout as controlled and reviewed:

- use dedicated environments per organization
- use managed secrets instead of plaintext config
- review ingress, auth, and storage configuration before exposure
- avoid enabling optional copyleft-sensitive or unreviewed integrations by default
