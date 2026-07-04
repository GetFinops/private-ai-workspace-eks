# Gap Analysis

## Public Disclosure Scope

This document is part of the public documentation set. It is written to be
safe for public exposure. It therefore:

- describes implementation gaps at the component and milestone level
- states the project's reuse posture (selective adaptation, not a fork)
- avoids publishing a file-by-file upstream porting list
- avoids restating specific upstream vulnerability details
- defers detailed provenance and security-sensitive porting decisions to
  maintainer review

Detailed per-file provenance mapping and any security-sensitive porting
decisions are handled through maintainer review and recorded in `NOTICE`, not
enumerated here. See `docs/04-governance-and-contribution.md` for the
escalation model.

## Purpose

This report compares the current state of the repository against the planned
scope in the planning bundle (`docs/03-implementation-plan.md`,
`docs/10-delivery-roadmap.md`, and the root `README.md`). It identifies what is
built, what is missing, and how remaining work should be approached.

## Method

The analysis reviewed:

- the root `README.md` scope and MVP definition
- the planning bundle in `docs/`
- the control-plane code under `app/`
- the infrastructure baseline under `infra/`
- the deployment scaffolding under `deploy/`
- the test suite under `tests/`
- the provenance record in `NOTICE`

## Current State Summary

> Updated after Phase 2. The platform baseline (M0–M6) plus M7a, and the full
> Phase-2 feature track (M9–M14), are **complete and dev-validated**. The
> remaining roadmap is M7b (full staging hardening) → M8 (production release).
> The original "control-plane skeleton" framing of this document is obsolete and
> has been replaced below.

### Built (M0–M14, dev-validated)

- **Governance + docs**: full planning bundle, governance files, CI (structure +
  `unittest`), provenance in `NOTICE`.
- **Control plane** (`app/control_plane/`): real OIDC token verification +
  per-tenant/per-user isolation; externalized PostgreSQL session/notification/
  retrieval/memory stores; an OpenAI-compatible chat path to an internal vLLM
  plane with degraded-mode handling; `/metrics`. Endpoints across ~12 capability
  areas (chat, notifications, retrieval/RAG, memory, agent tools, agent loop,
  deep-research, MCP, integrations, media) — see
  [`13-pre-production-gap-plan.md`](13-pre-production-gap-plan.md) §1 for the
  endpoint table.
- **Feature surfaces**: M10 retrieval + per-user memory on pgvector; M11 sandboxed
  agent tools + agent loop + deep-research; M12 sandboxed MCP; M13 integration
  harness + Google Calendar (live-validated against the real API); M14 media
  harness + Whisper STT (GPU-validated) + SDXL image-gen.
- **Infrastructure**: VPC/EKS/ECR/RDS/S3, IRSA, Cognito, NetworkPolicy
  enforcement (egress lockdown), GPU provisioning (Karpenter + managed warm-pool).
- **Web UI** (M9 + Tier-A pre-production pass + partial-closure pass): vanilla-JS
  SPA (OIDC PKCE) surfacing **chat (streaming) + notifications + Documents/RAG +
  memory + agent (runs + deep-research, incl. hybrid web research) + media (STT +
  image + TTS) + MCP + integrations (Google Calendar)**, with server-side
  conversation persistence, safe markdown/code rendering, client-side PDF text
  extraction, and **real-time SSE notification push**. Every ~12-capability
  backend area now has a user-reachable surface.
- **Tests**: full stdlib `unittest` suite (560+).

### Provenance posture

Selective upstream adaptation is already in use and recorded in `NOTICE`. The
inference routing and client logic were adapted from MIT-licensed upstream
patterns, and parts of the Terraform baseline were adapted from a permissive
AWS sample. The repository is intentionally a new project rather than a fork.

## Gap Table

The platform and feature backends are built. The **Tier-A pre-production pass**
([`13-pre-production-gap-plan.md`](13-pre-production-gap-plan.md) §5) has since
closed the dominant reachability gap: the four release areas (RAG, memory,
agents, media) are now surfaced in the UI, conversation persistence + streaming +
safe rendering shipped, and RAG file upload (incl. client-side PDF extraction) is
live. Measured against the served surface, UI reachability moved from **~3 of ~26
endpoints (~89% unreachable)** to **~16 of 26 (~38% backend-only)**. The remaining
gaps are two un-surfaced capability areas plus chat-product polish:

