# M10 — Retrieval and Memory on Externalized Vector Storage

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.
>
> This is a Phase 2 milestone. The full Phase 2 governance — adoption gating,
> licensing rules, isolation requirements, and excluded-by-default
> components — is in [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md).
> Read that document before opening any M10 work.

## Status

In progress (adopted). First increment landed: **document retrieval** on
pgvector.

Done in this increment:
- pgvector schema (migration 0003): `documents` + `document_chunks` tables,
  tenant-scoped, with a `vector(384)` embedding column.
- `app/control_plane/embeddings.py`: `EmbeddingClient` protocol + a
  dependency-free `DeterministicEmbeddingClient` for dev/tests.
- `app/control_plane/retrieval.py`: in-memory + pgvector stores and the pure
  index/query handlers, with per-tenant isolation at the store layer, size and
  rate limits, and an `indexing_complete` event into the M9 notifications feed.
- API: `POST /v1/retrieval/documents` (index) and `POST /v1/retrieval/query`.
- Unit tests (`tests/test_retrieval.py`) including a cross-tenant isolation case.

Remaining for the milestone (follow-up PRs):
- per-user **long-term memory** surface (build tasks 8–9) with explicit
  list/export/delete user controls;
- production in-cluster **embedding backend** (build task 6);
- **dev-deployment smoke test** for retrieval/memory against pgvector on the dev
  RDS (the standing Phase 2 validation rule);
- observability for retrieval (build task 7).

The isolation model mirrors the M9 notifications service and is flagged for
maintainer review on the PR per the Phase 2 isolation escalation trigger.

The vector-storage decision was deferred to this milestone per the record
in `NOTICE`'s "Vector-storage decision record (M3 deferral)". The current
recommendation in `09-aws-service-decision-matrix.md` and the Phase 2 doc
is **PostgreSQL + pgvector** as the first-pass choice; a dedicated vector
service is a later option if scale demands it.

## Objective

Document and knowledge retrieval — and per-user long-term memory — grounded
in tenant-isolated indexes.

The Phase 2 governance doc lists *"retrieval and memory"* together as a single
high-risk subsystem because the cross-tenant data-leakage failure mode is the
same: any vector or document store that holds user content must enforce
isolation at the storage layer. This milestone owns both surfaces so they
share one isolation model rather than diverging.

## Primary workstreams

- product-app
- platform-infra

## Prerequisites

- M9 (or the API baseline if M9 is skipped for this release).
- M3 externalized state.

## In scope

- retrieval and ranking logic on the control plane
- vector storage on managed or in-cluster services (default: pgvector on
  the existing RDS PostgreSQL instance)
- per-tenant index isolation
- per-user long-term memory storage and recall (opt-in, scoped to one user)
- size and rate limits on indexing, retrieval, and memory-write operations
- chunking, embedding, and ranking patterns reused as *logic* from upstream
- explicit user controls to view, export, and delete stored memories

## Non-goals

- agents or tools using retrieval or memory (M11)
- MCP-exposed retrieval or memory (M12)
- embedded or local-FS vector stores — explicitly excluded
- a dedicated vector service unless pgvector is proven insufficient
- cross-user or cross-tenant memory sharing — explicitly excluded
- implicit memory capture without a clear user-visible opt-in

## Build tasks

1. Confirm the chosen vector backend (default: pgvector on the existing
   RDS PostgreSQL). If switching to a dedicated service, treat that as a
   separate decision recorded in `NOTICE`.
2. Add the pgvector extension to the M3 RDS instance via Terraform; document
   the migration in the M3 follow-up section.
3. Implement indexing and retrieval logic in the control plane behind the
   public API contract. Do not embed retrieval logic in the inference
   service.
4. Enforce per-tenant index isolation at the database layer (separate
   schema, table, or row-level security — choose explicitly and document).
5. Apply size and rate limits on indexing (per upload) and retrieval (per
   request) to keep multi-tenant operations bounded.
6. Choose and document an embedding model strategy: either run embeddings
   on the vLLM inference plane (M4) or call a dedicated embedding deployment.
   Either way, embeddings are computed in-cluster; no external embedding
   provider by default.
