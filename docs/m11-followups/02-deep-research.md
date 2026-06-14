# M11 Follow-up 2 — Deep-Research Multi-Step Agent

> Status: **planned, optional, not started.** Requires its **own adoption
> decision** recorded in `NOTICE` plus the Apache-2.0 attribution checkpoint
> below — on top of the standard escalation gate.
>
> Builds on [Follow-up 1 (agent loop)](01-agent-loop.md) and M10 retrieval. Read
> [`README.md`](README.md) (invariants) and the milestone doc's "Optional
> sub-feature: deep-research" section first.

## Objective

A specific, fixed-shape agent workflow — **plan → retrieve → synthesize** — that
answers a research question by decomposing it, pulling supporting passages from
the tenant's retrieval corpus (M10), and composing a cited synthesis. It is the
**one place in Phase 2 where actual upstream code (not just patterns) is most
likely adapted** (the upstream "deep-research" component, Apache-2.0).

## What it adds over the agent loop

Follow-up 1 is a *general* loop. Deep-research is a *constrained specialization*:

- A fixed plan→retrieve→synthesize controller rather than open-ended iteration.
- Retrieval-as-a-tool: each retrieval step is an ordinary tool call governed by
  the **same per-tenant allow-list and audit rules** — no special bypass, no
  ambient access to other tenants' corpora.
- A synthesis step that must **attribute** claims to retrieved sources and must
  not emit content into telemetry.

## Dependencies

- **Follow-up 1** (the agent loop + its injection defenses and budgets) — and
  therefore the **M4 inference plane (vLLM/GPU)**.
- **M10 retrieval** (deployed on dev). Per-tenant/per-user isolation at the
  storage layer already holds and is reused unchanged.
- **Follow-up 3 (Job sandbox)** *only if* a deep-research step needs network
  egress (e.g. web search). Default deep-research over the **internal retrieval
  corpus needs no egress** and can run in the existing subprocess sandbox.

## Apache-2.0 attribution checkpoint (blocking)

Before merging **any** adapted deep-research code, complete the milestone's
checkpoint:

1. Confirm the upstream source files are Apache-2.0.
2. Preserve the upstream `LICENSE`/`NOTICE` for adapted portions; copy required
   notices into this repo's `NOTICE`.
3. Record provenance **per adapted file** (upstream path, commit, license).
4. Retrieval-as-a-tool inside deep-research is governed by the normal
   allow-list + audit rules — no bypass.
5. The same sandbox boundary and kill-switch apply — multi-step agents get **no**
   relaxed sandbox.

If not adopted, **no M11 exit criterion depends on this** — it is purely
additive.

## Threat-model delta

- **Longer autonomous runs** ⇒ larger budgets; the step/token/wall-clock caps
  from Follow-up 1 must be sized for research depth but stay hard limits.
- **Retrieved content is untrusted** (it may contain injected instructions);
  feeding it into synthesis must not let it alter authorization or budgets — same
  "tool results are adversarial" rule as the agent loop.
- **Citation integrity:** synthesis must tie claims to source IDs without
  copying retrieved *content* into logs/notifications (shape-only telemetry).
- **Provenance risk:** adapting real upstream code (not patterns) is the novel
  licensing exposure — hence the blocking checkpoint above.

## Build outline

1. **Adoption decision + design + Apache-2.0 checkpoint** recorded in `NOTICE`.
2. Implement the plan→retrieve→synthesize controller on top of the Follow-up 1
   loop; do not fork a second execution path.
3. Wire retrieval as an allow-listed tool; reuse M10 query APIs and isolation.
4. Synthesis with source attribution; enforce shape-only telemetry.
5. Notifications: `agent.task.progress`/`completed`/`failed`, shape only.

## Testing & validation

- One synthetic end-to-end deep-research task runs on the dev cluster and the
  smoke test asserts the **same sandbox boundary and kill-switch** apply (the
  milestone's explicit dev-validation requirement for this sub-feature).
- Injection embedded in a retrieved passage cannot escalate tool access.
- Cross-tenant: a research run for tenant A retrieves only A's corpus.
- Budgets bound the run; kill-switch halts it.
- Provenance: a test/CI check that every adapted file has a `NOTICE` entry.

## Non-goals

- Open web browsing by default (needs Follow-up 3 + a host allow-list + a
  separate escalation).
- Treating deep-research as exempt from any allow-list, budget, or sandbox rule.
