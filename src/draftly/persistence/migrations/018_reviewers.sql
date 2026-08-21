CREATE TABLE IF NOT EXISTS reviewers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    name TEXT NOT NULL,

    email TEXT,
    slack_user_id TEXT,
    discord_user_id TEXT,
    clerk_user_id TEXT,

    notify_slack BOOL DEFAULT true,
    notify_discord BOOL DEFAULT false,
    notify_email BOOL DEFAULT false,

    is_active BOOLEAN DEFAULT true,

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now() ON UPDATE now()
);

CREATE INDEX IF NOT EXISTS idx_reviewers_org
ON reviewers(org_id);

CREATE INDEX IF NOT EXISTS idx_reviewers_active
ON reviewers(is_active);

CREATE INDEX IF NOT EXISTS idx_reviewers_clerk_user
ON reviewers(clerk_user_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reviewers_email_org
ON reviewers(org_id, email)
WHERE email IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_reviewers_slack_org
ON reviewers(org_id, slack_user_id)
WHERE slack_user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_reviewers_discord_org
ON reviewers(org_id, discord_user_id)
WHERE discord_user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_reviewers_clerk_user_org
ON reviewers(org_id, clerk_user_id)
WHERE clerk_user_id IS NOT NULL;
