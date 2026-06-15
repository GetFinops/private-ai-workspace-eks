# M11 — Job-sandbox design (delta)

> Escalation-gate artifact for the M11 follow-up
> [`m11-followups/03-job-sandbox.md`](m11-followups/03-job-sandbox.md). Per
> `AGENTS.md`, reviewed and signed off **before** implementation. A **delta** on
> the reviewed tool sandbox ([`m11-sandbox-design.md`](m11-sandbox-design.md)): it
> adds a *second, stronger* executor for IO-capable tools and **does not relax**
> the existing subprocess sandbox (the default for pure-compute tools).
>
> **Revision (tighter privilege model).** An earlier draft gave the control-plane
> ServiceAccount `Job` create RBAC directly. Rejected: that puts cluster
> pod-creation behind the largest, internet-facing, auth/chat attack surface. This
> revision keeps the **control plane at zero Kubernetes privileges** and isolates
> Job creation in a small, non-user-facing **tool-runner dispatcher**.

## Why a second executor

The shipped subprocess sandbox is correct for pure-compute tools (`text_stats`):
a child of the control-plane pod, scrubbed env, `RLIMIT_FSIZE=0`, no network,
killed on timeout. It is the **wrong** boundary for a tool that legitimately needs
a filesystem workspace or *allow-listed* network egress — widening the subprocess
to allow those would weaken the boundary for every tool, and a subprocess shares
the control-plane pod's network namespace and node. IO-capable tools therefore get
a stronger, **per-call Kubernetes Job**: isolated pod, unprivileged identity, own
NetworkPolicy, own ephemeral disk, torn down after each call.

## Components and the privilege boundary

```
 end user ──auth──> CONTROL PLANE ──HTTP(+token, NetworkPolicy)──> TOOL-RUNNER ──create Job──> RUNNER JOB
                    (app ns)                                       DISPATCHER                  (agent-jobs ns)
                    ZERO k8s RBAC                                  (agent-jobs ns)             unprivileged SA, no creds,
                    does authz + allow-list                       scoped Job RBAC,            default-deny NetworkPolicy,
                    + kill-switch + audit                         owns the FIXED pod          read-only rootfs, ephemeral
                                                                  template, tiny surface      emptyDir, runs app.sandbox.runner
```

- **Control plane (existing, `app` ns) — zero Kubernetes privileges.** On a
  Job-backed tool call it does *not* touch the K8s API. It performs the usual
  token verification, deny-by-default allow-list re-check, kill-switch, rate limit
  and audit, then forwards `{tenant, run_id, tool, arguments}` to the dispatcher
  over **HTTP**, authenticating with a shared bearer token (Secrets Manager via
  ESO). It never sends pod spec, image, command, or SA — only the tool name and
  arguments.
- **Tool-runner dispatcher (NEW Deployment, `agent-jobs` ns) — the only privileged
  component.** A small stdlib HTTP service with **no auth/chat/user-facing
  surface**. NetworkPolicy allows ingress **only** from the `app` namespace; it
  requires the shared token. It owns the **fixed, locked-down Job template** (the
  control plane cannot influence it), re-checks the tool is a registered
  Job-executor tool, creates the Job via the in-cluster API using *its own* SA
  token, waits, reads the scrubbed result from the pod log, deletes the Job, and
  returns the result. Its blast radius is small: minimal code, no user surface,
  network-restricted, and it can only ever create Jobs from one hard-coded
  template.
- **Runner Job (`agent-jobs` ns) — unprivileged.** Runs `python3 -m
  app.sandbox.runner` from the **existing control-plane image** (no new image).
  Dedicated SA with **no IRSA** and `automountServiceAccountToken: false`;
  `runAsNonRoot`, `readOnlyRootFilesystem`, drop ALL caps, `seccompProfile:
  RuntimeDefault`; a single scoped `emptyDir`; cpu/mem limits;
  `activeDeadlineSeconds`; `backoffLimit: 0`; `ttlSecondsAfterFinished`;
  `nodeSelector` to CPU nodes. Default-deny NetworkPolicy (IMDS / egress blocked);
  optional per-tool egress allow-list.

