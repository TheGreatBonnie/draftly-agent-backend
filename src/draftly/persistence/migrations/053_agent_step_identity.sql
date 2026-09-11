-- Stable agent and graph-node identity for new step telemetry.

ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS agent_id TEXT;
ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS node_id TEXT;
ALTER TABLE agent_steps ADD COLUMN IF NOT EXISTS surface TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_agent_steps_run_seq
    ON agent_steps (run_id, seq, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_steps_agent_run_seq
    ON agent_steps (agent_id, run_id, seq, created_at);
