-- Durable application jobs, account lifecycle fields and operational indexes.
-- This migration is intentionally idempotent because existing Render databases
-- already contain the app-owned schema from the initial bootstrap.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE app_users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_app_sessions_expiry ON app_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_audit_events_user_created ON audit_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trip_metadata_archived_updated ON trip_metadata(archived, updated_at DESC);

CREATE TABLE IF NOT EXISTS travel_jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    thread_id TEXT,
    operation TEXT NOT NULL DEFAULT 'travel',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'awaiting_approval', 'completed', 'blocked', 'failed', 'cancelled')),
    result JSONB,
    error_code TEXT,
    error_message TEXT,
    request_id TEXT,
    idempotency_key TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_travel_jobs_claim ON travel_jobs(available_at, created_at)
    WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_travel_jobs_user_created ON travel_jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_travel_jobs_thread ON travel_jobs(thread_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_travel_jobs_idempotency
    ON travel_jobs(user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_password_reset_user_expiry
    ON password_reset_tokens(user_id, expires_at DESC);
