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
