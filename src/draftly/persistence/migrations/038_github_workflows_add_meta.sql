ALTER TABLE github_workflows ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE github_workflows ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT '';
ALTER TABLE github_workflows ADD COLUMN IF NOT EXISTS actor TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_github_workflows_run_id ON github_workflows (run_id);
