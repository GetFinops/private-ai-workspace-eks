# M13 Follow-ups — Planning & Guidance

The M13 milestone ([`../milestones/m13-personal-info-integrations.md`](../milestones/m13-personal-info-integrations.md))
is **not started** and is high-risk: personal-information integrations
(calendar, contacts, mail) handle third-party credentials and outbound network
egress, the exact area the upstream review flagged for weaker URL validation and
plaintext credentials. Adoption is opt-in **per integration** and gated on a
credential-handling review.

This directory holds the planning for the **shared harness** — the reusable
machinery (credential resolver, outbound URL guard, audit, kill-switch,
per-tenant disable) that every individual integration plugs into. It deliberately
stops short of adopting any real provider; the first real integration is a
separate decision with its own `NOTICE` record and credential review.

| # | Doc | Status | Covers |
| --- | --- | --- | --- |
| — | [Escalation note](../m13-shared-harness-escalation.md) | A–C signed off (`NOTICE`) | the three maintainer-only decisions that gated the build + the `NOTICE` record |
| 0 | [Build plan](00-build-plan.md) | shipped | PR-by-PR breakdown of the shared harness (PRs #41/#45/#43/#44) |
| 1 | [Integration module guide](01-integration-module-guide.md) | **standard** | how to add a new integration as a standardized module (the contract, credentials, registration, tests, NOTICE, dev validation) — read before building any provider |

## Two scoping facts that shaped these plans

1. **The "M3 hardened URL-validation layer" does not exist in code.** It is named
   as an M13 prerequisite throughout the docs, but there is no SSRF / private-IP
   / metadata-blocking module in `app/control_plane/`. The harness must **build**
   it (Decision A → `outbound.py`), not consume it.
2. **The current secret path is static and per-cluster.** Secrets flow Secrets
   Manager → External Secrets Operator → env vars at deploy time. M13 needs
   **per-tenant** credentials fetched at runtime with rotation-without-restart,
   which requires a new runtime resolver (Decision B → boto3 behind an injected
   interface; boto3 already ships via `app/storage/s3.py`).

## Sequencing

```
Decision A ──> PR1 (outbound URL guard / M3 layer)
Decision B ──> PR2 (per-tenant secret resolver + IRSA)   # PR1, PR2 parallel once signed
              PR1 + PR2 ──> PR3 (harness wiring) ──> PR4 (loopback fixture + dev smoke)
```

PR4 is the only one needing a live dev cluster, and it is GPU-independent, so it
validates without the cold vLLM plane.

## Non-negotiable invariants (apply to the harness and every integration)

Carried from `AGENTS.md`, [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md),
and the M13 milestone. Not re-litigated per integration:

1. **No outbound call bypasses the URL guard.** Every egress routes through
   `validate_outbound_url` + `guarded_open`; private/loopback/link-local/reserved
   ranges and `169.254.169.254` are blocked; deny-by-default host allow-list.
2. **Credentials are per-tenant, runtime-resolved, never plaintext.** Secrets
   come from AWS Secrets Manager via IRSA, scoped by a path derived from the
   verified token. Never env vars, never ConfigMaps. One tenant can never name
   another's secret.
3. **Deny-by-default, per-tenant, re-checked every call.** An operator
   kill-switch (`INTEGRATIONS_ENABLED`) **and** a per-tenant DB switch must both
   be on; a valid token alone grants nothing.
4. **Content policy holds.** Audit/telemetry/notifications carry shape only —
   host, method, response class, latency, decision, reject-reason — never URL
   path/query, request bodies, results, or credentials.
5. **Escalation gate.** The harness design is reviewed and signed off in `NOTICE`
   *before* implementation; each real integration additionally requires its own
   per-integration adoption decision, SDK licensing review, and credential
   review.

## How to use these docs

The escalation note is the gate: it states the A–C decisions and the draft
`NOTICE` language. The build plan is the execution breakdown that unlocks once
those are signed. They are **plans, not specs** — record sign-off in `NOTICE`
before writing code, exactly as M11/M12 did.
