CREATE TABLE IF NOT EXISTS documentation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    repository TEXT,

    path TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,

    document_type TEXT NOT NULL DEFAULT 'general',

    version INT8 NOT NULL DEFAULT 1,
    commit_sha TEXT,

    status TEXT NOT NULL DEFAULT 'draft',

    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,

    stale BOOL NOT NULL DEFAULT false,
    outdated BOOL NOT NULL DEFAULT false,
    incomplete BOOL NOT NULL DEFAULT false,
    broken_links BOOL NOT NULL DEFAULT false,
    unsupported_claims BOOL NOT NULL DEFAULT false,

    source_hash TEXT,
    last_verified_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (org_id, path)
);

CREATE INDEX IF NOT EXISTS idx_documentation_org
ON documentation (org_id);

CREATE INDEX IF NOT EXISTS idx_documentation_repository
ON documentation (repository);
