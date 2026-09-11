-- Canonical reusable workflow definitions. Provider-specific workflow tables
-- remain compatibility/read-model tables during the resource migration.
CREATE TABLE IF NOT EXISTS workflow_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    workflow_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    version INT NOT NULL DEFAULT 1,
    trigger_config JSONB NOT NULL DEFAULT '{}'::JSONB,
    condition_config JSONB NOT NULL DEFAULT '{}'::JSONB,
    agent_config JSONB NOT NULL DEFAULT '{}'::JSONB,
    repository_config JSONB NOT NULL DEFAULT '{}'::JSONB,
    evaluation_config JSONB NOT NULL DEFAULT '{}'::JSONB,
    review_config JSONB NOT NULL DEFAULT '{}'::JSONB,
    delivery_config JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT workflow_definitions_status_check
        CHECK (status IN ('active', 'paused', 'draft', 'archived')),
    CONSTRAINT workflow_definitions_version_check CHECK (version > 0),
    CONSTRAINT workflow_definitions_slug_check
        CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    CONSTRAINT workflow_definitions_org_slug_unique UNIQUE (org_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_workflow_definitions_org_status_updated
    ON workflow_definitions (org_id, status, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_definitions_org_key_updated
    ON workflow_definitions (org_id, workflow_key, updated_at DESC, id DESC);
