CREATE TABLE IF NOT EXISTS episodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    agent_run_id UUID,
    trigger_type TEXT NOT NULL,
    trigger_id TEXT,
    trigger_summary TEXT NOT NULL,
    actions_taken JSONB NOT NULL DEFAULT '[]'::JSONB,
    tools_used TEXT[] NOT NULL DEFAULT '{}',
    outcome TEXT NOT NULL CHECK (outcome IN ('success','failure','partial')),
    evaluation_results JSONB,
    artifacts_created JSONB NOT NULL DEFAULT '[]'::JSONB,
    summary TEXT,
    embedding VECTOR(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_episodes_org_trigger
ON episodes (org_id, trigger_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_episodes_embedding
ON episodes USING hnsw (embedding vector_cosine_ops);
