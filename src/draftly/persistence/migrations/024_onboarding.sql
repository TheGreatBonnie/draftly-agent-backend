-- Onboarding state machine, one row per org.
CREATE TABLE IF NOT EXISTS onboarding_state (
    org_id TEXT PRIMARY KEY REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'NOT_STARTED',
    completed_steps JSONB NOT NULL DEFAULT '[]'::JSONB,
    failure JSONB,
    selected_repository JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
