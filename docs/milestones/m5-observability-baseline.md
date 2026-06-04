# M5 — Observability Baseline

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.

## Status

Scaffolded chart only. `deploy/helm/observability/` exists, but there is no
application metrics endpoint, instrumentation, or tracing. See
`docs/07-observability.md` for the full target design.

## Objective

Add enough metrics, logs, traces, dashboards, and alerts to operate the system
safely.

## Primary workstreams

- platform-infra
- ml-inference
- product-app

## Prerequisites

- M4 complete.

## In scope

- Prometheus-compatible metrics collection
- Grafana or a managed equivalent
- a GPU metrics exporter
- application and inference instrumentation
- a first dashboard set and a first alert set

## Non-goals

- autoscaling on these signals (M6)
- production-grade retention and incident tooling depth (M7)

## Build tasks

1. Expose application metrics from the control plane (a metrics endpoint and
   the golden signals described in `docs/07-observability.md`: request rate,
   error rate, latency percentiles, auth failures, dependency latency).
2. Deploy Prometheus-compatible scraping for the app and vLLM via
   `deploy/helm/observability/`.
3. Deploy a GPU metrics exporter on GPU nodes with tolerations for GPU-tainted
   scheduling.
4. Add structured logging with request and correlation IDs. Do not log prompts,
   secrets, or user content without a reviewed, redacted policy.
5. Add tracing for the request path from ingress through the control plane to
   inference.
6. Create the first dashboards (platform, inference, incident triage) and the
   first golden-signal alerts.

## Provenance and licensing checkpoints

- Review observability components and exporters for license compatibility.
- Record provenance for any adapted dashboards or alert rules in `NOTICE`.

## Security checkpoints

- Never emit prompts, secrets, tokens, or user content into metrics, logs, or
  traces without an explicit, reviewed, redacted policy.
- Restrict access to dashboards and telemetry backends.
- Use tenant-safe correlation identifiers.

## Testing and validation

- Confirm app and vLLM metrics are scraped and visible.
- Confirm GPU metrics appear from the exporter.
- Trigger alert conditions in a test setting and confirm they fire.
- Confirm a request can be followed through logs and traces.
- Capture evidence: a dashboard view and a fired test alert.

## Exit criteria

- App, cluster, and GPU dashboards exist.
- Key alerts fire under test conditions.
- The request path can be debugged via logs and traces.

## Escalation triggers

- what prompt or response metadata, if any, may appear in telemetry
- access controls for telemetry backends
