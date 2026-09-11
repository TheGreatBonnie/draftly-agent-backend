CREATE INDEX IF NOT EXISTS idx_memory_items_knowledge_page
    ON memory_items (org_id, namespace, updated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_memory_links_org_source_target
    ON memory_links (org_id, source_memory_id, target_memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_feedback_org_item_created
    ON memory_feedback (org_id, memory_item_id, created_at DESC);
