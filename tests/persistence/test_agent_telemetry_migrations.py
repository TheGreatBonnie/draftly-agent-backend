from __future__ import annotations

from pathlib import Path


MIGRATIONS = Path(__file__).parents[2] / "src/draftly/persistence/migrations"


def _sql(name: str) -> str:
    return (MIGRATIONS / name).read_text()


def test_agent_run_surface_migration_is_additive_and_indexed() -> None:
    sql = _sql("052_agent_run_surface.sql")

    assert "ADD COLUMN IF NOT EXISTS surface TEXT NOT NULL DEFAULT ''" in sql
    assert "ADD COLUMN IF NOT EXISTS workflow_key TEXT" in sql
    assert "ADD COLUMN IF NOT EXISTS definition_id UUID" in sql
    assert "idx_agent_runs_org_started" in sql
    assert "DROP TABLE" not in sql.upper()
    assert "DROP COLUMN" not in sql.upper()


def test_agent_step_identity_migration_is_additive_and_indexed() -> None:
    sql = _sql("053_agent_step_identity.sql")

    assert "ADD COLUMN IF NOT EXISTS agent_id TEXT" in sql
    assert "ADD COLUMN IF NOT EXISTS node_id TEXT" in sql
    assert "ADD COLUMN IF NOT EXISTS surface TEXT NOT NULL DEFAULT ''" in sql
    assert "idx_agent_steps_run_seq" in sql
    assert "idx_agent_steps_agent_run_seq" in sql
    assert "DROP TABLE" not in sql.upper()
    assert "DROP COLUMN" not in sql.upper()
