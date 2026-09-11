-- Agent run metadata needed by the dynamic agents dashboard.

ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS surface TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS workflow_key TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS definition_id UUID;

CREATE INDEX IF NOT EXISTS idx_agent_runs_org_started
    ON agent_runs (org_id, started_at DESC, run_id);

CREATE INDEX IF NOT EXISTS idx_agent_runs_org_surface_started
    ON agent_runs (org_id, surface, started_at DESC, run_id);
