-- DB-backed Strands session storage (layer 1B of the resume-session fix).
--
-- Interrupted graph state must survive process restarts and multi-instance
-- deployments so human review decisions can resume the paused workflow from
-- any host. Strands' session-manager CRUD is flattened into JSONB blobs
-- keyed by a composite ``key``:
--
--   session:{session_id}
--   agent:{session_id}:{agent_id}
--   message:{session_id}:{agent_id}:{message_id}
--   multi_agent:{session_id}:{multi_agent_id}
--
-- Requires an existing migration runner (scripts/bootstrap.py) to apply.

CREATE TABLE IF NOT EXISTS draftly_sessions (
    key        TEXT        PRIMARY KEY,
    session_id TEXT        NOT NULL,
    payload    JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_draftly_sessions_session_id
    ON draftly_sessions (session_id);