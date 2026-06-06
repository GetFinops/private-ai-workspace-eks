# M10 — Retrieval (RAG) on Externalized Vector Storage

> Read `docs/milestones/README.md` first. The standing rules there apply to
> this milestone and are not repeated here.
>
> This is a Phase 2 milestone. The full Phase 2 governance — adoption gating,
> licensing rules, isolation requirements, and excluded-by-default
> components — is in [`../12-phase-2-feature-adoption.md`](../12-phase-2-feature-adoption.md).
> Read that document before opening any M10 work.

## Status

Not started. Scaffolded as part of the Phase 2 kickoff. Requires explicit
maintainer adoption (see the Decision Checklist in the Phase 2 doc) before
implementation begins.

The vector-storage decision was deferred to this milestone per the record
in `NOTICE`'s "Vector-storage decision record (M3 deferral)". The current
recommendation in `09-aws-service-decision-matrix.md` and the Phase 2 doc
is **PostgreSQL + pgvector** as the first-pass choice; a dedicated vector
service is a later option if scale demands it.

## Objective

Document and knowledge retrieval grounded in tenant-isolated indexes.

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
- size and rate limits on indexing and retrieval operations
- chunking, embedding, and ranking patterns reused as *logic* from upstream

## Non-goals

- agents or tools using retrieval (M11)
- MCP-exposed retrieval (M12)
- embedded or local-FS vector stores — explicitly excluded
- a dedicated vector service unless pgvector is proven insufficient

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

## Provenance and licensing checkpoints

- Review embedding-model and vector-engine licenses.
- pgvector is PostgreSQL-licensed (permissive); record provenance in `NOTICE`.
- Any chosen embedding model carries its own license — record it.
- Avoid retrieval libraries that bundle AGPL-sensitive components.

## Security checkpoints

- Strict per-tenant index isolation. Cross-tenant retrieval must be
  impossible by design, not by application-layer check alone.
- Indexed content is treated as user data and follows the same data-handling
  rules as session content.
- No content (chunks, queries, retrieved passages) in telemetry. The M5
  content policy applies in full.
- Rate and size limits enforced server-side; UI-level limits are
  defense-in-depth only.
- Any deletion path must verify tenant ownership.

## Testing and validation

- A user can index a document and retrieve a relevant passage end-to-end.
- Cross-tenant retrieval attempts (scripted, with different tenant tokens)
  return zero results and produce an audit log entry.
- Backup/restore drill on the M3 RDS instance still succeeds with the
  pgvector extension installed.
- Rate-limit and size-limit tests reject oversized indexing payloads with
  a clear error.

## Exit criteria

- Retrieval works against externalized, per-tenant-isolated storage with
  no local-FS dependency.
- Per-tenant isolation is validated against scripted cross-tenant tests.
- Embedding strategy is documented and reflects an in-cluster posture.

## Escalation triggers

- choosing a vector backend other than pgvector (architectural decision)
- choosing to route embeddings through an external provider
- any cross-tenant isolation finding
- any embedding model whose license is unclear
