CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,

    aggregate_type TEXT,
    aggregate_id UUID,

    source TEXT NOT NULL DEFAULT 'github',
    repository TEXT,
    actor TEXT,

    payload JSONB NOT NULL DEFAULT '{}'::JSONB,

    occurred_at TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (event_id)
);

CREATE INDEX IF NOT EXISTS idx_events_org
ON events (org_id);

CREATE INDEX IF NOT EXISTS idx_events_source_type
ON events (source, event_type);

CREATE INDEX IF NOT EXISTS idx_events_occurred_at
ON events (occurred_at);
