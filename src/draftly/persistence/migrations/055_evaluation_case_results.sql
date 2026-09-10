CREATE TABLE IF NOT EXISTS evaluation_case_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT NOT NULL REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    evaluation_id UUID NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    dataset TEXT NOT NULL,
    case_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    threshold FLOAT8,
    score FLOAT8,
    passed BOOL NOT NULL DEFAULT false,
    reason TEXT NOT NULL DEFAULT '',
    input TEXT,
    expected_output TEXT,
    actual_output TEXT,
    evidence JSONB NOT NULL DEFAULT '[]'::JSONB,
    trace_id TEXT,
    duration_ms INT8 CHECK (duration_ms IS NULL OR duration_ms >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (score IS NULL OR (score >= 0 AND score <= 100)),
    CHECK (threshold IS NULL OR (threshold >= 0 AND threshold <= 100)),
    UNIQUE (evaluation_id, dataset, case_id, metric)
);

CREATE INDEX IF NOT EXISTS idx_evaluation_case_results_org_run
    ON evaluation_case_results (org_id, run_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_evaluation_case_results_org_evaluation
    ON evaluation_case_results (org_id, evaluation_id, created_at DESC, id DESC);
