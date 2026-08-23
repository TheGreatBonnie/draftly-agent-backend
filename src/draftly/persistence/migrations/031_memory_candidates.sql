CREATE TABLE IF NOT EXISTS memory_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    candidate_type TEXT NOT NULL CHECK (candidate_type IN
        ('fact','decision','procedure_pattern','doc_relation','episode_summary')),
    payload JSONB NOT NULL,
    source_type TEXT,
    source_id TEXT,
    evidence JSONB NOT NULL DEFAULT '[]'::JSONB,
    confidence FLOAT8 NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processing','applied','rejected')),
    decision_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_memory_candidates_pending
ON memory_candidates (status, created_at) WHERE status = 'pending';
