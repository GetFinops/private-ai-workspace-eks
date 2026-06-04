# M4 — Inference Plane MVP

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

Scaffolded on the client and chart side. The control plane has an inference
client (`app/control_plane/inference.py`) and there is a vLLM Helm chart
(`deploy/helm/vllm/`), but no real vLLM backend has been deployed and connected.

## Objective

Run an isolated vLLM inference service on GPU nodes and connect the control
plane to it through the internal contract, with defined failure behavior.

## Primary workstreams

- ml-inference
- product-app
- platform-infra

## Prerequisites

- M3 complete.

## In scope

- a vLLM deployment on isolated GPU capacity
- an internal-only inference endpoint
- control-plane model routing to that endpoint
- defined timeout, fallback, and degraded behavior

## Non-goals

- elastic GPU autoscaling (M6)
- full observability dashboards and alerts (M5)
- multiple model families beyond what the MVP requires

## Build tasks

1. Deploy vLLM via `deploy/helm/vllm/` onto GPU-tainted nodes with appropriate
   tolerations and resource requests. Keep the service internal to the cluster.
2. Expose the inference endpoint only inside the cluster or private network.
   Public ingress belongs at the control plane, never at model serving.
3. Point the control plane at the internal endpoint through configuration. Use
   the existing routing and client layers; do not embed model-serving logic in
   the application.
4. Confirm the timeout and retry policy in the inference client matches
   `docs/inference-contract.md` and the failure-mode guidance in
   `docs/06-cloud-architecture.md`. Adjust if the deployed behavior differs.
5. Define and implement degraded behavior when GPU capacity is cold or
   unavailable: clear messaging, queued or retry-after responses, and an
   explicit fallback policy if one is adopted.
6. Confirm the control plane remains available when inference is unavailable.

## Provenance and licensing checkpoints

- Review the vLLM image and any model artifacts for licensing before use.
- Keep copyleft-sensitive optional features out of the inference path.
- Record provenance for any adapted inference-serving configuration in
  `NOTICE`.

## Security checkpoints

- The inference service must not be publicly reachable.
- Restrict inference access to control-plane workloads via network boundaries.
- Do not log prompts or completions without a reviewed, redacted policy.

## Testing and validation

- Verify the control plane can send a chat-completion request to vLLM in the
  cluster and receive a valid response.
- Verify failure modes are visible and handled: timeout, backend error, and no
  capacity.
- Verify the control plane stays operational with inference unavailable.
- Capture evidence: a successful inference round-trip and a clean degraded-mode
  response.

## Exit criteria

- The app successfully sends inference requests to vLLM in EKS.
- Failure modes are visible and handled.
- The app remains operational when inference is unavailable.

## Escalation triggers

- inference network exposure and isolation
- fallback policy that routes to any external provider
- model selection and licensing
