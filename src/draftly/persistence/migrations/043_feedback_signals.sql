CREATE TABLE IF NOT EXISTS feedback_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT NOT NULL REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    source_message_id TEXT,
    source_url TEXT,
    content TEXT NOT NULL,
    topic TEXT,
    author TEXT,
    channel TEXT,
    category TEXT NOT NULL DEFAULT 'question',
    sentiment TEXT NOT NULL DEFAULT 'neutral',
    timestamp TIMESTAMPTZ,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, platform, source_event_id)
);

CREATE INDEX IF NOT EXISTS idx_feedback_signals_org_timestamp
    ON feedback_signals (org_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_feedback_signals_org_platform
    ON feedback_signals (org_id, platform);
