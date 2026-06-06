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

