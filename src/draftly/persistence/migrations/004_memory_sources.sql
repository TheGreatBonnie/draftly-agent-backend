CREATE TABLE IF NOT EXISTS memory_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    memory_item_id UUID NOT NULL
        REFERENCES memory_items (id)
        ON DELETE CASCADE,

    source_type TEXT NOT NULL,
    source_id TEXT,

    source_url TEXT,

    repository TEXT,
    commit_sha TEXT,

    content_hash TEXT,

    evidence TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memory_sources_item
ON memory_sources (memory_item_id);

-- Add org_id for multi-tenant data isolation
ALTER TABLE memory_sources
ADD COLUMN IF NOT EXISTS org_id TEXT
    REFERENCES organizations(clerk_org_id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_memory_sources_org
ON memory_sources (org_id);
