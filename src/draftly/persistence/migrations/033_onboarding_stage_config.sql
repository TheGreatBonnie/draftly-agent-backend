-- 033_onboarding_stage_config.sql
-- Adds stage_config JSONB column to store workflow stage definitions.
-- Written at workspace creation time so stage config is available on the
-- initialize page before SSE connects.

ALTER TABLE onboarding_state
ADD COLUMN IF NOT EXISTS stage_config JSONB DEFAULT NULL;
