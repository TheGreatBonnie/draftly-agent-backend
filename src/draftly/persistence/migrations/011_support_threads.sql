CREATE TABLE IF NOT EXISTS support_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    thread_id TEXT NOT NULL,
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    platform TEXT NOT NULL,

    external_id TEXT,
    channel_id TEXT NOT NULL,
    channel_name TEXT,
    root_message_id TEXT,

    question TEXT,
    answer TEXT,

    status TEXT NOT NULL DEFAULT 'open',
    resolved BOOL NOT NULL DEFAULT false,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    raw JSONB,

    UNIQUE (thread_id, platform)
);

CREATE TABLE IF NOT EXISTS support_messages (
    message_id TEXT NOT NULL,
    platform TEXT NOT NULL,

    channel_id TEXT NOT NULL,
    channel_name TEXT,

    author_id TEXT,
    author_name TEXT,

    content TEXT NOT NULL,

    thread_id TEXT,

    timestamp TIMESTAMPTZ NOT NULL,

    url TEXT,

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
ADD COLUMN IF NOT EXISTS org_id TEXT
    REFERENCES organizations(clerk_org_id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_support_messages_org_id
ON support_messages (org_id);
