-- Agent run audit trail (plan §10.2).
-- One agent_runs row per workflow run; one agent_steps row per
-- node/tool execution within the run.

CREATE TABLE IF NOT EXISTS agent_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    run_id TEXT NOT NULL UNIQUE,

    source TEXT NOT NULL DEFAULT 'github',

    event_type TEXT NOT NULL DEFAULT 'unknown',

    org_id TEXT NOT NULL DEFAULT '',

    status TEXT NOT NULL DEFAULT 'running',

    error TEXT,

    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_run_id ON agent_runs (run_id);

CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs (status, started_at DESC);

CREATE TABLE IF NOT EXISTS agent_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    run_id TEXT NOT NULL,

    seq INT NOT NULL DEFAULT 0,

    kind TEXT NOT NULL DEFAULT 'node',

    name TEXT NOT NULL,

    status TEXT NOT NULL DEFAULT 'completed',

    duration_ms INT,

    detail JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_steps_run_id ON agent_steps (run_id, seq);
