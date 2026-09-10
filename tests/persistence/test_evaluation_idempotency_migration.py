from pathlib import Path


def test_evaluation_idempotency_migration_is_additive_and_unique() -> None:
    path = (
        Path(__file__).parents[2]
        / "src/draftly/persistence/migrations/056_evaluation_idempotency.sql"
    )
    sql = path.read_text(encoding="utf-8").lower()

    assert "create table if not exists evaluation_run_idempotency" in sql
    assert "unique (org_id, idempotency_key)" in sql
    assert "create index" in sql
    assert "drop table" not in sql
