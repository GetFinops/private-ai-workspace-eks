# M1 — Control Plane Skeleton

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

Partial. The configuration model, health and readiness endpoints, inference
contract types, endpoint routing, and a standard-library inference client
exist. The chat request path, real authentication enforcement, and a session
store are not yet built. See `docs/11-gap-analysis.md`.

## Objective

Stand up a usable control-plane application that runs without GPU dependency
and exposes an authenticated path to the internal inference contract.

## Primary workstreams

- product-app

## Prerequisites

- M0 complete.

## In scope

- a chat request path that uses the existing inference client
- authentication verification and request-level enforcement
- session handling backed by an externalizable store interface
- configuration and readiness coverage for the above
- a container image build for the control plane

## Non-goals

- live cloud provisioning (M2)
- replacing local-development persistence with managed services (M3)
- deploying a real vLLM backend (M4)
- metrics, dashboards, and tracing depth (M5)

## Build tasks

1. Add a write-capable request path to the control-plane HTTP surface
   (`app/control_plane/server.py`) for chat completions. Reuse
   `app/control_plane/inference.py` and `app/control_plane/routing.py`. Do not
   re-implement endpoint URL handling.
2. Enforce authentication on the chat path. Build on the existing
   `app/control_plane/auth.py` primitives. Verify a bearer credential against
   the configured issuer and audience. Reject unauthenticated and
   unauthorized requests with explicit status codes. Do not add anonymous or
   localhost bypasses.
3. Define a session-store interface in `app/control_plane/session.py` (or a new
   sibling module) so session state can live in an external store later. Keep
   any in-memory implementation clearly marked as development-only.
4. Extend `app/control_plane/config.py` and `readiness_checks()` so the new
   capabilities are represented in `/readyz` without leaking secrets.
5. Keep the control plane operational when the inference backend is
   unconfigured or unavailable. Inference failures must degrade gracefully, not
   crash the process.
6. Ensure the container image under `app/` builds and starts the service
   without local-only filesystem assumptions.

## Provenance and licensing checkpoints

- If authentication or session patterns are adapted from upstream, preserve the
  required notices and record the provenance in `NOTICE`.
- Do not introduce copyleft-sensitive optional features.
- Prefer standard-library or already-approved dependencies; new third-party
  dependencies need maintainer review for license compatibility.

## Security checkpoints

- Authentication must be enforced server-side on protected routes.
- Never log prompts, tokens, secrets, or message content without a reviewed,
  redacted policy.
- Validate the inference base URL through the existing routing layer; do not
  accept arbitrary schemes or embedded credentials.
- Treat the inference backend as internal-only.

## Testing and validation

- Add unit tests covering the chat path request shaping, the authentication
  decision (allow, reject-unauthenticated, reject-unauthorized), and the
  session-store interface.
- Add a test that the service starts and serves health and readiness without
  inference configured.
- `python3 -m unittest discover -s tests` passes.
- Manually exercise the chat path against a stub backend and capture the
  request and response, the unauthorized rejection, and the degraded-mode
  behavior when inference is unavailable.

## Exit criteria

- The application builds locally and in CI.
- A container image is produced.
- The control-plane API starts without local-only filesystem assumptions.
- An authenticated chat path reaches the inference contract and degrades
  gracefully when inference is unavailable.

## Escalation triggers

- the authentication verification design and the identity-provider contract
- the session-store interface and its persistence semantics
- any new third-party dependency