7. Add observability: retrieval latency, recall proxies, per-tenant index
   size, embedding throughput. Reuse the M5 metrics infrastructure; respect
   the M5 content policy (never log document content in telemetry).
8. Add per-user long-term memory as a separate logical surface on the same
   pgvector backend: distinct schema or table from document retrieval,
   scoped strictly to one user (not one tenant). Memory writes are opt-in
   via an explicit user-visible setting and explicit per-write consent or
   policy; never implicit.
9. Provide user-controls endpoints to list, export, and delete stored
   memories. Deletion must be authoritative (no soft-delete fallback that
   leaves data recoverable without an audit trail).
10. Emit `indexing.completed` and `indexing.failed` events into the M9
    notifications service when long-running indexing jobs finish. Events
    carry only event class, document id, and timestamps — never document
    content, chunks, or extracted text. If M9 is not yet shipped, this
    step degrades cleanly (events are no-ops) and re-activates when M9
    lands.

## Provenance and licensing checkpoints

- Review embedding-model and vector-engine licenses.
- pgvector is PostgreSQL-licensed (permissive); record provenance in `NOTICE`.
- Any chosen embedding model carries its own license — record it.
- Avoid retrieval libraries that bundle AGPL-sensitive components.

## Security checkpoints

- Strict per-tenant index isolation. Cross-tenant retrieval must be
  impossible by design, not by application-layer check alone.
- Strict per-user memory isolation. Cross-user memory recall must be
  impossible by design; memory is scoped one level tighter than retrieval.
- Indexed content and stored memories are treated as user data and follow
  the same data-handling rules as session content.
- No content (chunks, queries, retrieved passages, memory text) in
  telemetry. The M5 content policy applies in full.
- Rate and size limits enforced server-side; UI-level limits are
  defense-in-depth only.
- Memory writes require an explicit user-visible setting and an explicit
  per-write consent or policy. Implicit "background memory capture" is
  excluded by default.
- Any deletion path must verify tenant ownership for documents and user
  ownership for memories.

## Testing and validation

- A user can index a document and retrieve a relevant passage end-to-end.
- A user can opt in to memory, record a memory, recall it, and delete it.
- Cross-tenant retrieval attempts (scripted, with different tenant tokens)
  return zero results and produce an audit log entry.
- Cross-user memory recall attempts (scripted, with different user tokens
  in the same tenant) return zero results and produce an audit log entry.
- Backup/restore drill on the M3 RDS instance still succeeds with the
  pgvector extension installed and memory tables populated.
- Rate-limit and size-limit tests reject oversized indexing and memory-write
  payloads with a clear error.
- Memory deletion is verified as authoritative (post-deletion recall
  returns nothing and no soft-deleted rows remain reachable).
- If M9 is deployed, an `indexing.completed` event reaches the target
  user's feed; cross-tenant publishers cannot emit into another tenant's
  feed.

## Dev deployment validation

Per the standing Phase 2 rule in `docs/milestones/README.md`:

- Enable retrieval/memory in `deploy/values/dev/` once it exists.
- Run a dev-deployment smoke test that indexes a small document, runs
  a retrieval query end-to-end against pgvector on the dev RDS instance,
  records and recalls one memory, and (if M9 is deployed) confirms the
  `indexing.completed` notification reaches the user's feed.
- The smoke test exercises the M1-adapted-from-Odysseus inference path
  (embeddings flow through `app/control_plane/routing.py` +
  `inference.py`) end-to-end.
- Record the run in the milestone PR; failures block merge.

## Exit criteria

- Retrieval works against externalized, per-tenant-isolated storage with
  no local-FS dependency.
- Memory works against externalized, per-user-isolated storage with no
  local-FS dependency and with explicit user controls for listing,
  exporting, and deleting stored memories.
- Per-tenant and per-user isolation are validated against scripted tests.
- Embedding strategy is documented and reflects an in-cluster posture.

## Escalation triggers

- choosing a vector backend other than pgvector (architectural decision)
- choosing to route embeddings through an external provider
- any cross-tenant or cross-user isolation finding
- any proposal to enable implicit "background memory capture" without
  explicit user-visible opt-in
- any embedding model whose license is unclear
