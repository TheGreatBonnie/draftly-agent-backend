CREATE TABLE IF NOT EXISTS model_performance (
    id BIGSERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,
    task_type TEXT NOT NULL,
    sample_count INTEGER DEFAULT 0,
    mean_latency_ms DOUBLE PRECISION DEFAULT 0.0,
    variance_latency_ms DOUBLE PRECISION DEFAULT 0.0,
    success_rate DOUBLE PRECISION DEFAULT 1.0,
    p50_latency_ms DOUBLE PRECISION DEFAULT 0.0,
    p95_latency_ms DOUBLE PRECISION DEFAULT 0.0,
    quality_ema DOUBLE PRECISION,
    approval_rate DOUBLE PRECISION,          -- human review signal (reference §16)
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(model_name, task_type)            -- per-task stats (reference §13)
);

CREATE INDEX idx_model_performance_lookup ON model_performance(task_type, model_name);
