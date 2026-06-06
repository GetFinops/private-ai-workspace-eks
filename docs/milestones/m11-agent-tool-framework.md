# M11 — Agent and Tool Framework (Sandboxed)

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.
>
> This is a Phase 2 milestone. The full Phase 2 governance — adoption gating,
> licensing rules, isolation requirements, and excluded-by-default
> components — is in [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md).
> Read that document before opening any M11 work.

> **High-risk milestone.** Agent and tool execution introduces a code-execution
> attack surface. Per `AGENTS.md`, any sandboxing design is an escalation
> trigger requiring maintainer review before implementation begins.

## Status

Not started. Scaffolded as part of the Phase 2 kickoff. Requires explicit
maintainer adoption (see the Decision Checklist in the Phase 2 doc) and
explicit sandbox-design review before implementation begins.

## Objective

A controlled agent and tool execution capability that runs within an
isolated, allow-listed, per-tenant sandbox with auditable execution.

## Primary workstreams

- product-app
- governance-security

## Prerequisites

- M4 inference (the agent loop calls the inference plane).
- M3 hardened validation, secret-handling, and rate-limiting layer.
- M10 retrieval if any tools call retrieval (optional).

## In scope

- a tool-schema, tool-parsing, and tool-result-handling layer reused as
  *patterns* from upstream (not vendored wholesale)
- per-tenant execution scoping that the baseline does not yet provide
- a sandbox boundary for tool execution that does not allow ambient cloud
  credentials, host filesystem access, or arbitrary network egress
- an explicit tool allow-list per tenant or per role
- prompt-injection defenses on tool selection
- audit logging of every tool call (caller, tool, arguments scrubbed of
  content, result class, latency) reusing the M5 logging infrastructure

## Non-goals

- arbitrary shell or command execution in multi-tenant hosting (excluded
  by default per `12-phase-2-feature-adoption.md`)
- MCP-exposed tools (M12)
- personal-information integrations (M13)

## In-scope optional sub-feature: "deep-research"-style multi-step agent

The upstream-Odysseus surface includes a "deep-research" component documented
in the Phase 2 licensing analysis as Apache-2.0. Functionally it is a
multi-step agent workflow (planning → retrieval → synthesis), so it is a
natural fit for this milestone rather than its own.

If adopted, treat it as an optional sub-feature subject to a separate adoption
decision recorded in `NOTICE`, and apply the additional rules below.

## Build tasks

1. **Sandbox design review (escalation gate).** Before any code is
   written, produce a sandbox-design doc for maintainer review that
   covers: process boundary, network egress policy, credential
   visibility, filesystem visibility, CPU/memory limits, timeout
   behavior, and crash containment. Implementation starts only after
   maintainer sign-off, recorded in `NOTICE`.
2. Choose the tool-schema and tool-parsing patterns to adapt from
   upstream. Adapt patterns, do not vendor large subsystems.
3. Implement the per-tenant allow-list. The default deny-list includes
   shell, arbitrary HTTP, file I/O outside a scoped temp area, and any
   AWS SDK call. Tools must be explicitly opt-in per tenant.
4. Implement the tool-execution sandbox per the design doc. The first
   implementation should run tools as a separate process, container, or
   Kubernetes Job — never in-process with the control plane.
5. Implement prompt-injection defenses at tool selection: reject tool
   choices that conflict with the active tenant's allow-list; log
   suspected injection attempts.
6. Wire audit logging of every tool call (caller, tool name, sanitised
   argument shape, result class, latency, sandbox exit code). Respect
   the M5 content policy — no prompt/completion content in the audit
   log.
7. Implement per-tenant rate and concurrency limits on tool execution.
8. Implement an operator kill-switch (environment variable or feature
   flag) that disables tool execution cluster-wide.

## Provenance and licensing checkpoints

- Review tool dependencies for license compatibility.
- Reject tools whose default build pulls in AGPL-sensitive components.
- Record provenance in `NOTICE` for every adapted tool-framework pattern.
- **Apache-2.0 attribution checkpoint for the "deep-research" sub-feature.**
  If adopted, the upstream "deep-research" component carries Apache-2.0
  attribution and `NOTICE` obligations per the licensing analysis in
  `12-phase-2-feature-adoption.md`. Before merging any deep-research code:
  1. Confirm the upstream source file licenses are Apache-2.0.
  2. Preserve the upstream `LICENSE` and `NOTICE` content for the adapted
     portions; copy required notices into this repository's `NOTICE`.
  3. Record provenance per adapted file (upstream path, commit, license).
  4. Treat retrieval-as-a-tool inside deep-research as a tool call governed
     by the same per-tenant allow-list and audit-logging rules as any other
     tool, with no special bypass.
  5. Apply the same sandbox boundary and kill-switch to deep-research as to
     any other tool — multi-step agents do not get a relaxed sandbox.

## Security checkpoints

- Strict tool allow-listing per tenant — deny by default.
- Path confinement: tools see only a scoped temp area, never host paths.
- No ambient cloud credentials reachable from tools (no IRSA chaining,
  no host metadata service).
- Per-tenant authorization re-checked on every tool call, not only at
  session start.
- Prompt-injection defenses at tool selection and at tool result
  processing.
- Tool execution must be killable via the operator kill-switch.

## Testing and validation

- Tools run within an isolated, allow-listed, per-tenant sandbox.
- A scripted prompt-injection attempt that would invoke a denied tool is
  rejected and audit-logged.
- The kill-switch disables tool execution within seconds.
- Cross-tenant tool-result leakage is impossible by design (validated
  with scripted tests using different tenant tokens).
- Sandbox exit on timeout, OOM, and CPU-exhausted scenarios is verified.

## Exit criteria

- Tools run within an isolated, allow-listed, per-tenant sandbox with
  auditable execution.
- Sandbox design has been reviewed and signed off.
- Operator kill-switch is functional.

## Escalation triggers

- the sandbox design itself (mandatory pre-implementation review)
- any tool that requires network egress beyond an allow-list of hosts
- any tool that requires cloud-credential access
- any cross-tenant isolation finding
- any tool framework whose default build cannot meet the sandbox
  requirements
- adopting the "deep-research" sub-feature (requires a separate decision
  record in `NOTICE` plus the Apache-2.0 attribution checkpoint above)
