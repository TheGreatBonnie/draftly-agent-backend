CREATE TABLE IF NOT EXISTS slack_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id STRING REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    workflow_id STRING NOT NULL UNIQUE,

    channel_id STRING NOT NULL,
    thread_ts STRING,
    source_message STRING,

    status STRING NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_slack_workflows_org
ON slack_workflows (org_id);

CREATE INDEX IF NOT EXISTS idx_slack_workflows_status
ON slack_workflows (status);
