CREATE TABLE IF NOT EXISTS discord_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    workflow_id TEXT NOT NULL UNIQUE,

    channel_id TEXT NOT NULL,
    message_id TEXT,
    thread_id TEXT,
    source_message TEXT,

    status TEXT NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_discord_workflows_org
ON discord_workflows (org_id);

CREATE INDEX IF NOT EXISTS idx_discord_workflows_status
ON discord_workflows (status);
