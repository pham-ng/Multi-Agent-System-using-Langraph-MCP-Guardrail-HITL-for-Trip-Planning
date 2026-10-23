-- Shared fixed-window rate limiting for multiple web instances.
CREATE TABLE IF NOT EXISTS rate_limit_buckets (
    bucket_key TEXT PRIMARY KEY,
    window_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hits INTEGER NOT NULL DEFAULT 0 CHECK (hits >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_buckets_updated ON rate_limit_buckets(updated_at);
