-- Reusable workflow templates. A NULL org_id denotes a system template.
CREATE TABLE IF NOT EXISTS workflow_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    workflow_key TEXT NOT NULL,
    defaults JSONB NOT NULL DEFAULT '{}'::JSONB,
    is_system BOOL NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT workflow_templates_slug_check
        CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    CONSTRAINT workflow_templates_system_org_check
        CHECK ((is_system AND org_id IS NULL) OR (NOT is_system AND org_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_templates_system_slug
    ON workflow_templates (slug) WHERE is_system;
CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_templates_org_slug
    ON workflow_templates (org_id, slug) WHERE NOT is_system;
CREATE INDEX IF NOT EXISTS idx_workflow_templates_visible
    ON workflow_templates (org_id, is_system, updated_at DESC, id DESC);
