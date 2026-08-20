CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id STRING REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    event_id STRING NOT NULL,
    event_type STRING NOT NULL,

    aggregate_type STRING,
    aggregate_id UUID,

    source STRING NOT NULL DEFAULT 'github',
    repository STRING,
    actor STRING,

    payload JSONB NOT NULL DEFAULT '{}'::JSONB,

    occurred_at TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ,
    status STRING NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (event_id)
);

CREATE INDEX IF NOT EXISTS idx_events_org
ON events (org_id);

CREATE INDEX IF NOT EXISTS idx_events_source_type
ON events (source, event_type);

CREATE INDEX IF NOT EXISTS idx_events_occurred_at
ON events (occurred_at);
