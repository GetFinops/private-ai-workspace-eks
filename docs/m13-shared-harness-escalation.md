# M13 Shared Harness — Maintainer Escalation Note

> **Status: awaiting maintainer decision. No code written. Blocks all M13
> implementation.**
>
> Read [`milestones/m13-personal-info-integrations.md`](milestones/m13-personal-info-integrations.md)
> and [`12-phase-2-feature-adoption.md`](12-phase-2-feature-adoption.md) first;
> the standing Phase 2 rules govern and are not repeated here.

## Why this is an escalation, not a PR

A *shared harness* for personal-information integrations (build-tasks #2–#6 of
the M13 milestone — credential resolver, outbound URL guard, audit, kill-switch,
per-tenant disable; **not** any concrete calendar/mail/contacts integration)
hits four `AGENTS.md` / Phase 2 escalation triggers at once:

- personal-information **and** credential-handling integration surface,
- a change affecting **tenant/user isolation**,
- **new production networking egress** from the control plane,
- the milestone's mandatory **credential-model review**.

Per `AGENTS.md` these must be signed off before implementation. Three of the
decisions below are ones only maintainers can make, and they gate everything
downstream. This note asks for those three decisions (A, B, C) and proposes the
`NOTICE` records to sign them off.

## Background discovered during scoping

Two facts materially change the milestone as written:

1. **The "M3 hardened URL-validation layer" does not exist in code.** It is named
   as an M13 prerequisite throughout the docs, but there is no SSRF / private-IP
   / metadata-blocking / outbound-URL-validation module anywhere in
   `app/control_plane/`. The only external-egress feature shipped to date
   (deep-research) pushes its network calls into the M11 sandbox/dispatcher
   rather than validating URLs in-process, so there is nothing to build on. The
   harness must **build** this layer, not consume it.

2. **The current secret path cannot satisfy M13's credential model.** Secrets
   today flow Secrets Manager → External Secrets Operator → Kubernetes Secret →
   env vars, synced as a fixed set at deploy time
   (`deploy/helm/.../externalsecret.yaml`). That is per-cluster and static. M13
   requires **per-tenant** (per-user where relevant) credentials, fetched at
   runtime, with **rotation propagating without a pod restart**. That demands a
   runtime fetch path the control plane does not have today.

Neither fact is a blocker — both have clean resolutions below — but both require
a maintainer call because they widen the control plane's posture.

---

## Decision A — Where the M3 URL-validation layer lives

**Problem.** M13 cannot route outbound calls through a layer that was never
built. The milestone and the Phase 2 doc both assume it exists.

**Recommendation.** Build it now as a new stdlib-only module
`app/control_plane/outbound.py` and **designate it the M3 hardened
URL-validation layer of record.** Scope: `https`-only scheme allow-list,
deny-by-default host allow-list, resolve-then-pin to defeat DNS rebinding, and
rejection of any address resolving into private / loopback / link-local /
reserved ranges or the cloud metadata IP `169.254.169.254`. It ships with its
own unit truth-table and an architecture test forbidding any other outbound HTTP
primitive in the integration package. Reviewed and merged on its own, ahead of
the rest of the harness.

**Open question for maintainers.**
- Confirm `outbound.py` is the intended home and is accepted as the M3 layer,
  rather than a separate "M3 backfill" milestone.
