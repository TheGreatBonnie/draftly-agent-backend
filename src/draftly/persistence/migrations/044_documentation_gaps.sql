CREATE TABLE IF NOT EXISTS documentation_gaps (
    gap_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT NOT NULL REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 0,
    severity DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    platforms JSONB NOT NULL DEFAULT '[]'::jsonb,
    sample_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome TEXT NOT NULL DEFAULT 'pending'
        CHECK (outcome IN ('documentation', 'content', 'both', 'pending')),
    dispatch_route TEXT,
    dispatch_run_id TEXT,
    dispatched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, topic)
);

CREATE INDEX IF NOT EXISTS idx_documentation_gaps_org_outcome
    ON documentation_gaps (org_id, outcome);
