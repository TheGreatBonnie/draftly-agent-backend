-- Agent steering persistence: bounded attempt counters and durable human
-- interventions. Additive only; no existing rows are rewritten.

ALTER TABLE workflow_runs DROP CONSTRAINT IF EXISTS workflow_runs_status_check;
ALTER TABLE workflow_runs ADD CONSTRAINT workflow_runs_status_check CHECK (
    status IN ('queued', 'running', 'pending_review', 'pending_intervention',
               'completed', 'failed', 'cancelled', 'skipped')
);

CREATE TABLE IF NOT EXISTS steering_attempts (
    run_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    tool_name TEXT NOT NULL DEFAULT '',
    model_turn INTEGER NOT NULL DEFAULT 0,
    guide_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, agent_id, node_id, phase, tool_name, model_turn)
);

CREATE TABLE IF NOT EXISTS workflow_interventions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id TEXT NOT NULL REFERENCES workflow_runs(id),
    interrupt_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    workflow_key TEXT,
    agent_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied',
        'guided', 'expired', 'cancelled')),
    reason JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key TEXT,
    resolver_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    UNIQUE (run_id, interrupt_id),
    UNIQUE (org_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_steering_interventions_org_status_created
    ON workflow_interventions (org_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_steering_interventions_run_status
    ON workflow_interventions (run_id, status);