CREATE TABLE IF NOT EXISTS embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    content_type TEXT NOT NULL,
    content_id TEXT NOT NULL,

    workflow_id TEXT,

    embedding vector(1536) NOT NULL,

    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS embeddings_embedding_idx
ON embeddings USING hnsw (embedding vector_cosine_ops);
