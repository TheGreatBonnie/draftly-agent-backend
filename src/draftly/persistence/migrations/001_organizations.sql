CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    clerk_org_id TEXT NOT NULL UNIQUE,

    clerk_org_name TEXT NOT NULL,

    slack_workspace_id TEXT,

    discord_guild_id TEXT,

    discord_trigger_channels JSONB DEFAULT '[]'::JSONB,

    github_org TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
