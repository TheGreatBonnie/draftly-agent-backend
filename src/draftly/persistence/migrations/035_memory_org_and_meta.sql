-- 035_memory_org_and_meta.sql
-- Org-scoped vector lookup and metadata deletes for onboarding init.

CREATE INDEX IF NOT EXISTS idx_memory_embeddings_org
    ON memory_embeddings (org_id);

CREATE INDEX IF NOT EXISTS idx_memory_items_org_namespace
    ON memory_items (org_id, namespace);

-- Enables bulk DELETE ... WHERE metadata->>'document_id' = $1 (Task 5).
CREATE INDEX IF NOT EXISTS idx_memory_items_metadata_gin
    ON memory_items USING GIN (metadata);
