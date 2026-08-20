CREATE TABLE IF NOT EXISTS memory_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    memory_item_id UUID NOT NULL
        REFERENCES memory_items (id)
        ON DELETE CASCADE,

    feedback_type STRING NOT NULL,
    source STRING,

    score FLOAT8 NOT NULL DEFAULT 0.5,

    comment STRING,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT memory_feedback_score_check
        CHECK (score >= 0 AND score <= 1)
);

CREATE INDEX IF NOT EXISTS idx_memory_feedback_item
ON memory_feedback (memory_item_id);

-- Add org_id for multi-tenant data isolation
ALTER TABLE memory_feedback
ADD COLUMN IF NOT EXISTS org_id STRING
    REFERENCES organizations(clerk_org_id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_memory_feedback_org
ON memory_feedback (org_id);
