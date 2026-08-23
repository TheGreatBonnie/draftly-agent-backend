-- Connected repository configuration captured during onboarding.
CREATE TABLE IF NOT EXISTS repositories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    full_name TEXT NOT NULL,
    default_branch TEXT NOT NULL DEFAULT 'main',
    doc_include JSONB DEFAULT '["README.md", "docs/**", "*.md", "*.mdx"]'::JSONB,
    doc_exclude JSONB DEFAULT '["node_modules/**", "dist/**"]'::JSONB,
    installation_id INT REFERENCES github_installations(installation_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, full_name)
);
