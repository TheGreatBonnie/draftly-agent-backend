CREATE TABLE IF NOT EXISTS embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id STRING REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    content_type STRING NOT NULL,
    content_id STRING NOT NULL,

    workflow_id STRING,

    embedding VECTOR(3072) NOT NULL,

    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE VECTOR INDEX IF NOT EXISTS embeddings_embedding_idx
ON embeddings (embedding vector_cosine_ops);
