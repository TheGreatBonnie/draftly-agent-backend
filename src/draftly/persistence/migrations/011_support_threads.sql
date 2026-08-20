CREATE TABLE IF NOT EXISTS support_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    thread_id STRING NOT NULL,
    org_id STRING REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    platform STRING NOT NULL,

    external_id STRING,
    channel_id STRING NOT NULL,
    channel_name STRING,
    root_message_id STRING,

    question STRING,
    answer STRING,

    status STRING NOT NULL DEFAULT 'open',
    resolved BOOL NOT NULL DEFAULT false,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    raw JSONB,

    UNIQUE (thread_id, platform)
);

CREATE TABLE IF NOT EXISTS support_messages (
    message_id STRING NOT NULL,
    platform STRING NOT NULL,

    channel_id STRING NOT NULL,
    channel_name STRING,

    author_id STRING,
    author_name STRING,

    content STRING NOT NULL,

    thread_id STRING,

    timestamp TIMESTAMPTZ NOT NULL,

    url STRING,

    raw JSONB,

    PRIMARY KEY (message_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_support_messages_thread
ON support_messages (thread_id);

CREATE INDEX IF NOT EXISTS idx_support_messages_channel
ON support_messages (platform, channel_id);

CREATE INDEX IF NOT EXISTS idx_support_messages_timestamp
ON support_messages (timestamp DESC);

-- Add org_id for multi-tenant data isolation on messages
ALTER TABLE support_messages
ADD COLUMN IF NOT EXISTS org_id STRING
    REFERENCES organizations(clerk_org_id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_support_messages_org_id
ON support_messages (org_id);
