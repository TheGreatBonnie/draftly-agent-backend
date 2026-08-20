CREATE TABLE IF NOT EXISTS github_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id STRING REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,

    workflow_id STRING NOT NULL UNIQUE,

    installation_id INT NOT NULL,
    owner STRING NOT NULL,
    repo STRING NOT NULL,
    issue_number INT8 NOT NULL,

    status STRING NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_github_workflows_org
ON github_workflows (org_id);

CREATE INDEX IF NOT EXISTS idx_github_workflows_status
ON github_workflows (status);

CREATE INDEX IF NOT EXISTS idx_github_workflows_issue
ON github_workflows (owner, repo, issue_number);
