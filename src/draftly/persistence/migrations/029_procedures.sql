CREATE TABLE IF NOT EXISTS procedures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    pattern_description TEXT NOT NULL,
    trigger_conditions JSONB NOT NULL DEFAULT '{}'::JSONB,
    steps JSONB NOT NULL DEFAULT '[]'::JSONB,
    applicability_context TEXT,
    success_count INT8 NOT NULL DEFAULT 0,
    failure_count INT8 NOT NULL DEFAULT 0,
    confidence FLOAT8 NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','archived')),
    embedding VECTOR(1536),
    last_applied_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT procedures_confidence_check CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS idx_procedures_org_status ON procedures (org_id, status);

CREATE INDEX IF NOT EXISTS idx_procedures_embedding
ON procedures USING hnsw (embedding vector_cosine_ops);
