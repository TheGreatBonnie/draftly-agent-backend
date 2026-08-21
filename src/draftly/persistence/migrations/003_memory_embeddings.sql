CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memory_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    memory_item_id UUID NOT NULL
        REFERENCES memory_items (id)
        ON DELETE CASCADE,

    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    embedding vector(1536) NOT NULL,

    model TEXT NOT NULL,
    dimensions INT8 NOT NULL,
    content_hash TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memory_embeddings_item
ON memory_embeddings (memory_item_id);

CREATE INDEX IF NOT EXISTS idx_memory_embeddings_vector
ON memory_embeddings USING hnsw (embedding vector_cosine_ops);
