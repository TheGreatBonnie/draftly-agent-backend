CREATE TABLE IF NOT EXISTS documentation_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documentation(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    revision_number INT8 NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('manual', 'restore')),
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'superseded', 'discarded')),
    title TEXT,
    content TEXT NOT NULL,
    base_source_hash TEXT,
    base_document_updated_at TIMESTAMPTZ,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (document_id, revision_number)
);

ALTER TABLE documentation
    ADD COLUMN IF NOT EXISTS draft_revision_id UUID
    REFERENCES documentation_revisions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_doc_revisions_org_document
    ON documentation_revisions (org_id, document_id, revision_number DESC);

CREATE INDEX IF NOT EXISTS idx_doc_revisions_created
    ON documentation_revisions (org_id, created_at DESC);
