# Scaling & Fallback Policy (M6)

This document records the elastic-scaling and capacity-shortage policy for
`private-ai-workspace-eks`.  It complements
[`06-cloud-architecture.md`](06-cloud-architecture.md) (target architecture) and
[`09-aws-service-decision-matrix.md`](09-aws-service-decision-matrix.md)
(option comparison).

## Two-plane scaling model

The platform splits compute into two planes and scales them independently:

| Plane | Capacity owner | Pod autoscaler | Cold start |
|-------|---------------|----------------|------------|
| Control plane (CPU) | Managed node group + **cluster-autoscaler** | HPA on CPU/memory | seconds |
| Inference plane (GPU) | **Karpenter** NodePool + (optional) managed warm-pool NG | HPA on `vllm_num_requests_waiting` | 60–180 s |

The control plane must remain usable even when GPU capacity is cold, scaling,
or unavailable.  See [Degraded mode](#degraded-mode-policy) below.

## GPU capacity policy

### Warm pool (per-environment)

| Env | `gpu_warm_pool_size` | Reasoning |
|-----|----------------------|-----------|
| dev | 0 | Cheapest; first request always degraded until Karpenter provisions a node |
| staging | 0 | Exercise the cold-start path realistically |
| prod | 1 | Single warm replica eliminates first-user cold-start; ~$0.50/h per warm `g5.xlarge` spot |

`gpu_warm_pool_size` is a Terraform input on the existing managed GPU node
group (`min_size = desired_size = gpu_warm_pool_size`).  It is **not** a vLLM
replica count — it controls *node* capacity that Karpenter cannot evict.

### Burst capacity (Karpenter)

Karpenter `NodePool` constraints:

- **Instance families:** `g5`, `g6`, `g6e` (Ada/Lovelace; current-generation NVIDIA)
- **Capacity type:** `spot` first; falls back to `on-demand` after spot interruption
- **Limits:** total GPU count capped at 8 per environment (override per env)
- **Consolidation:** `WhenUnderutilized` with 30-minute consolidation budget
- **Disruption budget:** at most 1 GPU node removed per 5 minutes during business hours

When demand exceeds the warm pool, Karpenter provisions additional GPU nodes
on the spot market.  When demand falls and consolidation conditions are met,
nodes are drained and terminated.

### Spot-capacity risk acceptance

Spot interruption is acceptable for this platform because:

1. Inference requests are short-lived (seconds) and stateless.
2. The control plane returns `503 + Retry-After` on interruption-driven
   `InferenceUnavailableError` (already implemented in M4).
3. Cold-start of a replacement spot node is bounded (~120 s for `g5.xlarge`).

Operators who require strict latency SLAs should override `gpu_capacity_type`
to `ON_DEMAND` in production Terraform values.  This is a knob, not a default.

## Pod autoscaling (HPA)

The vLLM Deployment is fronted by a `HorizontalPodAutoscaler` that targets
the vLLM-native metric `vllm_num_requests_waiting` (queue depth).

| Field | Default | Notes |
|-------|---------|-------|
| `minReplicas` | `1` | Always one ready pod when autoscaling is on |
| `maxReplicas` | `4` | Bounded by `nvidia.com/gpu` limit on the NodePool |
| `target_queue_depth` | `10` | Triggers scale-up when avg pending requests > 10 |

The metric is exposed to the Kubernetes custom-metrics API by
**prometheus-adapter** (Apache-2.0) deployed in the `cluster-addons` chart.
prometheus-adapter reads from the in-cluster Prometheus installed by the M5
observability chart.

## Degraded-mode policy

When inference capacity is **unavailable** for any reason
(spot interruption, model loading, queue saturation, node provisioning in flight),
the control plane behaves as follows:

| Trigger | Response | Headers |
|---------|----------|---------|
| `InferenceRoutingError` (DNS / network) | 503 `inference_not_reachable` | `Retry-After: 10` |
| `InferenceUnavailableError` HTTP 5xx | 503 `inference_unavailable` | `Retry-After: 30` |
| `InferenceUnavailableError` HTTP 429 | 503 `inference_unavailable` (capacity) | `Retry-After: 60` |
| `TimeoutError` | 503 `inference_timeout` | `Retry-After: 30` |

These are implemented in `app/control_plane/server.py::build_chat_response()`
and verified by `tests/test_m4_inference.py`.

## Fallback policy — **degrade-only**

**This project does not call external inference providers.**

Rationale:
- The platform's design stance (AGENTS.md) is *self-hosted,
  organization-private, maintainer-controlled, not a shared multi-tenant SaaS*.
- An external-provider fallback (OpenAI, Bedrock, etc.) would forward user
  prompts and completions to a third party, materially changing the
  data-privacy posture.
- The observability content policy (`07-observability.md`) explicitly
  forbids prompt/completion text in any telemetry surface; an external
  fallback would conflict with that posture unless re-scoped under an
  explicit reviewed policy.

What we ship instead:
- Aggressive in-cluster elasticity (Karpenter + spot + HPA on queue depth).
- Honest `Retry-After` headers so clients back off without hammering the API.
- A documented warm-pool option for production.
- **Optional in-cluster secondary model**: the chart supports deploying a
  second vLLM release (e.g. a smaller model on a smaller GPU) but the
  control plane does not perform automatic failover between them in M6.
  Cross-deployment routing is deferred to M7+.

### How external-provider fallback would be added (if ever)

Out of scope for M6.  If a future milestone reopens this question, the
implementation **must** include:

- per-request opt-in header or per-tenant policy in the database
- provider credentials in AWS Secrets Manager via IRSA
- structured audit log row per external call (timestamp, user/session ID,
  provider, latency, token counts — never content)
- operator kill-switch environment variable
- maintainer review and a NOTICE entry for the provider SDK

See AGENTS.md "Escalate Instead Of Guessing" and the M6 milestone doc's
"Escalation triggers".

## Verification

| Aspect | How tested |
|--------|-----------|
| Scale-up on real demand | Load test against `/v1/chat/completions`; observe HPA scale 1→N |
| Spot capacity shortage | Force Karpenter to fail provisioning; verify control plane returns 503 + Retry-After (no crash) |
| Cold start | Scale GPU node count to 0; first request degraded; subsequent requests succeed after Karpenter provisions |
| Warm pool | Set `gpu_warm_pool_size=1`; verify one GPU node always present even with no demand |

## References

- `infra/terraform/modules/eks/main.tf` — node groups
- `deploy/helm/cluster-addons/values.yaml` — autoscaler / Karpenter / adapter wiring
- `deploy/helm/vllm/templates/hpa.yaml` — HPA definition
- `app/control_plane/server.py::build_chat_response()` — degraded-mode responses
- `tests/test_m4_inference.py` — Retry-After regression tests
- `tests/test_m6_scaling.py` — chart/template regression tests (M6)
