CREATE TABLE IF NOT EXISTS slack_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    workflow_id TEXT NOT NULL UNIQUE,

    channel_id TEXT NOT NULL,
    thread_ts TEXT,
    source_message TEXT,

    status TEXT NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_slack_workflows_org
ON slack_workflows (org_id);

CREATE INDEX IF NOT EXISTS idx_slack_workflows_status
ON slack_workflows (status);
