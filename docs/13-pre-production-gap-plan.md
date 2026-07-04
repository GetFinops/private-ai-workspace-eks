# Pre-Production Gap Plan (Web UI + Functional Surface)

> Public documentation. Like [`11-gap-analysis.md`](11-gap-analysis.md) and
> [`12-phase-2-feature-adoption.md`](12-phase-2-feature-adoption.md), this
> analyses gaps at the **component/category level** — it does not publish a
> file-by-file upstream porting list or restate upstream vulnerability details.
>
> **Verified 2026-06-18 against the upstream repo.** Two corrections came out of
> that check: (1) **licensing** — upstream relicensed MIT → **AGPL-3.0-or-later**
> on 2026-06-09, so upstream-inspired features must be built **clean-room**, not
> adapted from source (see `NOTICE` / `12-phase-2-feature-adoption.md`); and
> (2) the **feature surface** below was expanded — upstream also ships Compare,
> Notes/Tasks, a Documents *editor*, Cookbook/model-management, mail + contacts,
> an image editor/gallery, and TTS; its Deep Research is **web**-based.

## Purpose

Phase 2 (M9–M14) is complete in dev. Before the full staging-hardening pass
(**M7b**) and the public production release (**M8**), this document captures the
gap between the **built backend surface** and the **user-reachable product**, and
sequences the work so M7b hardens the *real* release surface rather than an API
users cannot reach.

## Headline finding

The dominant gap is **not** missing backend capability — it is **UI exposure**.
The control plane exposes ~27 endpoints across 12 capability areas (chat,
retrieval/RAG, memory, agent tools, agent loop, deep-research, MCP, integrations,
media STT + image). The M9 web UI surfaces **chat and notifications only** — ~3
endpoints. The large majority of built, dev-validated capability is currently
**invisible to users**.

Consequence for sequencing: M7b is defined (in `10-delivery-roadmap.md`) as
hardening *"the combined platform + adopted Phase-2 feature surface."* If the UI
is expanded **after** M8, M7b will have hardened endpoints users never touch, and
the newly-exposed surfaces (agent/tool, integration credentials, media uploads)
will need a second hardening pass. Therefore the UI/UX gap for the chosen release
set should close **before** M7b.

## 1. UI access gap — built vs. surfaced

| Capability (built + dev-validated) | Endpoint(s) | In the v1 UI today |
| --- | --- | --- |
| Chat completions | `POST /v1/chat/completions` (+ `…/stream` SSE) | ✅ |
| Notifications feed | `GET/POST /v1/notifications`, `…/{id}/read`, `…/stream` (SSE push) | ✅ |
| Retrieval / RAG | `POST /v1/retrieval/{documents,query,upload}` | ✅ |
| Per-user memory | `GET/POST /v1/memory`, `…/recall`, `DELETE …/{id}` | ✅ |
| Agent tools / loop / deep-research | `POST /v1/agent/{tools/invoke,runs,research}` (incl. web) | ✅ |
| MCP | `POST /v1/mcp/{tools/list,invoke}` | ✅ |
| Integrations (Google Calendar) | `POST /v1/integrations/{list,invoke}` | ✅ |
| Media (STT, image, TTS) | `POST /v1/media/{list,transcribe,generate,synthesize}` | ✅ |
| Model listing | (none — models hardcoded in Helm values) | ❌ |

> **Update (2026-07-04):** the reachability gap above is closed. Tier A (§5)
> surfaced RAG/memory/agents/media; a follow-up pass then added the remaining
> partials — TTS, real-time SSE notification push, optional hybrid **web** deep
> research (guarded external-service search, no AGPL engine bundled), and the
> **MCP** + **integrations** panels (escalation sign-off recorded in `NOTICE`).
> Only dynamic **model listing** (Tier B) remains un-surfaced. These surfaces are
> exactly what M7b now hardens.

**Decision (recorded):** the v1 UI is scoped to surface **all four** product
areas — retrieval/RAG (+ file upload), memory, agents (tools/loop/deep-research),
and media (STT + image). These are exactly the surfaces M7b must then harden.

## 2. Chat-UX table stakes (independent of new backend work)

Baseline expectations for an AI-workspace UI that the M9 SPA does not yet meet:

- **Conversation persistence.** History lives in `sessionStorage` only and is
  lost on tab close — there is no server-side thread store. (A `conversations`/
  `messages` table on the existing RDS, per-tenant/per-user scoped, mirrors the
  M9/M10 store patterns. Respect the M5 content policy: telemetry never carries
  message content; the content table is returned only to its owner.)
- **Streaming.** Chat is request/response; long completions block behind a
  spinner. Upstream-class products stream tokens (SSE). The control plane would
  add an SSE chat path; vLLM already supports streaming.
