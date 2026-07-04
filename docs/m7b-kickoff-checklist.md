# M7b Kickoff Checklist — Full Staging Hardening

> Working checklist for the M7b milestone
> ([`milestones/m7b-full-staging-hardening.md`](milestones/m7b-full-staging-hardening.md)).
> It maps M7b's build tasks + checkpoints onto the **actual** post–Phase-2 surface,
> including everything shipped after the milestone doc was written (the #67 partials,
> #68 models/Compare, #73 Notes/Documents). Grounded in a code inventory across the
> six hardening dimensions (2026-07-04).

## Prerequisites

- **M7a complete** ✅ — paper review + license/governance sweeps, and both live drills
  (rollback, backup/restore) executed on `private-ai-workspace-dev` 2026-07-04 and
  PASSED ([`m7a-report.md`](m7a-report.md), PR #74). M7b **builds on** those; it does
  not repeat them.
- **M8 release-scope decision** — pin which Phase-2 milestones ship. Everything M9–M14
  is complete + surfaced, so the working assumption below is **all of M9–M14**. Each
  "if in the release" checkpoint applies.

## What M7a already established (do not re-drill)

- Instance-level RDS snapshot→restore (233s / 369s) and S3 versioning/lifecycle
  *config*; helm rollback on a broken deploy (81s detect / 16s recover, no outage);
  branch-protection contract verified live (≥1 review, no force-push/deletions, ≥1
  required check). M7b extends these to the *expanded surface + under load*.

## Scope note — what's NEW since the M7b milestone doc

The milestone doc predates these; M7b must explicitly cover them:

- **Two SSE streams** (`/v1/chat/stream`, `/v1/notifications/stream`) — new long-lived
  production networking.
- **New outbound egress**: `web_search.py` (hybrid web deep-research) through the guard.
- **New datastores**: `conversations`/`conversation_messages` (#57), **`notes`** (#73,
  migration 0007 — highest omission risk, postdates the M7a drill entirely).
- **New inference-amplifying endpoints**: `/v1/compare`, `/v1/documents/edit`.
- **New vendored artifact**: pdf.js binaries under `app/ui/static/vendor/`.
- **UI-surfaced MCP + Calendar** panels (confused-deputy escalation, signed off in
  `NOTICE`, carried here for load/injection validation).

---

## Task 1 — Staging soak under production-like load

- [ ] **[P0]** Stand up a staging env at production-like topology (UI tier, control
  plane, vLLM, embedding, media, MCP gateway, integrations egress lane) and run a
  sustained load soak; capture golden signals (M5 dashboards).
- [ ] **[P0]** **HTTP server backpressure.** `ThreadingHTTPServer` has **no
  max-thread/connection ceiling and no socket read/write timeouts** — thread-per-connection
  is unbounded (`server.py`). Verify a concurrency ceiling + timeouts so slow clients and
  the two SSE streams can't exhaust threads/FDs.
- [ ] **[P0]** **`/v1/chat/stream` has no connection-lifetime bound and no rate limiter
  on open** — a client can hold the socket indefinitely (`_handle_chat_stream`). Add a
  max-duration/idle timeout + a per-user concurrent-stream cap (the notifications stream
  already caps at `_NOTIF_STREAM_MAX_TICKS`; chat stream does not).
- [ ] **[P1]** **`/v1/notifications/stream`** — verify the lifetime cap holds under load
  and add a per-user simultaneous-connection limit (each connection pins a thread ≤5 min).
- [ ] **[P1]** **Missing rate limiters on high-cost paths**: `/v1/chat/completions` (the
  *primary* chat path — no app-layer `RateLimiter`), `/v1/retrieval/{query,upload}`,
  `/v1/memory/{record,recall}` (embedding compute per call), notes/conversations CRUD.
  Decide per-tenant rate policy and apply.
- [ ] **[P1]** **`/v1/compare` and `/v1/documents/edit` have `enabled=True` hardcoded —
  no kill-switch.** Both are compute-amplifying (compare fans out to N models). Verify a
  kill-switch + fan-out/size bounds.

## Task 2 — Rollback & failed-deploy recovery across the expanded surface

- [ ] **[P1]** Re-run the rollback drill per Phase-2 tier, not just the control plane:
  UI (nginx), vLLM, embedding, media services, MCP gateway — each should fail-closed and
  recover. (M7a covered the control-plane release only.)
- [ ] **[P1]** Verify a failed **schema migration** (e.g. a bad `notes`/`conversations`
  migration) is detected and recoverable — migrations run at startup (`_build_*_store`).

## Task 3 — Backup/restore for EVERY datastore

> Topology: **one** RDS Postgres (9 tables + pgvector) + **one** S3 artifact bucket.
> M7a drilled instance snapshot→restore; the deltas below are the M7b bar.

- [ ] **[P0]** **PITR, not just manual snapshot.** Drill
  `restore-db-instance-to-point-in-time` from automated backups (M7a used a manual
  `DBSnapshot`). Confirm the 7-day retention window is actually usable and
  `storage_encrypted=true` + the KMS key carry into the restored instance.
- [ ] **[P0]** **Prod-risk RDS defaults.** `deletion_protection=false` and
  `skip_final_snapshot=true` on non-prod must not reach prod (`infra/terraform/modules/rds`).
  Gate M8 on flipping these for the production instance.
- [ ] **[P1]** **pgvector-aware sentinel.** After restore, confirm the `vector` extension
  is present and an embedding column round-trips (`SELECT … embedding <=> %s::vector`
  returns sane cosine) — a restore can look fine (rows present) yet fail vector queries if
  the extension/opclass didn't return (`retrieval.py`, `memory.py`).
- [ ] **[P1]** **S3 object-level restore**, not config inspection. Delete/overwrite an
  object and recover it from a prior version (proves versioning is a *working* recovery
  mechanism); confirm `aws:kms` SSE on the restored object and that the noncurrent-version
  lifecycle rule doesn't expire versions inside the RPO.
- [ ] **[P1]** **Phase-2 datastores explicitly**: `conversations`+`conversation_messages`
  (FK `ON DELETE CASCADE` intact post-restore) and **`notes`** (#73 — verify it's in the
  backup set at all; the M7a report has no awareness of it).
- [ ] **[P1]** **`integration_tenant_state` restore fidelity** — a restore that lost/reset
  this table would **silently re-enable tenants that were disabled** (security-control
  state). Add an explicit sentinel assertion.
- [ ] **[P2]** Document the RPO/accepted-loss for ephemeral tables (`sessions`,
  `notifications`) — covered physically by the instance restore; just needs stating.
- [ ] **[P0]** Recovery procedures documented for **every** datastore (M7b exit criterion).

## Task 4 — Security-posture review of Phase-2 additions

### 4a. Per-tenant/user isolation UNDER LOAD (the central M7b delta)

> Every isolation surface has a *functional* cross-tenant/cross-user test; **none has a
> concurrency/load test.** M7b's job is the under-load proof.

- [ ] **[P0]** Retrieval index, per-user memory, and **MCP credential-scoping** under
  concurrent multi-tenant traffic — prove no cross-tenant/cross-user leakage and (MCP) that
  each spawned child gets only its own tenant's secret env when tenants invoke concurrently.
- [ ] **[P1]** Conversations, **notes/documents** (note: `test_documents.py` has **no**
  isolation test of its own — document isolation is only transitive through notes), and
  **media artifacts** (add a test that tenant B fetching tenant A's *known* `artifact_id`
  gets 404) under load.
- [ ] **[P1]** **Integration per-tenant OAuth token cache** under concurrent invokes — the
  highest-risk credential-bleed path, asserted only single-threaded today (`integrations.py`).
- [ ] **[P1]** **Noisy-neighbor / availability isolation.** `RateLimiter` concurrency cap
  is **global, not per-tenant** — verify one tenant saturating slots can't starve others
  (`agent_tools.py`).

### 4b. Egress & credential scoping

- [ ] **[P0]** **Web search (NEW egress).** Confirm every call routes through
  `validate_outbound_url` + `guarded_open` (SSRF-hardened), deny-by-default, and that the
  **single instance-wide `WEB_SEARCH` API key** (differs from the per-tenant model of every
  other integration) is never logged and is acceptable for the tenancy model (`web_search.py`,
  `config.py`).
- [ ] **[P1]** **`urllib.urlopen` on "trusted" internal backends follows redirects and does
  not pin DNS** (media, inference, embeddings). A compromised/misconfigured backend that
  3xx-redirects is followed with no private/metadata-IP check. Decide whether internal egress
  should also be guard-routed or redirect-disabled.
- [ ] **[P1]** **IRSA integrations secret scope is a wildcard** `<project>/<env>/integrations/*`
  — per-tenant isolation is **code-only** (`build_secret_id`), not IAM-enforced. Confirm the
  code path validates every component and decide if IAM-level per-tenant scoping is needed.
- [ ] **[P1]** **MCP subprocess egress** — the sandbox limits CPU/mem/FDs/FS-writes but does
  **not block network egress** from a child; an IO-capable MCP server would be an unguarded
  egress path. Confirm deny-by-default allow-list + NetworkPolicy are the only backstop and
  document it.
- [ ] **[P1]** Confirm no integration can set `allow_http=True` in production (only the
  loopback fixture may); OAuth refresh (`oauth2.googleapis.com`) is guard-routed with no
  private-host permit.

### 4c. Internal-only surfaces stay non-public

- [ ] **[P1]** **`/v1/inference/status` contradiction** — payload says `internal_only: true`
  but it's unauthenticated AND publicly proxied by nginx. Authenticate it or confirm the
  disclosed fields are safe.
- [ ] **[P1]** **Embedding chart has no NetworkPolicy** (unlike vllm/media) — isolation
  rests only on ClusterIP. Add one or confirm namespace reachability.
- [ ] **[P1]** Confirm `/metrics` stays off the nginx allow-list (in-cluster scrape only) and
  `networkPolicy.enabled=true` in every env override for vLLM/media.
- [ ] **[P2]** Unauthenticated public GETs (`/v1/models`, `/healthz`, `/readyz`) — decide if
  the posture disclosure (which subsystems are unconfigured; model roster) is acceptable;
  add rate limits if kept public.

### 4d. Prompt-injection / agent-tool sandbox

- [ ] **[P0]** **Deep-research hybrid web path (newest, highest-risk).** Attacker-controlled
  page titles/snippets from `web_search` flow verbatim into the synthesis prompt and the
  answer is rendered in the UI. Verify: web content is treated as data (never executed), the
  synthesis prompt is injection-resistant, and no tool/egress action can be driven from it
  (`deep_research.py`).
- [ ] **[P0]** **Confused-deputy: UI-surfaced MCP + Calendar.** The server holds tenant
  credentials; verify it acts only on the caller's own identity/tenant and a prompt-injected
  model or hostile web/document content cannot drive an integration/MCP call (docs/13 §7).
- [ ] **[P1]** **Agent loop** — a model-selected tool is re-checked against
  `is_allowed(allowlist, tenant, tool)` every call (no standing grant); tool observations fed
  back can't widen authorization/budgets (`agent_loop.py`).
- [ ] **[P1]** **Sandbox boundary** — tools run out-of-process with scrubbed env (no
  `AWS_*`/`DATABASE_URL`/OIDC/HF secrets reach the child), RLIMITs enforced, `AGENT_TOOLS_ENABLED`
  halts everything. MCP path injects per-tenant secrets into the child — bound its blast radius
  (`agent_tools.py`, `app/sandbox/`, `mcp.py`).
- [ ] **[P2]** Documents AI-edit + Compare are display-only (no action/egress channel) — confirm
  stateless (no server-side auto-apply) and gated like chat. Flag that an STT transcript / doc
  body is attacker-influenced text if later fed into agent context (indirect injection).

## Task 5 — Governance still in force

- [ ] **[P1]** Re-confirm branch protection + DCO + contribution flow (M7a verified live
  2026-07-04). **F-05** (`web_commit_signoff_required=false`) is the one open item — a repo
  Settings toggle only the owner can flip.

## Task 6 / Provenance & supply-chain checkpoints

- [ ] **[P0]** **No image scanning or SBOM exists** — grep for trivy/grype/cosign/scan across
  CI returns nothing. Wire a vulnerability scan + SBOM gate into the promotion path (M7b
  image-scan checkpoint).
- [ ] **[P0]** **Images are tag-pinned, not digest-pinned** (`vllm/vllm-openai:v0.9.0`,
  `text-embeddings-inference:cpu-1.6`, external-dns, etc.) — scanned ≠ deployed. Pin by
  `@sha256` so the scanned artifact equals the deployed one.
- [ ] **[P0]** **AGPL-exclusion guard is too narrow** — `license-sweep.sh` scans only
  `pyproject.toml` + `Chart.yaml`, not `web_search.py`, vendored JS, or Helm `values.yaml`
  where an operator could wire an AGPL provider. Extend the "no SearXNG / no AGPL in default
  build" assertion to cover them.
- [ ] **[P1]** **Extend `license-sweep.sh`** for new-this-cycle artifacts: pdf.js
  (`pdfjs-dist`), `web_search`, TTS, and the runtime images it currently omits (TEI,
  faster-whisper, media, external-dns). Confirm `NOTICE` records exist for each (wave-2,
  web-search, pdf.js, TTS records were added — the sweep should *assert* them).
- [ ] **[P1]** Drop a standalone `LICENSE`/`NOTICE` next to `app/ui/static/vendor/pdf.min.js`
  (Apache-2.0 attribution currently only in the minified header).

---

## P0 summary — gates M8

1. Staging soak + **server backpressure** (thread ceiling + timeouts) and **`/v1/chat/stream`
   lifetime/rate bound**.
2. **PITR** restore + **prod RDS defaults** (`deletion_protection`, `skip_final_snapshot`) +
   documented recovery for every datastore.
3. **Isolation under load** — retrieval, memory, MCP credential-scoping.
4. **Web-search egress** review + **deep-research web-injection** + **confused-deputy** (MCP/Calendar).
5. **Image scanning/SBOM + digest-pinning** + **broadened AGPL guard**.
6. Carry-overs from M7a findings: **F-02** (GitHub Actions cluster-admin → namespace-scope for prod).

## Exit criteria (from the milestone)

- [ ] Staging behaves like production across platform + adopted Phase-2 features.
- [ ] Recovery procedures documented for every datastore.
- [ ] Major operational risks known and owned (findings logged in an M7b report).
- [ ] No unresolved security or licensing items → M8 can open.

## Owners

| Area | Owner |
| --- | --- |
| Soak / backpressure / SSE | product-app + platform-infra |
| Backup/restore (PITR, pgvector, S3) | platform-infra |
| Isolation-under-load tests | product-app |
| Egress / credential scoping | governance-security + product-app |
| Prompt-injection / sandbox | product-app (agent) + governance-security |
| Image scan / SBOM / licensing | governance-security + platform-infra |
