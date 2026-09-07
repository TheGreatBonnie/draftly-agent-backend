CREATE TABLE IF NOT EXISTS feedback_outcomes (
    outcome_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT NOT NULL REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    outcome JSONB NOT NULL DEFAULT '{}'::jsonb,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_feedback_outcomes_org_unresolved
    ON feedback_outcomes (org_id, resolved_at, created_at);