- **Rich rendering.** Assistant text is set via `textContent` (correct for XSS)
  but renders no markdown/code blocks. A vetted, sanitising markdown renderer
  (no `innerHTML` of un-sanitised model output) closes this safely.

## 3. Upstream-described features not yet built

Confirmed against the upstream `routes/` surface (2026-06-18). **All of these
must be built clean-room** — upstream is now AGPL-3.0, so its source cannot be
adapted into this MIT project.

- **Web search.** Upstream uses SearXNG (AGPL) as the default metasearch
  backend. **Do not vendor.** Legitimately addable as an **external-service
  integration via the M13 pattern** (call a search API over the network through
  the hardened URL guard; a separately-run SearXNG, or a non-AGPL provider).
- **Deep research is web-based upstream.** Our M11 deep-research runs *only* over
  the tenant's own retrieval corpus; upstream's does multi-step **web** research
  with source reading. Closing this depends on the web-search item above.
- **Text-to-speech.** Upstream has TTS (`routes/tts_routes.py`). We shipped STT +
  image in M14; TTS is a natural M14 follow-on media service.
- **Model management ("Cookbook").** Hardware-aware model recommendations,
  downloads, and serving. We have no model-management surface (the UI even
  hardcodes the model list) — bigger than the "model listing" gap in §1.
- **Compare.** Blind side-by-side model A/B testing + synthesis. Not present here
  at all.
- **Notes / Tasks** (incl. scheduled agent tasks) and a **Documents *editor***
  (writing-first, AI edits/suggestions — richer than RAG ingestion). Not present.
- **Mail + contacts.** M13 covers calendar (Google Calendar); upstream also has
  mail and contacts (CalDAV/CardDAV). Each is its own M13 per-integration
  adoption + credential review.
- **Image editor / gallery** (vs. just generation) and **2FA** (Extras).
- **File/document upload** for RAG — shipped in Tier A (#58); PDF via client-side
  pdf.js is the remaining follow-up.
- **Real-time notifications.** Currently 30s polling; SSE/websocket push is the
  upstream-class experience.

These are **Tier B / future-milestone** candidates, individually adoption-gated;
none gate the M8 release. They are listed for completeness now that the upstream
surface is verified.

## 4. Explicitly excluded — must NOT be reintroduced

Recorded so a "close the gaps" push does not undo a governance decision
(`12-phase-2-feature-adoption.md` → *Excluded by Default*):

- **PDF-forms / PyMuPDF** (AGPL-sensitive for network-served use).
- **Bundled SearXNG** (vendoring an AGPL search engine — external-service only).
- **Arbitrary shell/command execution** in multi-tenant hosting.
- **Local SQLite / embedded vector store** (local-first state) as a production
  default.

## 5. Tiered plan (sequenced against M7b → M8)

### Tier A — before M7b (gates a credible release)

1. **Surface the four release areas in the UI** — retrieval/RAG, memory, agents
   (runs + deep-research), media (STT + image). The harness/endpoints exist; this
   is front-end + thin control-plane glue (e.g. a `/v1/media/artifacts` fetch for
   generated images, a file-upload path for RAG).
2. **Server-side conversation persistence** (RDS thread store).
3. **Streaming chat (SSE)** and **safe markdown/code rendering.**
4. **File/document upload** for RAG (per-tenant, size-capped, S3-backed).
5. **Refresh [`11-gap-analysis.md`](11-gap-analysis.md)** — it currently
   describes M1-skeleton state and is misleading.

Each new UI surface enters M7b's review: agent/tool prompt-injection + sandbox
boundaries, integration credential scoping, media upload/abuse limits, per-tenant
isolation of conversations + artifacts.

### Tier B — strong fast-follow (M8-adjacent)

Dynamic model listing (`/v1/models`), per-user settings, prompt/system-prompt
templates, **TTS** (M14 follow-on), **web search via the M13 external-service
pattern**, real-time notifications (SSE).

### Tier C — do not do (governance-excluded)

PyMuPDF PDF-forms, bundled SearXNG, shell exec, local DB/vector store.

## 6. How this feeds M7b and M8

- **M7b scope** should be the platform baseline **plus the Tier-A-exposed
  surface**: conversation isolation, RAG upload abuse + per-tenant index
  isolation, agent prompt-injection/sandbox, integration credential scoping,
  media size/abuse limits, and the streaming path's auth/backpressure.
- **M8** ships the Tier-A feature set once M7b passes; Tier-B items are
  fast-follows that do not gate the release.

## 7. Escalation triggers (per `AGENTS.md`)

The Tier-A work touches sensitive areas that require maintainer review before
implementation, consistent with the standing escalation model:

- conversation/artifact persistence schema + per-tenant/user isolation;
- the file-upload path (size/type validation, S3 scoping, content policy);
- exposing agent/tool execution and integrations in a user-facing surface
  (confused-deputy / prompt-injection exposure);
- any new production networking exposure (SSE, upload endpoints).