**What the tighter model buys:** a compromised control plane can ask the
dispatcher only to run **already-authorized, allow-listed Job tools** through a
**fixed template** — it cannot create arbitrary pods, set pod specs, mount host
paths, attach IRSA, or reach the K8s API at all. The component that *can* create
pods (the dispatcher) has a tiny, non-internet-facing attack surface.

## Threat model delta

| Risk | Mitigation |
| --- | --- |
| Control-plane compromise → arbitrary pods | Control plane has **no K8s RBAC**. It can only call the dispatcher, which only runs registered Job-tools from a fixed template. |
| Dispatcher compromise | Minimal code, no user/auth surface, ingress restricted to `app` ns + shared token. Scoped to `create/get/list/delete jobs` + `pods/log` in `agent-jobs` only. |
| Ambient cloud credentials in the tool | Runner SA has **no IRSA**, no mounted API token; IMDS unreachable (default-deny egress). |
| Unrestricted egress | NetworkPolicy default-deny; first Job tool ships **fully network-isolated**; real egress is per-tool allow-listed + its own escalation. |
| Host FS / shared state | read-only rootfs + scoped `emptyDir`, destroyed with the Job. |
| Runaway / hang | `activeDeadlineSeconds`, cpu/mem limits, `backoffLimit:0`; timeout/OOM/CPU-exhaust → `tool_timeout`/`tool_error`. |
| Cross-tenant | Per-tenant/run Job labels + names; unprivileged SA + default-deny networking; no shared volume/secret. |
| Leaked Jobs | `ttlSecondsAfterFinished` + explicit delete; kill-switch reaps in-flight Jobs. |
| Spoofed dispatcher calls | Shared bearer token (ESO) + NetworkPolicy ingress restricted to `app` ns; the dispatcher trusts only the control plane. |

## Authorization & audit (unchanged)
Deny-by-default per-tenant allow-list re-checked every call **in the control
plane**, operator kill-switch (`AGENT_TOOLS_ENABLED`), rate/concurrency limits,
and shape-only audit — all reused verbatim. The executor is chosen **per tool** by
its descriptor (`TOOLS[name]["executor"] == "job"`); subprocess stays the default.
The agent loop and the invoke endpoint both route through the same authorization;
only the backend differs. The dispatcher re-validates (defense in depth) that the
tool is a registered Job-tool before creating anything.

## First Job-backed tool (validation target)
To validate the *mechanics* without a risky IO tool, the first Job-backed tool is
a pure-compute demonstrator flagged `executor: job`. Dev validation asserts the
isolation guarantees — no creds, NetworkPolicy denies egress, IMDS unreachable,
ephemeral FS, timeout/OOM mapping, no leaked Jobs — **on CPU nodes, no GPU, no real
egress**. Real IO tools (web-fetch, etc.) come later, each behind its own
allow-list + egress allow-list + its own escalation.

## Decision required (maintainer)
This still grants **Job-create RBAC**, but to the small isolated dispatcher, not
the control plane. Sign-off covers: (a) the new `agent-jobs` namespace + dispatcher
Deployment, (b) the dispatcher SA's scoped Role (`jobs`, `pods/log` in
`agent-jobs`), (c) the unprivileged runner SA, (d) the shared control-plane↔
dispatcher token, (e) the control plane retaining **zero** K8s RBAC. Recorded in
`NOTICE` like the prior M11 gates before code lands.

## Testing & validation (the bar)
- Control plane has **no** K8s API access (it only reaches the dispatcher).
- A Job-backed tool **cannot reach a non-allow-listed host** and **cannot read
  cloud creds** (no SA token; IMDS unreachable).
- Timeout / OOM / CPU-exhaust terminate the Job → correct result class.
- Cross-tenant: A's Job cannot access B's volume/network/secrets.
- Kill-switch reaps an in-flight Job within seconds; no leaked Jobs on
  success/failure/cancel.
- Dispatcher rejects unauthenticated / wrong-namespace callers.
- **Live dev validation needs no GPU** — Jobs run on CPU nodes.

## Non-goals / unchanged red lines
Per-call ephemeral Jobs only (no daemons). Arbitrary shell / unrestricted egress
stay excluded by default. The subprocess sandbox remains the default for
pure-compute tools; the Job executor is opt-in per IO-capable tool. The sandbox is
**not** relaxed; more capability ⇒ stronger isolation, never weaker.
