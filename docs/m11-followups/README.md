# M11 Follow-ups — Planning & Guidance

The M11 first increment ([`../milestones/m11-agent-tool-framework.md`](../milestones/m11-agent-tool-framework.md))
shipped the **security foundation**: an out-of-process, deny-by-default,
kill-switched, audited tool sandbox with a single inert `text_stats` stub tool.
It deliberately stopped short of the capabilities that make agents *useful* but
also widen the attack surface.

This directory holds the guidance for the three deferred extensions. Each is an
independent, separately-adoptable workstream that **builds on** the shipped
sandbox — none of them relax it.

| # | Follow-up | Status | Adds | Hard dependency | Primary new risk |
| --- | --- | --- | --- | --- | --- |
| 1 | [Agent loop](01-agent-loop.md) | **shipped; e2e pending GPU** | LLM-driven plan→act→observe loop that selects and calls tools | M4 inference plane (vLLM on GPU) | prompt-injection driving tool selection |
| 2 | [Deep-research](02-deep-research.md) | **shipped; e2e pending GPU** | Multi-step research agent (plan → retrieve → synthesize) | #1 + M10 retrieval | Apache-2.0 provenance; longer autonomous runs |
| 3 | [Job sandbox](03-job-sandbox.md) | **shipped (pure-compute); egress gated on #36** | Per-call Kubernetes-Job isolation for IO-capable tools | EKS + per-tenant RBAC | tools that need scoped network/FS at all |

## Sequencing

```
shipped: subprocess sandbox + stub tool  (M11 increment 1)
                 │
                 ├──► (1) agent loop ───────┐
                 │        needs vLLM/GPU     ├──► (2) deep-research
                 │                           │        needs retrieval (M10 ✓)
                 └──► (3) job sandbox ───────┘
                          needed before any tool wants network/FS/credentials
```

- **(1) and (3) are independent** and can proceed in parallel. (1) makes
  *tool selection* autonomous; (3) makes *tool execution* able to do scoped IO.
- **(2) depends on (1)** (it is an agent loop with a fixed plan→retrieve→
  synthesize shape) and on retrieval (M10, already deployed). If any
  deep-research tool needs network egress, it also depends on (3).
- The current `text_stats` stub needs none of these; it proves the boundary.

## Non-negotiable invariants (apply to all three)

Carried from `AGENTS.md`, `docs/12-phase-2-feature-adoption.md`, and the M11
milestone. Every follow-up must preserve them — they are not re-litigated per
feature:

1. **The sandbox is not relaxed.** Multi-step agents and IO tools get the *same*
   boundary as the stub: no ambient cloud credentials, no host FS, no
   unrestricted egress. More capability ⇒ a *stronger* sandbox (a Job), never a
   weaker subprocess.
2. **Deny-by-default, per-tenant, re-checked every call.** A more autonomous
   loop does not get a standing grant; each tool call is authorized afresh
   against the caller's allow-list.
3. **Kill-switch covers everything.** `AGENT_TOOLS_ENABLED=false` must halt the
   agent loop, deep-research, and Job-backed tools — no feature gets a bypass.
4. **Content policy holds.** Audit/telemetry/notifications carry shape only,
   never prompts, completions, tool arguments, results, or retrieved content.
5. **Escalation gate.** Like the original sandbox design, each follow-up's
   design is reviewed and signed off in `NOTICE` *before* implementation.
   Adopting deep-research additionally requires its Apache-2.0 attribution
   checkpoint.

## How to use these docs

Each plan states: objective, what it adds over the shipped increment, the
threat model delta, a build outline, the test/validation bar (including the
adversarial cases), and explicit non-goals. They are **plans, not specs** — open
the corresponding escalation review and record sign-off in `NOTICE` before
writing code, exactly as M11 increment 1 did.
