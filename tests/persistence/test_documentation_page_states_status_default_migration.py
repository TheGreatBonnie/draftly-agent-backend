"""Migration 061: give documentation_page_states.status a default.

The page-workflow seed INSERT originally omitted the `status` column, and the
column is NOT NULL with no default, so every seed row violated the constraint
(`NotNullViolationError` → `page_workflow_failed`). The code now supplies
`'pending'` explicitly; this migration gives already-deployed databases the
same safety net.
"""

from pathlib import Path


def _migration() -> str:
    path = (
        Path(__file__).parents[2]
        / "src/draftly/persistence/migrations/061_documentation_page_states_status_default.sql"
    )
    assert path.exists(), (
        "061_documentation_page_states_status_default.sql is required so the "
        "page-workflow seed cannot insert a NULL page status"
    )
    return path.read_text(encoding="utf-8").lower()


def test_migration_sets_status_default_to_pending() -> None:
    sql = _migration()
    assert "alter table documentation_page_states" in sql
    assert "alter column status set default 'pending'" in sql


def test_migration_is_additive() -> None:
    sql = _migration()
    assert "drop table" not in sql
    assert "drop column" not in sql
    assert "drop constraint" not in sql
    assert "drop default" not in sql
