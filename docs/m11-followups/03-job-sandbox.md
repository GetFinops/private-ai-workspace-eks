# M11 Follow-up 3 — Kubernetes-Job Sandbox for IO-Capable Tools

> Status: **planned, not started.** Escalation gate applies — design + sign-off
> in `NOTICE` before implementation. Any tool needing network egress or
> credential access is itself an escalation trigger (milestone doc).
>
> Builds on the shipped M11 sandbox. Read [`README.md`](README.md) (invariants)
> and the milestone doc first.

## Objective

A **stronger** execution boundary for tools that legitimately need scoped IO
(filesystem workspace, *allow-listed* network egress) which the in-process-host
subprocess sandbox cannot safely grant. Each such tool call runs as a
short-lived **Kubernetes Job** with its own service account, NetworkPolicy,
resource limits, and scoped ephemeral volume — then is torn down.

## What it adds over the shipped increment

Shipped today: tools run as a **subprocess** of the control-plane pod with a
scrubbed env and `RLIMIT_FSIZE=0` (no writes), no network, killed on timeout.
That is correct for pure-compute stubs like `text_stats`, but it is the *wrong*
tool for anything needing files or network: a subprocess shares the pod's
network namespace and node, and widening its capabilities would weaken the
boundary for *every* tool.

This follow-up adds a **second executor** selected per tool:

- Pure-compute tools → existing subprocess sandbox (unchanged, the default).
- IO-capable tools → **Job executor**: render a Job with a locked-down pod spec,
  submit it, stream the scrubbed result, delete it. The control plane never runs
  the tool itself.

## Hard dependency

EKS (present) plus **per-tenant/per-tool RBAC and network policy**. The Job's
service account must have **no IRSA role** (no ambient cloud credentials), and a
default-deny NetworkPolicy with egress opened only to an explicit host
allow-list per tool. The node-level metadata service must be unreachable from
the Job pod (no `169.254.169.254`).

## Threat-model delta

The whole point is that these tools *can* touch IO, so the boundary does more
work:

- **No ambient cloud credentials.** Job SA is unprivileged; no IRSA chaining; IMDS
  blocked. (Same red line as the subprocess sandbox, enforced at the pod level.)
- **Egress is deny-by-default + per-tool host allow-list**, enforced by
  NetworkPolicy — not by trusting the tool. Any host beyond the allow-list is an
  escalation trigger.
- **Filesystem is a scoped ephemeral volume**, destroyed with the Job; never a
  host path or a shared PVC.
- **Resource + time bounds** via Job `activeDeadlineSeconds`, pod resource
  limits, and `backoffLimit: 0` (no silent retries). OOM/CPU-exhaust/timeout all
  terminate the Job; the control plane maps them to `tool_timeout`/`tool_error`
  exactly as the subprocess sandbox does.
- **Cross-tenant:** Job namespace/labels/SA are tenant-scoped; one tenant's Job
  cannot see another's volume, secrets, or network.
- **Kill-switch** must delete in-flight Jobs, not just stop scheduling new ones.

## Build outline

1. **Design + escalation sign-off** (`NOTICE`): the locked-down Job pod spec,
   the NetworkPolicy model, the no-credentials guarantee, and teardown.
2. Add an `Executor` abstraction so `agent_tools` selects subprocess vs Job by
   tool descriptor; the subprocess path stays the default and unchanged.
3. Job templating: unprivileged SA, default-deny NetworkPolicy + per-tool egress
   allow-list, `activeDeadlineSeconds`, resource limits, `backoffLimit: 0`,
   read-only root FS + one scoped `emptyDir` workspace, IMDS blocked.
4. Result plumbing: read the scrubbed JSON result, enforce size caps, delete the
   Job (and on failure, ensure teardown — no leaked Jobs).
5. Authorization unchanged: deny-by-default allow-list, per-call re-check, audit
   (shape only, plus the chosen egress allow-list class), rate/concurrency caps.
6. Kill-switch wiring that also reaps running Jobs.

## Testing & validation

- A Job-backed tool **cannot reach a non-allow-listed host** (NetworkPolicy
  denies; test asserts failure) and **cannot read cloud credentials** (no SA
  token grants AWS; IMDS unreachable).
- Timeout / OOM / CPU-exhaust each terminate the Job and map to the right
  result class (the milestone's required sandbox-exit tests, at the Job level).
- Cross-tenant: tenant A's Job cannot access B's volume/network/secrets.
- Kill-switch deletes an in-flight Job within seconds.
- No leaked Jobs after success, failure, or cancellation.

## Non-goals

- Long-running/daemon tools — these are *per-call* ephemeral Jobs.
- Arbitrary shell or unrestricted egress (still excluded by default per
  `12-phase-2-feature-adoption.md`).
- Replacing the subprocess sandbox — it remains the default for pure-compute
  tools; the Job executor is opt-in per IO-capable tool.
