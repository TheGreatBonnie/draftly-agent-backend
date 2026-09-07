ALTER TABLE github_workflows
ADD COLUMN IF NOT EXISTS event_type TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_github_workflows_event_type
ON github_workflows (event_type);
