-- Canonical execution instances. agent_runs/agent_steps and workflow_events
-- remain the detailed audit and streaming stores for these instances.
CREATE TABLE IF NOT EXISTS workflow_runs (
    id TEXT PRIMARY KEY,
    definition_id UUID REFERENCES workflow_definitions(id) ON DELETE SET NULL,
    org_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    source_event_id TEXT,
    event_type TEXT,
    title TEXT,
    repository TEXT,
    actor TEXT,
    target JSONB,
    status TEXT NOT NULL DEFAULT 'queued',
    current_stage TEXT,
    stage_states JSONB NOT NULL DEFAULT '{}'::JSONB,
    input_data JSONB,
    output_data JSONB,
    error TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT workflow_runs_status_check
        CHECK (status IN ('queued', 'running', 'pending_review', 'completed',
                          'failed', 'cancelled', 'skipped'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_runs_source_event
    ON workflow_runs (org_id, source, source_event_id)
    WHERE source_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_workflow_runs_org_created
    ON workflow_runs (org_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_org_status_created
    ON workflow_runs (org_id, status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_definition_created
    ON workflow_runs (definition_id, created_at DESC, id DESC);
