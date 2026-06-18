-- Control-plane schema — migration 0001
-- Applied by app.db.migrations.apply_migrations on first startup.

-- Session store: externalized replacement for InMemorySessionStore.
-- One row per live session; expired rows are pruned on access or by a
-- periodic cleanup (see app.db.migrations.purge_expired_sessions).
CREATE TABLE IF NOT EXISTS sessions (
    session_id  UUID        PRIMARY KEY,
    subject     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at);

-- Migration 0002: notification events table (M9 — in-app notification service)
-- Events carry no prompt/completion content per the M5 content policy.
-- Columns: event_class, resource_id, timestamps only.
CREATE TABLE IF NOT EXISTS notifications (
    id          UUID        PRIMARY KEY,
    tenant_id   TEXT        NOT NULL,
    user_id     TEXT        NOT NULL,
    event_class TEXT        NOT NULL,
    resource_id TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    read_at     TIMESTAMPTZ
);

-- Index supports the common query: list recent unread events for one user.
CREATE INDEX IF NOT EXISTS idx_notifications_user
    ON notifications (tenant_id, user_id, created_at DESC);

-- Migration 0003: retrieval (M10 — document/knowledge retrieval on pgvector)
-- Tenant-isolated document index. Per the M5 content policy, telemetry never
-- carries document text; the text itself lives only in these tables and is
-- returned only to the owning tenant.
--
-- pgvector provides the `vector` type and cosine-distance operator (<=>).
-- RDS PostgreSQL 16 ships pgvector on the extension allow-list, so creating
-- the extension from the migration runner keeps DDL ownership with the app
-- (consistent with the sessions/notifications migrations) rather than splitting
-- it into Terraform, which would need a DB session at apply time.
CREATE EXTENSION IF NOT EXISTS vector;

-- One row per indexed source document, scoped to a tenant.
CREATE TABLE IF NOT EXISTS documents (
    id          UUID        PRIMARY KEY,
    tenant_id   TEXT        NOT NULL,
    user_id     TEXT        NOT NULL,
    title       TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant
    ON documents (tenant_id, created_at DESC);

-- One row per chunk of a document, carrying its embedding. tenant_id is
-- denormalised onto the chunk so every retrieval query filters by tenant at
-- the storage layer (isolation by design, not by join correctness alone).
-- The embedding dimension (384) matches the EMBEDDING_DIM constant in
-- app/control_plane/embeddings.py; changing one requires a migration.
CREATE TABLE IF NOT EXISTS document_chunks (
    id           UUID        PRIMARY KEY,
    document_id  UUID        NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    tenant_id    TEXT        NOT NULL,
    chunk_index  INTEGER     NOT NULL,
    content      TEXT        NOT NULL,
    embedding    vector(384) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_tenant
    ON document_chunks (tenant_id);

-- Migration 0004: per-user long-term memory (M10 — memory surface)
-- Memory is scoped one level tighter than retrieval: to a single
-- (tenant_id, user_id). Cross-user recall is impossible by design — every
-- query filters by user_id at the storage layer. Writes are opt-in with
-- explicit per-write consent enforced at the API layer; deletion is
-- authoritative (the row is removed, not soft-deleted).
CREATE TABLE IF NOT EXISTS memories (
    id          UUID        PRIMARY KEY,
    tenant_id   TEXT        NOT NULL,
    user_id     TEXT        NOT NULL,
    content     TEXT        NOT NULL,
    embedding   vector(384) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_user
    ON memories (tenant_id, user_id, created_at DESC);

-- Migration 0005: per-tenant integration operator switch (M13)
-- Operator per-tenant disable for personal-information integrations, distinct
-- from the deny-by-default allow-list. Default-enabled: absence of a row means
-- enabled; a row with enabled = FALSE switches a tenant off cluster-side
-- (incident response, abuse) without editing the allow-list. Carries no
-- credentials or content — tenant + integration name + flag + timestamp only.
CREATE TABLE IF NOT EXISTS integration_tenant_state (
    tenant_id   TEXT        NOT NULL,
    integration TEXT        NOT NULL,
    enabled     BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, integration)
);

-- Migration 0006: server-side conversation persistence (pre-production Tier A)
-- Chat threads + messages scoped to one (tenant_id, user_id). Message content is
-- the user's own data, returned only to its owner (same handling as memories);
-- telemetry never carries it (M5). ON DELETE CASCADE removes a thread's messages.
CREATE TABLE IF NOT EXISTS conversations (
    id          UUID        PRIMARY KEY,
    tenant_id   TEXT        NOT NULL,
    user_id     TEXT        NOT NULL,
    title       TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations (tenant_id, user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id              UUID        PRIMARY KEY,
    conversation_id UUID        NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    tenant_id       TEXT        NOT NULL,
    user_id         TEXT        NOT NULL,
    role            TEXT        NOT NULL,
    content         TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages
    ON conversation_messages (conversation_id, created_at);

