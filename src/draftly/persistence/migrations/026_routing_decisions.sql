CREATE TABLE IF NOT EXISTS routing_decisions (
    id BIGSERIAL PRIMARY KEY,
    request_id TEXT NOT NULL,
    organization_id TEXT,
    task_type TEXT NOT NULL,
    selected_model TEXT NOT NULL,
    provider TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    candidates_considered INTEGER NOT NULL,
    profile TEXT NOT NULL,
    reason_codes JSONB DEFAULT '[]'::JSONB,
    fallback_chain JSONB DEFAULT '[]'::JSONB,
    estimated_cost DOUBLE PRECISION,
    estimated_latency_ms DOUBLE PRECISION,
    actual_cost DOUBLE PRECISION,
    latency_ms DOUBLE PRECISION,
    success BOOLEAN,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_routing_decisions_task_type ON routing_decisions(task_type);
CREATE INDEX idx_routing_decisions_created_at ON routing_decisions(created_at);