| Area | Built backend | Status |
| --- | --- | --- |
| **UI feature exposure** | retrieval, memory, agents, deep-research, MCP, integrations, media all have working endpoints | 🟢 **closed** — RAG/memory/agent/media surfaced (Tier A #57–#64); **MCP** and **integrations (Google Calendar)** panels now surfaced too (escalation sign-off in `NOTICE`) |
| Conversation persistence | stateless chat path | 🟢 **closed** — server-side RDS thread store (list/get/delete/messages), per-tenant/user scoped (#57) |
| Streaming | request/response chat | 🟢 **closed** — SSE token streaming via `/v1/chat/stream` (#60) |
| Rich rendering | plain-text chat bubbles | 🟢 **closed** — safe markdown/code renderer, no `innerHTML` (#61) |
| File upload (RAG) | text-only document indexing | 🟢 **closed** — per-tenant upload→extract→index; PDF via vendored client-side pdf.js |
| Real-time + delivery | 30s notification polling | 🟢 **closed** — `/v1/notifications/stream` SSE push (bounded, content-safe), polling kept as a backstop |
| Agent / deep research | corpus-only research | 🟢 **closed** — optional hybrid **web** research via a guarded external-service search client (deny-by-default, no AGPL engine bundled) |
| Media | STT + image | 🟢 **closed** — **TTS** added (`/v1/media/synthesize`, OpenAI speech shape); image editor/gallery still future |
| Model selection | internal vLLM routing | 🟢 **closed** — dynamic `GET /v1/models` (config-served, GPU-independent); UI selector populated from it |
| Compare | — | 🟢 **closed** — `POST /v1/compare`: blind A/B of one prompt across N models + optional synthesis, over the existing inference client |
| Upstream parity (optional) | — | 🟡 **open** — Cookbook/model-mgmt, Notes/Tasks, Documents editor, mail+contacts, 2FA (each Tier-B / its own adoption) |

The remaining 🟡 items are sequenced against M7b → M8 in
[`13-pre-production-gap-plan.md`](13-pre-production-gap-plan.md) §5 (Tier B /
future); none gate the M8 release. The now-surfaced **MCP** and **integrations**
panels, plus the SSE push and web-search egress, carry into M7b hardening
(agent/tool prompt-injection + sandbox, streaming auth/backpressure) per that
document's §7.

## Reuse Posture: Adapt Selectively, Do Not Fork

> **CORRECTION (verified 2026-06-18):** "selective reuse / adapt narrow patterns"
> applied while upstream was MIT (its v1.0, 2026-05-31). Upstream **relicensed to
> AGPL-3.0-or-later on 2026-06-09.** This project's existing adaptations predate
> that and remain MIT (see `NOTICE`), but **no further code may be adapted from
> upstream** — new upstream-inspired features must be **clean-room**. The
> "what to adapt" guidance below is retained as historical rationale for the
> already-adapted code only; it is not a license to pull in current AGPL source.
> See `12-phase-2-feature-adoption.md` → "Top-level position".

The planning bundle already decided on a new repository with selective reuse
rather than a wholesale fork, and that decision still holds. The rationale:

- Upstream inspiration is oriented toward local-first, single-host operation.
  Direct reuse would re-import local-state assumptions that conflict with this
  project's externalized-state, cloud-native direction.
- Some upstream optional features carry copyleft-sensitive licensing and must
  stay excluded from the default build.
- The MVP needs a much smaller feature surface than upstream provides.

### What to adapt

At the category level, the strongest candidates for adaptation are narrow,
permissively licensed building blocks that fit a stateless, externalized-state
control plane:

- request and endpoint validation patterns
- authentication and session-handling patterns, reworked for an external
  identity provider and an externalized session store
- hardened outbound URL validation and secret-handling patterns
- readiness and degraded-mode patterns

Each adaptation must preserve required upstream notices and record provenance
in `NOTICE`.

### What to build fresh

- the managed-database persistence layer
- object-storage flows
- managed-secret retrieval
- production-grade HTTP serving and request handling
- observability instrumentation

### What to exclude

- anything assuming a local default database, local data directories, or local
  vector storage
- copyleft-sensitive optional features
- the broad set of upstream features that fall outside the MVP

## Recommended Next Focus

The control-plane core is done. The highest-value pre-production work is closing
the **UI/UX gap** so the release surface is coherent and M7b hardens what users
actually touch (full plan in
[`13-pre-production-gap-plan.md`](13-pre-production-gap-plan.md)):

1. surface the four product areas (retrieval/RAG + upload, memory, agents +
   deep-research, media) in the web UI;
2. add server-side conversation persistence, streaming chat (SSE), and safe
   markdown rendering;
3. then run M7b over the combined platform + exposed feature surface, and ship
   M8.

## Escalation Notes

Per `AGENTS.md` and `docs/04-governance-and-contribution.md`, several of the
gaps above touch sensitive areas that require maintainer review before
implementation:

- authentication and session semantics
- secret handling
- licensing and provenance decisions for any adapted code
- production networking exposure
- tenant and user isolation behavior

These should not be resolved unilaterally by an automated contributor.

## Milestone Instructions

Per-milestone build instructions for automated and human contributors live in
`docs/milestones/`. Each file expands a roadmap milestone into objective,
scope, tasks, provenance and security checkpoints, testing requirements, and
exit criteria.
