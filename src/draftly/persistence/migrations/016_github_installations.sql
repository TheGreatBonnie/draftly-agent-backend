CREATE TABLE IF NOT EXISTS github_installations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    installation_id INT NOT NULL UNIQUE,

    github_org TEXT NOT NULL,

    repositories JSONB DEFAULT '[]'::JSONB,

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now() ON UPDATE now()
);

CREATE INDEX IF NOT EXISTS idx_installations_org
ON github_installations(org_id);

CREATE INDEX IF NOT EXISTS idx_installations_github_org
ON github_installations(github_org);
