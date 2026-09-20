ALTER TABLE draft_revisions ADD COLUMN IF NOT EXISTS version INTEGER;
ALTER TABLE draft_revisions ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_draft_revisions_run_path_version
    ON draft_revisions (run_id, path, version) WHERE version IS NOT NULL;

CREATE TABLE IF NOT EXISTS documentation_page_states (
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    path TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'pending','writing','evaluating','revising','passed',
        'awaiting_human_review','failed'
    )),
    latest_artifact_id TEXT REFERENCES draft_revisions(id),
    latest_version INTEGER NOT NULL DEFAULT 0 CHECK (latest_version >= 0),
    next_version INTEGER NOT NULL DEFAULT 1 CHECK (next_version >= 1),
    evaluation_attempt INTEGER NOT NULL DEFAULT 0 CHECK (evaluation_attempt >= 0),
    escalation_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, page_id)
);

CREATE TABLE IF NOT EXISTS documentation_page_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES draft_revisions(id),
    version INTEGER NOT NULL CHECK (version >= 1),
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    status TEXT NOT NULL CHECK (status IN ('passed','revision_required','awaiting_human_review')),
    score DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 1),
    metrics JSONB NOT NULL DEFAULT '[]'::jsonb,
    revision_feedback JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, artifact_id)
);

CREATE TABLE IF NOT EXISTS documentation_workflow_tasks (
    run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    task_type TEXT NOT NULL CHECK (task_type IN ('write','evaluate','cross_page_review')),
    page_id TEXT,
    artifact_version INTEGER,
    dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','cancelled')),
    infrastructure_retries INTEGER NOT NULL DEFAULT 0 CHECK (infrastructure_retries BETWEEN 0 AND 1),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    input_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_data JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_documentation_tasks_ready
    ON documentation_workflow_tasks (run_id, status, lease_expires_at);