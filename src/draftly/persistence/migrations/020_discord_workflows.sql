CREATE TABLE IF NOT EXISTS discord_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id STRING REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    workflow_id STRING NOT NULL UNIQUE,

    channel_id STRING NOT NULL,
    message_id STRING,
    thread_id STRING,
    source_message STRING,

    status STRING NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_discord_workflows_org
ON discord_workflows (org_id);

CREATE INDEX IF NOT EXISTS idx_discord_workflows_status
ON discord_workflows (status);
