# Phase 2 — Feature Adoption Track (M9+)

## Status

**Committed track. Each individual feature still requires explicit maintainer
adoption before it enters a milestone.**

The delivery roadmap (`docs/10-delivery-roadmap.md`, milestones M0–M6, then
M7a) defines a *platform baseline*: a secure, multi-user control plane with
externalized state, isolated vLLM inference, observability, and elastic GPU
scaling on EKS, plus a minimum operational-hygiene pass (M7a) before features
land on top of it. That baseline is intentionally feature-light.

This document describes the product-feature track layered on top of the
M7a-validated baseline. The public production release (M8) and the full
staging-hardening pass (M7b) occur **at the end of Phase 2**, exercising the
combined platform + adopted-feature surface.

Each individual feature in Phase 2 requires an explicit maintainer decision
plus a licensing and security review before it enters a milestone. See the
decision checklist at the end.

## Public Disclosure Scope

This is public documentation. It analyzes licensing and architectural risk at a
category level. It intentionally does not publish exploit details, specific
vulnerability write-ups, or a file-by-file porting list. Detailed provenance and
security-sensitive porting decisions stay in maintainer review and `NOTICE`, per
`docs/04-governance-and-contribution.md`.

## Relationship to the Roadmap

```text
M0 ── M6        Platform baseline (committed)
   └── M7a      Minimum operational-hygiene pass on the M6 surface (committed)
        └── M9+     Phase 2 product features (this document, individually adoption-gated)
             └── M7b   Full staging hardening across platform + adopted features
                  └── M8   Public production release
```

- M0–M6 plus M7a must be complete and stable before Phase 2 begins.
- Phase 2 features are additive and individually optional.
- Adopting a Phase 2 feature must not regress the baseline's security,
  isolation, or externalized-state guarantees.
- The public production release (M8) is gated on M7b, which exercises the
  full topology that by then includes any adopted Phase 2 surfaces. A
  release that includes a Phase 2 feature requires that feature to have
  passed M7b's expanded security and isolation review.

This sequencing was confirmed in the maintainer decision recorded as the
"Phase 2 kickoff and M7 split" decision in `NOTICE`.

## Adoption Principles

These extend the standing rules in `docs/milestones/README.md`.

1. **Selective adaptation continues.** Reuse narrow patterns; do not fork or
   vendor large subsystems wholesale.
2. **Baseline-first.** A feature may only depend on services that already exist
   in the M8 baseline or are added by an earlier Phase 2 milestone.
3. **No feature sprawl without a decision.** Each feature is gated by a
   maintainer decision recorded against this document.
4. **Externalized state always.** No feature may reintroduce local-first
   assumptions (embedded database, local vector store, local bind-mounted
   state).
5. **Isolation is non-negotiable.** No feature may weaken tenant or user
   isolation in a hosted, multi-user deployment.
6. **Provenance every time.** Adapted code is recorded in `NOTICE` with source
   and license.

## Critical Background

Three structural facts shape every decision below.

### 1. Local-first to cloud-native gap

The upstream project is oriented toward single-host, self-hosted operation. It
assumes an embedded database, a local vector store, and a local filesystem in
several subsystems. The review in `docs/02-review-summary.md` identified this as
the largest blocker to cloud-native scaling. Any feature that carries those
assumptions must be re-architected on externalized services (managed PostgreSQL,
managed or in-cluster vector storage, object storage) before adoption — the same
discipline M3 applies to the core.

### 2. Single-user to multi-tenant threat-model shift

The upstream product is primarily single-user and self-hosted. A hosted,
multi-user deployment changes the threat model fundamentally:

- every feature must enforce per-user and per-tenant authorization, not just
  authentication
- data, sessions, retrieval indexes, memories, and uploaded artifacts must be
  isolated per tenant
- any feature that executes code, fetches URLs, or calls external services
  becomes a cross-tenant attack surface

Features that were safe in a single-user desktop context are not automatically
safe when hosted for many organizations.

### 3. Expanded attack surface from agentic and integration features

Agentic and integration features add prompt-injection, server-side request,
and code-execution surfaces. These must sit behind the hardened validation,
secret-handling, and rate-limiting layer introduced in M3, and several require
sandboxing that the baseline does not yet provide.

