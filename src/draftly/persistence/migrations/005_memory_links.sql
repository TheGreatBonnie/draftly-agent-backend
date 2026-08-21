CREATE TABLE IF NOT EXISTS memory_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source_memory_id UUID NOT NULL
        REFERENCES memory_items (id)
        ON DELETE CASCADE,

    target_memory_id UUID NOT NULL
        REFERENCES memory_items (id)
        ON DELETE CASCADE,

    relationship TEXT NOT NULL,

    confidence FLOAT8 NOT NULL DEFAULT 0.5,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT memory_links_confidence_check
        CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS idx_memory_links_source
ON memory_links (source_memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_links_target
ON memory_links (target_memory_id);

-- Add org_id for multi-tenant data isolation
ALTER TABLE memory_links
ADD COLUMN IF NOT EXISTS org_id TEXT
    REFERENCES organizations(clerk_org_id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_memory_links_org
ON memory_links (org_id);
