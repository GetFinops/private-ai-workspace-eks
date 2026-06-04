# M6 — Elastic GPU Scaling

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

Not started. GPU capacity is currently a managed node group in the Terraform
baseline; demand-driven scaling and warm-pool behavior are not implemented.

## Objective

Enable demand-driven GPU capacity scaling with clear service behavior during
cold starts and spot shortages.

## Primary workstreams

- ml-inference
- platform-infra

## Prerequisites

- M5 complete. Scaling decisions depend on the observability signals from M5.

## In scope

- a GPU capacity policy
- a managed-node-group or dynamic-provisioning transition path
- inference autoscaling wired to demand signals
- queueing, warm-pool, and fallback behavior under load

## Non-goals

- staging soak and recovery hardening (M7)
- production launch (M8)

## Build tasks

1. Define the GPU capacity policy: minimum warm capacity, scale thresholds, and
   spot-versus-on-demand behavior. Follow the spot-first guidance in
   `docs/06-cloud-architecture.md` without making the control plane depend on
   immediate GPU availability.
2. Choose and implement the scaling mechanism: managed node groups with
   autoscaling, or dynamic node provisioning. Follow
   `docs/09-aws-service-decision-matrix.md`.
3. Wire pod autoscaling to inference-aware metrics (pending requests,
   concurrency, queue depth, saturation) rather than CPU alone.
4. Implement and test queueing, warm-pool, and fallback behavior during
   scale-up and capacity shortage.
5. Document the cold-start behavior and the production fallback policy.

## Provenance and licensing checkpoints

- Review any autoscaling or provisioning components for license compatibility.
- Record provenance for adapted scaling configuration in `NOTICE`.

## Security checkpoints

- Keep GPU nodes and inference services internal; scaling must not change their
  exposure.
- Ensure new node capacity inherits the same network boundaries and isolation.

## Testing and validation

- Demonstrate scale-up reacting to a real demand signal.
- Demonstrate acceptable behavior during a simulated capacity shortage
  (queueing or retry-after, not a control-plane outage).
- Capture evidence: a scaling event driven by demand and a clean
  capacity-shortage response.

## Exit criteria

- Inference scaling reacts to real demand signals.
- Cold-start behavior is documented and acceptable.
- The production fallback policy is tested.

## Escalation triggers

- the production fallback policy, especially any external-provider fallback
- spot-capacity risk acceptance
