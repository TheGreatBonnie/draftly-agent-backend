CREATE TABLE IF NOT EXISTS workflow_events (
    run_id TEXT NOT NULL,
    seq INT NOT NULL,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    type TEXT NOT NULL,
    node_id TEXT,
    payload JSONB NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_workflow_events_run ON workflow_events (run_id, seq);
