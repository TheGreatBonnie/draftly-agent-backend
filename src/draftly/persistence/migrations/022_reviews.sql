-- persistence/migrations/022_reviews.sql
CREATE TABLE IF NOT EXISTS reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id STRING NOT NULL,

    thread_id STRING NOT NULL,

    workflow STRING NOT NULL,

    tool_name STRING NOT NULL,

    tool_args JSONB NOT NULL DEFAULT '{}'::JSONB,

    action_description STRING,

    status STRING NOT NULL DEFAULT 'pending',

    reviewer_id STRING,

    decision STRING,

    decision_comment TEXT,

    decided_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    expires_at TIMESTAMPTZ,

    notification_sent_at TIMESTAMPTZ,

    metadata JSONB DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS idx_reviews_pending ON reviews(status, created_at)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_reviews_org ON reviews(org_id);
CREATE INDEX IF NOT EXISTS idx_reviews_thread ON reviews(thread_id);