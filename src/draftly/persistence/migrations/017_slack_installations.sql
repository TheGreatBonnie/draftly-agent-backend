CREATE TABLE IF NOT EXISTS slack_installations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id TEXT,

    team_id TEXT NOT NULL,

    team_name TEXT,

    bot_user_id TEXT,
    bot_token TEXT NOT NULL,
    bot_scopes TEXT,

    user_id TEXT NOT NULL,
    user_token TEXT,
    user_scopes TEXT,

    token_type TEXT,

    installed_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now() ON UPDATE now(),

    UNIQUE (team_id)
);

CREATE INDEX IF NOT EXISTS idx_slack_installations_org
ON slack_installations(org_id);

CREATE INDEX IF NOT EXISTS idx_slack_installations_team
ON slack_installations(team_id);
