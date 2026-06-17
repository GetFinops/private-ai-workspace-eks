# Pre-Production Gap Plan (Web UI + Functional Surface)

> Public documentation. Like [`11-gap-analysis.md`](11-gap-analysis.md) and
> [`12-phase-2-feature-adoption.md`](12-phase-2-feature-adoption.md), this
> analyses gaps at the **component/category level** — it does not publish a
> file-by-file upstream porting list or restate upstream vulnerability details.

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
| Chat completions | `POST /v1/chat/completions` | ✅ |
| Notifications feed | `GET/POST /v1/notifications`, `…/{id}/read` | ✅ |
| Retrieval / RAG | `POST /v1/retrieval/{documents,query}` | ❌ |
| Per-user memory | `GET/POST /v1/memory`, `…/recall`, `DELETE …/{id}` | ❌ |
| Agent tools / loop / deep-research | `POST /v1/agent/{tools/invoke,runs,research}` | ❌ |
| MCP | `POST /v1/mcp/{tools/list,invoke}` | ❌ |
| Integrations (Google Calendar) | `POST /v1/integrations/{list,invoke}` | ❌ |
| Media (STT, image) | `POST /v1/media/{list,transcribe,generate}` | ❌ |
| Model listing | (none — models hardcoded in Helm values) | ❌ |

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

Category-level, from the upstream review and the Phase-2 doc:

- **Web search.** Upstream bundles a search engine (SearXNG) — **excluded as a
  vendored AGPL component** (`12-phase-2-feature-adoption.md`). It is, however,
  legitimately addable as an **external-service integration via the M13 pattern**
  (call it over the network through the hardened URL guard; do not vendor it).
- **Text-to-speech.** M14 shipped STT + image generation; TTS is a natural M14
  follow-on media service (its own per-model license review).
- **File/document upload** for RAG ingestion. Retrieval indexing accepts text
  only today; a per-tenant, size-capped upload path (S3 + extract→index) is
  needed for the UI RAG flow.
- **Real-time notifications.** Currently 30s polling; SSE/websocket push is the
  upstream-class experience.

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
