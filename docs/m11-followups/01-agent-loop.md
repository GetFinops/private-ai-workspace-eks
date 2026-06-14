# M11 Follow-up 1 — LLM Agent Loop

> Status: **shipped (PR #32) and cold-validated on dev; end-to-end pending the
> vLLM/GPU plane.** Design reviewed at the escalation gate
> ([`../m11-agent-loop-design.md`](../m11-agent-loop-design.md) + `NOTICE` sign-off).
> Implemented in `app/control_plane/agent_loop.py` at `POST /v1/agent/runs`;
> 22 unit tests vs a stub inference client. Live dev check passes the
> auth-gating and clean-degradation paths (anonymous → 401, authenticated →
> 502 when inference is unreachable). The **only remaining validation** is the
> end-to-end 200 run, which needs the M4 vLLM/GPU inference plane deployed.
>
> Builds on the shipped M11 sandbox. Read
> [`../milestones/m11-agent-tool-framework.md`](../milestones/m11-agent-tool-framework.md)
> and [`README.md`](README.md) (invariants) first. The notes below are retained
> as the design rationale.

## Objective

Turn the framework from "invoke one named tool" into an **autonomous loop**: the
model is given a task plus the caller's allow-listed tool schemas, and it
iterates *plan → call tool → observe result → continue* until it produces a
final answer or hits a budget. Tool *execution* is unchanged (the existing
sandbox); what is new is letting the **model** choose which tool to call and
feeding results back in.

## What it adds over the shipped increment

Shipped today: a client names the tool and arguments; the control plane
validates, authorizes, sandboxes, and returns one result. There is no model in
that path.

This follow-up adds:

- A `POST /v1/agent/runs` surface (or extension of the invoke path) that takes a
  natural-language task, not a pre-chosen tool call.
- An orchestration loop in the control plane that calls the **M4 inference
  plane** (vLLM, OpenAI-compatible) with the allow-listed tool schemas, parses
  the model's tool-call requests, runs each through the **existing** sandbox,
  and appends results to the conversation for the next step.
- A hard **step / token / wall-clock budget** per run, and `agent.task.progress`
  notifications for long runs (shape only).

## Hard dependency

The **M4 inference plane (vLLM on GPU)** must be deployed and reachable at
`INFERENCE_BASE_URL`. The dev cluster currently has no GPU node group running
vLLM, so this follow-up cannot be validated end-to-end until inference is up.
The control plane already degrades gracefully when inference is cold
(`/v1/chat/completions` → 503); the agent loop must do the same — **no GPU ⇒ the
run is refused cleanly, never silently in-process-faked.**

## Threat-model delta

The new risk is **prompt injection driving tool selection**: task text (or, more
dangerously, a *tool result* fed back into the loop) tries to steer the model
into calling a denied tool or exfiltrating data.

Mitigations (these are the new work; the sandbox itself is unchanged):

- **Authorization is on execution, not the model's intent.** Even if the model
  emits a call to a denied/unknown tool, the existing deny-by-default check
  rejects it before any spawn. The model cannot widen its own allow-list.
- **Tool results are untrusted input.** Treat every observation fed back into
  the loop as adversarial; it never alters authorization, budgets, or the
  kill-switch.
- **Loop budgets are server-enforced**, not model-suggested — a runaway or
  goaded loop terminates deterministically.
- **Log suspected injection** (a model tool-call rejected by the allow-list) as
  an audit event with a `decision: denied` + reason class, shape only.

## Build outline

1. **Design delta + escalation sign-off** (`NOTICE`): the loop's budget model,
   how tool results re-enter the prompt, and the injection-rejection path.
2. Inference client for tool-calling: send allow-listed schemas, parse tool-call
   responses. Reuse `app/control_plane/inference.py`; do not couple to vLLM
   internals.
3. The loop: bounded iteration, each tool call routed through
   `agent_tools.build_tool_invoke_response`-equivalent authorization + sandbox.
   No new execution path — reuse the sandbox executor.
4. Budgets (steps, tokens, wall-clock) + cancellation honoring the kill-switch.
5. `agent.task.progress`/`completed`/`failed` notifications (shape only).
6. Per-tenant concurrency: an agent run holds a slot for its lifetime; respect
   the existing rate/concurrency limiter so one tenant cannot starve others.

## Testing & validation

- A scripted **prompt-injection** task that tries to invoke a denied tool is
  rejected and audit-logged (mirrors the milestone's required test).
- A tool result containing injection text does not change which tools the run is
  allowed to call.
- Budgets terminate a deliberately non-converging loop deterministically.
- Kill-switch halts an in-flight run within seconds.
- Cross-tenant: a run under tenant A can never call a tool only B is allowed, and
  never sees B's data.
- Dev validation requires vLLM deployed; until then, assert the **refused-when-
  inference-cold** path and run the loop against a stub inference server in tests.

## Non-goals

- MCP-exposed tools (M12).
- Tools needing network/FS/credentials — those wait for the
  [Job sandbox](03-job-sandbox.md); the loop with the current `text_stats`-class
  stubs is the first milestone.
- Relaxing any sandbox, budget, or authorization rule for "smarter" agents.
