CREATE TABLE IF NOT EXISTS evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id STRING REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    evaluation_type STRING NOT NULL,

    run_id STRING,
    case_id STRING,
    metric STRING,

    target_type STRING,
    target_id UUID,

    score FLOAT8,
    threshold FLOAT8,
    passed BOOL NOT NULL DEFAULT false,

    status STRING NOT NULL DEFAULT 'completed',

    metrics JSONB NOT NULL DEFAULT '{}'::JSONB,
    failures JSONB NOT NULL DEFAULT '[]'::JSONB,

    trace_id STRING,

    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evaluations_org
ON evaluations (org_id);

CREATE INDEX IF NOT EXISTS idx_evaluations_created
ON evaluations (org_id, created_at);