- Confirm whether existing egress (deep-research's sandbox dispatcher) should be
  retrofitted to route through it **later** (recommended, but out of M13 scope),
  or left as-is.

---

## Decision B — Runtime per-tenant Secrets Manager fetch

**Problem.** Per-tenant credentials with rotation-without-restart require runtime
resolution; the ESO → env-var path is static and per-cluster.

**Recommendation.** Add a runtime resolver module
`app/control_plane/integration_secrets.py`, shaped exactly like the existing
`app/storage/s3.py` — a thin **boto3** wrapper behind a minimal interface, with
the resolver **injected** so the unit suite stays stdlib-only (the M12
`MCPExecutor(secret_resolver=...)` seam already establishes this pattern).
Secret naming `private-ai-workspace/<env>/integrations/<tenant>/<integration>`
(append `/<user_sub>` where per-user), so a tenant's path is derived from its
**verified token** and one tenant can never name another's secret. Per-call
fetch with a short ARN+version TTL cache gives rotation without restart. IRSA
policy in `modules/irsa-app` widens by exactly one statement —
`secretsmanager:GetSecretValue` / `DescribeSecret` scoped to the
`.../integrations/*` ARN prefix (mirrors the existing vLLM HF-token policy);
**no** wildcard over all secrets.

**Why this is acceptable posture.** boto3 is already in the production image
(`app/storage/s3.py`, Apache-2.0); this adds no new dependency. The IRSA grant is
prefix-scoped, not blanket. Credentials never touch env vars or ConfigMaps. The
runtime import stays out of the stdlib-only unit surface via injection.

**Open question for maintainers.**
- Approve a runtime boto3-backed Secrets Manager fetch in the control plane
  (deviation from the ESO-only model).
- Approve the IRSA policy widening to `.../integrations/*`.
- Confirm the per-tenant (vs per-user) granularity default — recommend
  per-tenant with per-user opt-in per integration.

---

## Decision C — First integration target (deferred, but named for the record)

**Problem.** The milestone's build-task #6 credential-handling security review
cannot sign off in the abstract — it reviews a *specific* integration's
credential model. The harness, however, does not need one.

**Recommendation.** Merge the harness validated against a **synthetic loopback
fixture only** (a fake in-cluster calendar/mail server — never a real provider),
per the standing Phase 2 dev-validation rule. Treat the **first real
integration** (calendar, mail, or contacts — pick one) as a *separate* adoption
decision with its own `NOTICE` record, licensing review of its SDK, and the
build-task #6 credential review. Nothing in this harness work ships a real
third-party credential path.

**Open question for maintainers.**
- Confirm the harness may merge on the fixture alone.
- (Optional, non-blocking) name the intended first integration so the eventual
  per-integration review can be scheduled.

---

## Proposed `NOTICE` records (to sign off on approval)

> Draft language mirroring the existing M11/M12 decision records. Maintainers
> edit + sign; nothing here is committed to `NOTICE` until approved.

```
M13 shared-harness adoption decision
────────────────────────────────────────────────────────────────────────────────

M13 (docs/milestones/m13-personal-info-integrations.md) adds optional
personal-information integrations. This record covers the SHARED HARNESS only
(milestone build-tasks #2–#6); each real integration is a separate per-
integration adoption + credential review.

Decision: the M13 shared harness is ADOPTED, implemented as ORIGINAL control-
plane code. No upstream integration code is vendored (the upstream CardDAV-class
URL/credential handling flagged in 02-review-summary.md is specifically NOT
ported). Outbound calls route through a new stdlib URL-validation layer
(app/control_plane/outbound.py), designated the M3 hardened layer of record:
https-only, deny-by-default host allow-list, resolve-then-pin against DNS
rebinding, private/loopback/link-local/reserved + 169.254.169.254 blocked.
Per-tenant credentials are resolved at runtime from AWS Secrets Manager via IRSA
(app/control_plane/integration_secrets.py, boto3 behind an injected interface;
boto3 already present via app/storage/s3.py), scoped to
.../integrations/<tenant>/<integration>[/<user>] — IRSA grant prefix-scoped to
.../integrations/*, no blanket secret access. Access is deny-by-default per
tenant; an operator kill-switch (INTEGRATIONS_ENABLED) disables it cluster-wide;
a per-tenant DB switch disables it per tenant; audit is shape-only (host, method,
response class, latency, decision, reject-reason — never URL path/query, params,
or credentials).

Per-integration records: the ONLY integration shipped in this increment is a
synthetic LOOPBACK FIXTURE (fake in-cluster calendar/mail server) used solely to
dev-validate the harness — no real provider, no real credentials. Any real
integration is a separate adoption decision with its own NOTICE record, SDK
licensing review, and credential-handling security review (milestone build-task
#6).
```

## Sequencing once approved

Each a separate reviewable PR, in order:

1. `outbound.py` URL guard + unit truth-table + architecture test (Decision A).
2. `integration_secrets.py` runtime resolver + IRSA terraform statement
   (Decision B).
3. Harness wiring: config fields, allow-list, kill-switch, per-tenant DB state,
   `/v1/integrations/{list,invoke}`, shape-only audit, dedicated rate limiter.
4. Loopback fixture + dev smoke (`scripts/smoke-test.sh --integrations`) → dev
   validation → milestone PR recording the run.

## What is explicitly NOT being asked for here

- Adoption of any real calendar/contacts/mail provider.
- Any outbound path that bypasses the URL guard.
- Any per-cluster (non-per-tenant) credential model.
- Any change to the existing ESO → env-var path for the platform's own secrets.
