CREATE TABLE IF NOT EXISTS memory_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id STRING REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    namespace STRING NOT NULL,
    memory_type STRING NOT NULL,

    content STRING NOT NULL,
    summary STRING,

    status STRING NOT NULL DEFAULT 'active',

    importance FLOAT8 NOT NULL DEFAULT 0.5,
    confidence FLOAT8 NOT NULL DEFAULT 0.5,

    source_type STRING,
    source_id STRING,

    expires_at TIMESTAMPTZ,
    last_accessed_at TIMESTAMPTZ,
    access_count INT8 NOT NULL DEFAULT 0,

    version INT8 NOT NULL DEFAULT 1,

    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT memory_items_importance_check
        CHECK (importance >= 0 AND importance <= 1),

    CONSTRAINT memory_items_confidence_check
        CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS idx_memory_items_org
ON memory_items (org_id);

CREATE INDEX IF NOT EXISTS idx_memory_items_namespace
ON memory_items (org_id, namespace);

CREATE INDEX IF NOT EXISTS idx_memory_items_type
ON memory_items (org_id, memory_type);

CREATE INDEX IF NOT EXISTS idx_memory_items_updated
ON memory_items (org_id, updated_at);
