"""Static checks for the additive Knowledge read indexes."""

from pathlib import Path


def test_knowledge_read_indexes_are_idempotent_and_org_first() -> None:
    migration = Path("src/draftly/persistence/migrations/057_knowledge_read_indexes.sql")
    sql = migration.read_text()

    assert sql.count("CREATE INDEX IF NOT EXISTS") == 3
    assert "ON memory_items (org_id, namespace, updated_at DESC, id DESC)" in sql
    assert "ON memory_links (org_id, source_memory_id, target_memory_id)" in sql
    assert "ON memory_feedback (org_id, memory_item_id, created_at DESC)" in sql