High-risk subsystems flagged for special scrutiny:

- **arbitrary command/shell execution** — remote code execution risk; unsafe
  for multi-tenant hosting without strong sandboxing and is excluded by default
- **agent tool execution** — confused-deputy and injection risk; needs strict
  tool allow-listing, path confinement, and per-tenant scoping
- **personal-information integrations** (calendar, contacts, mail) — the
  upstream review specifically flagged weaker URL validation and plaintext
  credential handling on one integration path; all such integrations must route
  through the hardened secret and URL-validation layer
- **retrieval and memory** — cross-tenant data leakage risk if indexes are not
  isolated
- **media and model features** — supply-chain and per-model licensing risk

## Licensing Analysis

### Top-level position

The upstream project is MIT-licensed, which permits copy, modification, and
redistribution provided notices are preserved. This project stays permissive
(MIT) and records adapted code in `NOTICE`. That part is settled.

The risk is **not** the top level. It is that the upstream project bundles or
optionally depends on components under different and sometimes copyleft licenses.
Adopting a feature means inheriting that feature's dependency licensing, not just
the MIT top level.

### Components under non-MIT or copyleft-sensitive licensing

Based on the upstream attribution and license records:

| Component | Licensing signal | Implication for this project |
| --- | --- | --- |
| PDF-forms feature (PyMuPDF) | AGPL-sensitive for network-served use | **Exclude from the default build.** If ever needed, isolate as a separate, separately-reviewed service and re-assess obligations |
| Bundled web search (SearXNG) | AGPL-3.0 when run alongside the app | **Do not vendor.** Treat strictly as an external service dependency reached over the network |
| Deep-research component | Apache-2.0 (per upstream `licenses/`) | Permitted, but carries Apache-2.0 attribution and NOTICE obligations; preserve them if adapted |
| Other small adapted libraries | MIT (per upstream `licenses/`) | Permitted; preserve MIT notices |

### Licensing rules for Phase 2

- Adopting a feature requires reviewing **that feature's transitive
  dependencies**, not only the upstream top-level license.
- Copyleft-sensitive components (AGPL) are excluded from the default
  distribution. If a network-served AGPL component is ever introduced, it must
  be isolated and its obligations independently reviewed and approved.
- Apache-2.0-derived portions must carry their attribution and notice files.
- Every new third-party dependency is reviewed for license compatibility before
  merge.
- Model weights and media models carry their own licenses and must be reviewed
  per model; an MIT codebase does not imply permissively licensed models.

## Proposed M9+ Sequence

The ordering reflects dependencies and risk: enable a usable surface first,
then retrieval, then the higher-risk agentic and integration features. Each
milestone is a candidate, not a commitment.

### M9 — Product Surface (API client / Web UI)

- **Objective**: a first-party user-facing surface over the control-plane API.
- **Depends on**: M8 baseline.
- **Adopt / adapt**: reuse upstream UX and product concepts only.
- **Build fresh**: a new client that consumes the public control-plane API.
  The repository currently has no frontend; the upstream UI is coupled to its
  monolith and is not a drop-in.
- **Exclude**: server-rendered, local-state UI assumptions.
- **Licensing gate**: review any adopted frontend assets and fonts for license.
- **Security gate**: authenticated, per-tenant views; no privileged client-side
  trust; standard web hardening (CSP, CSRF, output encoding).
- **Exit criteria**: an authenticated user can drive the existing API
  (including the chat path) through the new surface.

### M10 — Retrieval (RAG) on Externalized Vector Storage

- **Objective**: document/knowledge retrieval grounded in tenant-isolated
  indexes.
- **Depends on**: M9 (or the API baseline), M3 externalized state.
- **Adopt / adapt**: retrieval and ranking *logic* patterns.
- **Build fresh / rebuild**: storage on managed or in-cluster vector services
  chosen in M3 (for example PostgreSQL with a vector extension, or a dedicated
  vector service).
- **Exclude**: embedded/local vector store implementations and local-FS index
  assumptions.
- **Licensing gate**: review embedding-model and vector-engine licenses.
- **Security gate**: strict per-tenant index isolation; no cross-tenant
  retrieval; size and rate limits.
- **Exit criteria**: retrieval works against externalized, per-tenant-isolated
  storage with no local-FS dependency.

