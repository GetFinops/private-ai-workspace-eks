# M11 — Agent-loop design (delta)

> Escalation-gate artifact for the M11 follow-up
> [`m11-followups/01-agent-loop.md`](m11-followups/01-agent-loop.md). Per
> `AGENTS.md`, agent/tool execution changes are reviewed and signed off before
> implementation. The maintainer sign-off is recorded in `NOTICE` under
> "M11 agent-loop design sign-off". This is a **delta** on the reviewed
> tool-execution sandbox ([`m11-sandbox-design.md`](m11-sandbox-design.md)) — it
> changes *who chooses the tool*, not *how a tool runs*.

## Scope

Adds an LLM-driven loop that, given a task and the caller's allow-listed tools,
iterates **plan → call tool → observe → continue** until it produces a final
answer or hits a budget. **Tool execution is unchanged**: every call still goes
through the same out-of-process, scrubbed, rlimited `SandboxExecutor` and the
same deny-by-default per-tenant allow-list. The new surface is `POST
/v1/agent/runs`.

Out of scope (unchanged exclusions): arbitrary shell, network-egress / FS-write
/ credential tools (still need the Job sandbox, follow-up 3), MCP tools (M12),
and the deep-research sub-feature (follow-up 2).

## What is new vs. the shipped increment

The shipped increment takes a *client-named* tool call and runs it. The loop
lets the **model** name the tool and feeds results back. The only new trust
boundary is **the model's output and the tool observations are untrusted
input** that must not be able to widen authorization or budgets.

## Threat model delta

Primary new risk: **prompt injection driving tool selection** — task text, or a
tool result fed back into the loop, tries to make the model call a denied/unknown
tool or run forever.

| Risk | Mitigation (this design) |
| --- | --- |
| Model emits a call to a denied/unknown tool | Authorization is on **execution**, re-checked every call against the caller's allow-list. A denied/unknown request is rejected (audited `decision=denied`), never spawned; the model cannot widen its own allow-list. |
| Injection inside a tool **result** steers later steps | Observations are treated as adversarial. They enter only the model prompt — never authorization, budgets, or the kill-switch. |
| Runaway / goaded loop | Server-enforced budgets: max steps, max wall-clock, max cumulative tokens. Budgets are **not** client- or model-settable; client-supplied budgets are ignored. |
| Resource starvation across tenants | A run holds exactly **one** concurrency slot (acquired at run start via the existing `RateLimiter`, released at run end) and counts once against the per-tenant per-minute limit. Tool calls inside the run do **not** re-enter the limiter (avoids self-deadlock at low concurrency). |
| Content leakage | Tool results live only in the model context. Audit/telemetry/notifications carry **shape only** — never task text, model output, tool arguments, or results. |
| GPU cold / inference down | The run is **refused cleanly** (503) before any work; the loop is never faked in-process. Mid-run inference failure fails the run (502/504), it does not silently complete. |

## Design

### Model ↔ loop protocol (model-agnostic)
Rather than depend on a specific model's native tool-calling chat template, the
loop uses a small, explicit JSON protocol. The system prompt instructs the model
to reply with exactly one JSON object per turn:

- `{"action": "call_tool", "tool": "<name>", "arguments": {...}}`
- `{"action": "final", "answer": "<text>"}`

The parser treats model output as untrusted: it extracts the first JSON object,
tolerates surrounding prose, and on unparseable/oversized output appends a
"respond with the required JSON" nudge and continues (bounded by the step
budget). This keeps the first increment portable across served models; a native
`tools`/`tool_calls` path can replace the protocol later without changing the
authorization or sandbox.

### Control flow
1. Verify token → tenant/user (from the token, never the body).
2. **Kill-switch:** if `AGENT_TOOLS_ENABLED` is off → 503, nothing runs. (The
   loop shares the tool kill-switch; disabling tools disables the loop.)
3. **Inference configured?** If not → 503 `agent_runs_unavailable` (cold refuse).
4. Parse body `{ "task": "<text>" }`; validate size. Client budget fields are
   ignored.
5. Acquire one rate/concurrency slot for the **run** (429 on exhaustion).
6. Loop up to `max_steps`, while within `wall_clock` and `max_tokens`:
   - Call inference with the system prompt + allow-listed tool schemas +
     transcript, `max_tokens` per call.
   - Parse the action. `final` → completed. `call_tool` →
     authorize (`is_allowed` + known tool); denied → audit + feed a rejection
     observation + continue; allowed → run via `SandboxExecutor`, audit
     (`decision=allowed`, result_class, latency, exit code), feed the result (or
     error) as the next observation.
   - On inference error/timeout → fail the run.
7. Release the slot. Emit a single terminal notification
   (`agent_task_completed` / `agent_task_failed`); emit one
   `agent_task_progress` at run start. Events carry only class + run id +
   timestamps.

### Budgets (defaults, server-enforced)
- `AGENT_LOOP_MAX_STEPS` = 6
- `AGENT_LOOP_WALL_CLOCK_SECONDS` = 60
- `AGENT_LOOP_MAX_TOKENS` = 512 (per inference call; cumulative cap = steps × this)
- `AGENT_LOOP_MODEL` = served model name to target

### Authorization & audit
Unchanged from the sandbox design and reused verbatim: deny-by-default
per-tenant allow-list, per-call re-check, and `_audit(...)` (shape-only). A
model-selected denied tool is logged as `decision=denied` — the same record a
direct denied invoke produces — which is also the prompt-injection signal.

## Testing & validation (the bar this must meet)
- A scripted **injection** task whose stub model insists on a denied tool is
  rejected every step, audited `decision=denied`, and never spawns it.
- An injected instruction inside a tool **result** does not change which tools
  the run may call.
- Step / wall-clock / token budgets each terminate a deliberately
  non-converging run deterministically.
- Kill-switch off → run refused (503); inference cold → run refused (503).
- Cross-tenant: a run under tenant A can only call tools A is allowed.
- Full coverage runs against a **stub inference client** (no GPU). Live
  end-to-end validation needs vLLM on a (Karpenter-provisioned, scale-to-zero)
  GPU node and is run in a short window before merge of the live milestone.

## Non-goals / unchanged red lines
The sandbox is **not** relaxed for the loop or for multi-step agents. No ambient
cloud credentials, no host FS, no egress. More autonomy ⇒ stricter budgets,
never a weaker boundary.
