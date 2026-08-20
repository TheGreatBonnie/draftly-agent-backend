CREATE TABLE IF NOT EXISTS delivery_plans (
    id UUID PRIMARY KEY,
    repository_id STRING NOT NULL,
    repository_path STRING NOT NULL,
    summary STRING NOT NULL,
    status STRING NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delivery_plans_repository
ON delivery_plans (repository_id);


CREATE TABLE IF NOT EXISTS delivery_commits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id STRING NOT NULL,
    branch STRING NOT NULL,
    commit_sha STRING NOT NULL,
    message STRING NOT NULL,
    files JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delivery_commits_repository
ON delivery_commits (repository_id);


CREATE TABLE IF NOT EXISTS delivery_pull_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id STRING NOT NULL,
    owner STRING NOT NULL,
    repository STRING NOT NULL,
    number INT8 NOT NULL,
    url STRING NOT NULL,
    title STRING NOT NULL,
    branch STRING NOT NULL,
    base_branch STRING NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delivery_pull_requests_repository
ON delivery_pull_requests (repository_id);

CREATE INDEX IF NOT EXISTS idx_delivery_pull_requests_number
ON delivery_pull_requests (
    repository_id,
    number
);

-- Add org_id for multi-tenant data isolation
ALTER TABLE delivery_plans
ADD COLUMN IF NOT EXISTS org_id STRING
    REFERENCES organizations(clerk_org_id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_delivery_plans_org_id
ON delivery_plans (org_id);

ALTER TABLE delivery_commits
ADD COLUMN IF NOT EXISTS org_id STRING
    REFERENCES organizations(clerk_org_id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_delivery_commits_org_id
ON delivery_commits (org_id);

ALTER TABLE delivery_pull_requests
ADD COLUMN IF NOT EXISTS org_id STRING
    REFERENCES organizations(clerk_org_id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_delivery_pull_requests_org_id
ON delivery_pull_requests (org_id);
