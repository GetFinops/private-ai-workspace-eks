# M14 — Media Services (Optional)

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.
>
> This is a Phase 2 milestone. The full Phase 2 governance — adoption gating,
> licensing rules, isolation requirements, and excluded-by-default
> components — is in [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md).
> Read that document before opening any M14 work.

## Status

**Shared harness delivered; first services adopted.** The control-plane media
surface (`app/control_plane/media.py`: routing to isolated GPU backends, deny-by-
default per-tenant allow-list, operator + per-tenant kill-switches, dedicated
rate limiter, server-side size/content caps, per-tenant S3 artifact isolation,
shape-only audit, `media_task_*` notifications) and a generic GPU media-service
Helm chart (`deploy/helm/media-service`, vLLM-shape: ClusterIP, ingress-from-
control-plane NetworkPolicy, GPU taint/nodeSelector, gated-model pattern) shipped
as original code. **Whisper STT (MIT, ungated)** is the first adopted model
(`NOTICE` "M14 Whisper STT"); **SDXL image-gen** is the next per-service
increment. Each further model is a separate per-service adoption + license
review (Phase 2 Decision Checklist).

## Objective

Optional speech, media, and image-generation features deployed as isolated,
independently-scaled services on the GPU plane.

## Primary workstreams

- ml-inference
- product-app
- platform-infra

## Prerequisites

- M4 inference plane (the media services share the GPU isolation pattern).
- M6 GPU capacity policy (media services consume Karpenter-provisioned GPU
  capacity).

## In scope

- service-boundary patterns reused from M4's vLLM deployment
- deployment of each media service as an isolated, independently-scaled
  Helm release
- per-model license review before any model artifact lands in the build
- resource isolation, rate limits, and abuse/content policy

## Non-goals

- bundling media model weights into the control-plane image
- a single media service that fronts multiple model families — each model
  family is its own deployment for isolation and scaling clarity

## Build tasks

1. Choose the *first* media service target (speech-to-text, text-to-speech,
   image generation — pick one). Each subsequent service is its own
   decision and its own per-service M14 sub-task; this scaffold covers
   the shared deployment pattern.
2. Implement the service as its own Helm chart under `deploy/helm/` with
   the same boundary properties as M4's vLLM chart: ClusterIP service,
   NetworkPolicy restricting ingress to the control plane, GPU taint
   tolerations, NodeSelector targeting the inference plane.
3. Pull model weights through the same gated-model pattern used by M4
   (Hugging Face token in Secrets Manager via IRSA). Do not vendor model
   weights into the image.
4. Implement per-tenant rate limits and per-request size limits at the
   control plane. Media operations are expensive and abuse is amplified
   on GPU capacity.
5. Implement a content policy on inputs (e.g. file-size cap on uploaded
   audio, prompt-length cap on image generation) and on outputs (e.g.
   total media size returned per tenant per minute).
6. Add observability metrics: per-service request count, latency, GPU
   utilization (via M5 DCGM exporter), error class. Respect the M5
   content policy — never log media content in telemetry.
7. Wire the new service into the M6 Karpenter NodePool only if its GPU
   profile fits; introduce a separate NodePool if the instance family
   differs significantly.
8. Emit `media.task.completed` and `media.task.failed` events into the
   M9 notifications service for media operations that exceed an interactive
   latency budget (typical for image generation or long audio
   transcription). Events carry only event class, task id, and
   timestamps — never media content or prompts.

## Provenance and licensing checkpoints

- **Per-model license review is mandatory.** An MIT codebase does not
  imply permissively-licensed models. Record every model's license,
  source, and acceptance status in `NOTICE`.
- Review the service runtime (e.g. Whisper, SDXL) for license
  compatibility before adoption.
- Reject media models whose licenses are incompatible or unreviewed.

## Security checkpoints

- Service is internal-only (ClusterIP + NetworkPolicy), like vLLM in M4.
- No ambient cloud credentials reachable from the media pod beyond what
  IRSA explicitly grants.
- Rate and size limits enforced at the control plane (server-side), not
  only at the UI.
- Content policy on inputs and outputs is enforced and observable.
- Uploaded media is treated as user data and follows the same
  data-handling rules as session content (S3 with per-tenant scoping,
  no cross-tenant access).

## Testing and validation

- At least one media service runs isolated, with reviewed model
  licensing and enforced limits.
- Cross-tenant access to media artifacts (uploaded or generated) is
  impossible (validated with scripted tests).
- Rate-limit and size-limit tests reject oversized requests with a
  clear error.
- GPU metrics for the new service appear on the M5 Grafana dashboards.
- If M9 is deployed, a `media.task.completed` event reaches the
  originating user's feed; cross-tenant publishers cannot emit into
  another tenant's feed.

## Dev deployment validation

Per the standing Phase 2 rule in `docs/milestones/README.md`:

- Enable the chosen media service in `deploy/values/dev/` with a small
  reviewed model. The dev environment should use the smallest viable
  model and a low `maxReplicas` so GPU cost stays bounded.
- Run a dev-deployment smoke test that submits one media request
  end-to-end, asserts the rate-limit rejects a synthetic burst, asserts
  cross-tenant access fails, and (if M9 is deployed) confirms the
  `media.task.completed` notification reaches the user's feed.
- The smoke test exercises both M4's vLLM-shape deployment pattern (the
  media chart inherits from it) and the M1-adapted-from-Odysseus
  control-plane surfaces that route the request.
- Record the run in the milestone PR; failures block merge.

## Exit criteria

- At least one media service runs isolated, with reviewed model
  licensing and enforced limits.
- Per-tenant scoping for inputs and outputs is validated.
- Operator and per-tenant kill-switches are functional.
- Dev-deployment smoke test passes against a freshly-deployed dev
  cluster.

## Escalation triggers

- adoption of any individual media model (per-model license review)
- any media runtime whose default dependencies include AGPL-sensitive
  components
- any cross-tenant isolation finding
- any GPU-capacity decision that conflicts with the M6 scaling policy
