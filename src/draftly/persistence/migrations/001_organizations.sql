CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    clerk_org_id STRING NOT NULL UNIQUE,

    clerk_org_name STRING NOT NULL,

    slack_workspace_id STRING,

    discord_guild_id STRING,

    discord_trigger_channels JSONB DEFAULT '[]'::JSONB,

    github_org STRING,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
