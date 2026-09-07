CREATE TABLE IF NOT EXISTS content_packages (
    package_id UUID PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    repository_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    source_event_type TEXT NOT NULL CHECK (source_event_type IN
        ('pull_request', 'release', 'documentation', 'manual_brief', 'feedback_gap')),
    status TEXT NOT NULL CHECK (status IN ('draft', 'in_review', 'approved', 'rejected')),
    brief TEXT NOT NULL,
    source_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_feedback_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_gap_id UUID,
    workflow_run_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, repository_id, source_event_type, source_event_id)
);

CREATE TABLE IF NOT EXISTS content_variants (
    variant_id UUID PRIMARY KEY,
    package_id UUID NOT NULL REFERENCES content_packages(package_id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('blog', 'linkedin', 'x')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'in_review', 'approved', 'rejected')),
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    evaluation JSONB NOT NULL DEFAULT '{}'::jsonb,
    revision_id UUID,
    UNIQUE (package_id, channel, revision_id)
);

CREATE TABLE IF NOT EXISTS content_revisions (
    revision_id UUID PRIMARY KEY,
    package_id UUID NOT NULL REFERENCES content_packages(package_id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    reason TEXT NOT NULL CHECK (reason IN ('initial', 'request_changes', 'regeneration')),
    reviewer_comment TEXT,
    created_by_run_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (package_id, revision_number)
);

CREATE TABLE IF NOT EXISTS content_review_events (
    review_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    package_id UUID NOT NULL REFERENCES content_packages(package_id) ON DELETE CASCADE,
    revision_id UUID,
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'request_changes', 'reject')),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_content_packages_org_status ON content_packages (org_id, status);
CREATE INDEX IF NOT EXISTS idx_content_variants_package ON content_variants (package_id);
