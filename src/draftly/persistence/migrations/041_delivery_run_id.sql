ALTER TABLE delivery_plans ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE delivery_commits ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE delivery_pull_requests ADD COLUMN IF NOT EXISTS run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_delivery_pull_requests_run_id
ON delivery_pull_requests (run_id);
