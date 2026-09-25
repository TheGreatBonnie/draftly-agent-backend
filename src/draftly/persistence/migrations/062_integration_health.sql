CREATE TABLE IF NOT EXISTS integration_health (
    org_id TEXT NOT NULL REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('healthy', 'degraded', 'error')),
    message TEXT,
    sources JSONB NOT NULL DEFAULT '[]'::JSONB,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, provider, connection_id)
);

CREATE TABLE IF NOT EXISTS integration_oauth_states (
    nonce TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    org_id TEXT NOT NULL REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    return_to TEXT NOT NULL,
    installation_id BIGINT,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_integration_oauth_expires ON integration_oauth_states(expires_at);
