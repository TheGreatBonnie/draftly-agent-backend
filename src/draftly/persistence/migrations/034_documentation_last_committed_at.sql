-- 034_documentation_last_committed_at.sql
-- Stores the GitHub commit date of the most recent commit that touched
-- each document file. Used by the health report freshness dimension.

ALTER TABLE documentation
ADD COLUMN IF NOT EXISTS last_committed_at TIMESTAMPTZ DEFAULT NULL;
