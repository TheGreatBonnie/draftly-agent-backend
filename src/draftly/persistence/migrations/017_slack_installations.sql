CREATE TABLE IF NOT EXISTS slack_installations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id STRING,

    team_id STRING NOT NULL,

    team_name STRING,

    bot_user_id STRING,
    bot_token STRING NOT NULL,
    bot_scopes STRING,

    user_id STRING NOT NULL,
    user_token STRING,
    user_scopes STRING,

    token_type STRING,

    installed_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now() ON UPDATE now(),

    UNIQUE (team_id)
);

CREATE INDEX IF NOT EXISTS idx_slack_installations_org
ON slack_installations(org_id);

CREATE INDEX IF NOT EXISTS idx_slack_installations_team
ON slack_installations(team_id);
