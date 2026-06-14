# M11 — Tool-execution sandbox design

> Escalation-gate artifact for [`milestones/m11-agent-tool-framework.md`](milestones/m11-agent-tool-framework.md)
> build task 1. Per `AGENTS.md`, the sandbox design must be reviewed and signed
> off before implementation. The maintainer sign-off for the first increment is
> recorded in `NOTICE` under "M11 sandbox-design sign-off".

## Scope of this design

This covers the **first increment**: declarative, allow-listed tools executed
**out-of-process** with no credentials, tight resource/time limits, and full
audit logging. It deliberately does **not** cover arbitrary shell/command
execution or tools needing network egress or filesystem I/O — those remain
excluded by default (`docs/12-phase-2-feature-adoption.md`) and require a
separate, stronger sandbox (Kubernetes Job + NetworkPolicy deny-egress +
read-only rootfs) and its own review.

## Threat model

Hosted, multi-tenant. A tool invocation may be driven by attacker-influenced
input (prompt injection selecting a tool, or hostile tool arguments). The
sandbox must ensure a tool cannot: reach AWS credentials or the instance
metadata service, read the control-plane process memory or pod filesystem,
make network calls, write to disk, exhaust CPU/memory, or hang the request
path.

## Design

### 1. Process boundary
- Tools run in a **separate OS process** (`python3 -m app.control_plane.sandbox_runner`),
  never in-process with the control plane. The runner imports only the
  pure-function tool registry — it does **not** import the server, DB, config,
  or AWS SDK.
- The control plane communicates with the runner over **stdin/stdout only**
  (JSON request in, JSON result out). No shared sockets, no shared memory.
- One process per invocation (no reuse), so state cannot leak between calls or
  tenants.

### 2. Credential visibility
- The child is launched with a **scrubbed environment** (`env={}` plus only a
  minimal `PATH`/`PYTHONPATH`). No `AWS_*`, `DATABASE_URL`, OIDC, or HF
  secrets are inherited.
- The runner does not import boto3 or the AWS SDK and makes no STS/IMDS calls.
  IRSA chaining is therefore impossible from a tool.

### 3. Filesystem visibility
- The child runs with `cwd` set to a per-invocation temporary directory.
- `RLIMIT_FSIZE = 0` — the process cannot create or grow any file, so even the
  scoped temp area is read-bounded for the first increment. (Tools that need
  scratch space are a later, separately-reviewed change.)
- First-increment tools are pure functions over their JSON arguments and touch
  no files at all.

### 4. Network egress
- First-increment tools perform no network I/O. The runner imports no HTTP
  client. (Egress-allow-listed tools are a future change gated behind the
  Kubernetes-Job sandbox + NetworkPolicy, per the milestone escalation
  triggers.)

### 5. CPU / memory limits
- Enforced in the child via `resource.setrlimit` in a `preexec_fn`:
  - `RLIMIT_CPU` — hard CPU-seconds cap (SIGKILL on exceed).
  - `RLIMIT_AS` — virtual-memory cap (allocation fails / process dies on exceed).
  - `RLIMIT_NOFILE` — small open-file cap.
  - `RLIMIT_FSIZE = 0` — no file writes (see above).

### 6. Timeout behaviour
- The parent enforces a wall-clock timeout on the child. On timeout the parent
  **kills the process group** (`SIGKILL`) and returns a structured
  `tool_timeout` error. The CPU rlimit is a defence-in-depth backstop for
  busy-loops that the wall clock alone might let run hot briefly.

### 7. Crash containment
- A child crash, non-zero exit, OOM, or malformed output never crashes the
  control plane: the parent captures the exit code + stderr, maps it to a
  `tool_error` result class, audit-logs it, and returns a 5xx degraded
  response. The request path stays alive.

### 8. Authorization & allow-listing (control-plane side, before spawn)
- **Deny by default.** A tool runs only if it is in the caller's tenant
  allow-list. The allow-list is operator config (`AGENT_TOOLS_ALLOWLIST`), keyed
  by tenant; an unknown tenant or un-listed tool is rejected `403` and
  audit-logged as a denied/possible-injection attempt **before** any process is
  spawned.
- The tenant is derived from the verified token, never from the request body.
- Authorization is re-checked on **every** invocation, not cached per session.

### 9. Kill-switch
- `AGENT_TOOLS_ENABLED` (default **false**) gates the whole subsystem. When
  false, the invoke endpoint returns `503` and spawns nothing — an operator
  cluster-wide off switch.

### 10. Rate & concurrency limits
- Per-tenant token-bucket rate limit and a global concurrency cap on live
  sandbox processes, enforced control-plane-side before spawn. Excess calls get
  `429`.

### 11. Audit logging (M5 content policy)
- Every invocation (allowed or denied) logs: tenant, user, tool name, **sanitised
  argument shape** (key names + value types/sizes only — never values), result
  class, latency, and sandbox exit code. No prompt/argument/result **content**
  ever enters logs or metrics.

## Explicitly out of scope (excluded by default; future, separately-reviewed)
- Arbitrary shell/command execution.
- Tools requiring network egress (→ Kubernetes Job + NetworkPolicy).
- Tools requiring filesystem writes or host paths.
- Any tool reaching cloud credentials.
- The LLM-driven agent loop that *selects* tools (needs the M4 inference plane;
  the first increment exposes a direct, authorized tool-invoke API and the
  selection-time injection defence is the allow-list rejection).
- The "deep-research" sub-feature (separate adoption + Apache-2.0 checkpoint).

## Validation (see tests + dev smoke)
- Denied tool / unknown tenant → `403`, audit-logged, no spawn.
- Kill-switch off → `503`, no spawn.
- Timeout and CPU/memory exhaustion → process killed, `tool_timeout`/`tool_error`,
  control plane survives.
- No tenant can invoke a tool not in its allow-list (cross-tenant isolation).
- A tool cannot read `AWS_*`/`DATABASE_URL` from its environment (asserted).
