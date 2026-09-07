ALTER TABLE slack_workflows ADD COLUMN IF NOT EXISTS source_message_id TEXT;
ALTER TABLE slack_workflows ADD COLUMN IF NOT EXISTS provider_message_id TEXT;
ALTER TABLE slack_workflows ADD COLUMN IF NOT EXISTS delivery_error TEXT;
ALTER TABLE slack_workflows ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;
ALTER TABLE discord_workflows ADD COLUMN IF NOT EXISTS source_message_id TEXT;
ALTER TABLE discord_workflows ADD COLUMN IF NOT EXISTS provider_message_id TEXT;
ALTER TABLE discord_workflows ADD COLUMN IF NOT EXISTS delivery_error TEXT;
ALTER TABLE discord_workflows ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_slack_workflows_org_status ON slack_workflows (org_id, status);
CREATE INDEX IF NOT EXISTS idx_discord_workflows_org_status ON discord_workflows (org_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_slack_workflows_org_source
    ON slack_workflows (org_id, source_message_id)
    WHERE source_message_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_discord_workflows_org_source
    ON discord_workflows (org_id, source_message_id)
    WHERE source_message_id IS NOT NULL;