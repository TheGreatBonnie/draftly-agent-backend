CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    job_type TEXT NOT NULL,

    status TEXT NOT NULL DEFAULT 'pending',

    scheduled_for TIMESTAMPTZ,

    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    attempts INT8 NOT NULL DEFAULT 0,

    payload JSONB NOT NULL DEFAULT '{}'::JSONB,

    name TEXT NOT NULL DEFAULT '',
    schedule TEXT NOT NULL DEFAULT '',
    configuration JSONB NOT NULL DEFAULT '{}'::JSONB,

    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,

    result JSONB,

    error TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_status
ON jobs (status);

CREATE INDEX IF NOT EXISTS idx_jobs_scheduled
ON jobs (status, scheduled_for);
