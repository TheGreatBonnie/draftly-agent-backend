CREATE TABLE IF NOT EXISTS memory_consolidations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id STRING REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    operation STRING NOT NULL,
    target_memory_id UUID,

    source_memory_ids JSONB NOT NULL DEFAULT '[]'::JSONB,

    reason STRING,
    model STRING,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
