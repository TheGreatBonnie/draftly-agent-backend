CREATE TABLE IF NOT EXISTS memory_access_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    memory_item_id UUID NOT NULL
        REFERENCES memory_items (id)
        ON DELETE CASCADE,

    agent STRING,
    workflow STRING,
    query STRING,

    similarity_score FLOAT8,
    rank INT8,
    used BOOL NOT NULL DEFAULT false,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memory_access_log_item
ON memory_access_log (memory_item_id);

-- Add org_id for multi-tenant data isolation
ALTER TABLE memory_access_log
ADD COLUMN IF NOT EXISTS org_id STRING
    REFERENCES organizations(clerk_org_id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_memory_access_log_org
ON memory_access_log (org_id);