### M11 — Agent and Tool Framework (Sandboxed)

- **Objective**: a controlled agent/tool execution capability.
- **Depends on**: M4 inference, M10 retrieval (if tools use retrieval), the M3
  hardened validation layer.
- **Adopt / adapt**: tool-schema, tool-parsing, and tool-security *patterns*.
- **Build fresh**: per-tenant execution scoping and sandboxing the baseline
  does not yet provide.
- **Exclude**: arbitrary shell/command execution in multi-tenant contexts
  unless a strong sandbox is designed and separately reviewed.
- **Licensing gate**: review tool dependencies.
- **Security gate**: strict tool allow-listing, path confinement, prompt-
  injection defenses, per-tenant authorization on every tool call, and no
  ambient cloud credentials reachable from tools.
- **Exit criteria**: tools run within an isolated, allow-listed, per-tenant
  sandbox with auditable execution.

### M12 — MCP Integration Layer

- **Objective**: expose selected capabilities through the Model Context
  Protocol.
- **Depends on**: M11 agent/tool framework.
- **Adopt / adapt**: the connection-manager pattern; treat each MCP server as an
  opt-in, independently reviewed integration.
- **Build fresh**: per-tenant credential scoping and connection isolation.
- **Exclude**: MCP servers whose dependencies fail the licensing or isolation
  gates.
- **Security gate**: each MCP server is sandboxed and authorized per tenant;
  secrets via managed secret storage only.
- **Exit criteria**: at least one MCP server runs with tenant-scoped credentials
  and isolation.

### M13 — Personal-Information Integrations (Optional)

- **Objective**: optional calendar, contacts, and mail integrations.
- **Depends on**: M3 hardened secret and URL-validation layer.
- **Adopt / adapt**: integration *concepts* only.
- **Build fresh / rebuild**: all credential handling through managed secrets;
  all outbound URLs through the hardened validation layer.
- **Security gate**: this is the area the upstream review flagged for weaker URL
  validation and plaintext credentials; no integration ships until both are
  remediated and reviewed. Per-tenant credential isolation is mandatory.
- **Exit criteria**: integrations pass a security review with no plaintext
  credential storage and validated outbound URLs.

### M14 — Media Services (Optional)

- **Objective**: optional speech, media, and image-generation features.
- **Depends on**: M4 inference plane, GPU capacity policy (M6).
- **Adopt / adapt**: service-boundary patterns.
- **Build fresh**: deployment as isolated, independently scaled services.
- **Exclude**: any media model whose license is incompatible or unreviewed.
- **Licensing gate**: per-model license review is mandatory.
- **Security gate**: resource isolation, abuse and rate limits, content policy.
- **Exit criteria**: at least one media service runs isolated, with reviewed
  model licensing and enforced limits.

## Excluded by Default (Any Phase)

Regardless of sequencing, these are excluded from the default build unless
separately designed, isolated, and approved:

- arbitrary shell/command execution in multi-tenant hosting
- AGPL-sensitive bundled components (for example the PyMuPDF PDF-forms feature)
- vendored AGPL services (for example bundling a web-search engine into the app
  rather than calling it as an external service)
- any embedded/local database or local vector store as a production default
- any feature that cannot enforce per-tenant isolation

## Decision Checklist (Per Feature)

Before a Phase 2 feature is promoted from candidate to a scheduled milestone, a
maintainer must confirm:

- [ ] the M8 baseline (and any prerequisite Phase 2 milestone) is complete
- [ ] the feature has a clear product justification, not just upstream
      availability
- [ ] the feature's full dependency licensing has been reviewed and is
      compatible
- [ ] no AGPL-sensitive code is vendored into the default build
- [ ] the feature enforces per-tenant and per-user isolation
- [ ] secret handling and outbound URL validation use the hardened M3 layer
- [ ] code-execution or integration surfaces are sandboxed and authorized
- [ ] provenance for any adapted code is recorded in `NOTICE`
- [ ] tests and observability cover the new surface

## Escalation Triggers

Per `AGENTS.md`, the following must be escalated to maintainers before any
implementation:

- any code-execution or sandboxing design
- any personal-information or credential-handling integration
- any AGPL-sensitive or license-uncertain component
- any change affecting tenant or user isolation
- any new production networking exposure
