"""§steering Task 3: additive steering migration contract checks."""

from pathlib import Path


def _migration() -> str:
    path = (
        Path(__file__).parents[2]
        / "src/draftly/persistence/migrations/057_agent_steering.sql"
    )
    assert path.exists(), "057_agent_steering.sql is required for steering persistence"
    return path.read_text(encoding="utf-8").lower()


def test_steering_migration_defines_intervention_and_attempt_tables() -> None:
    sql = _migration()
    assert "workflow_interventions" in sql
    assert "steering_attempts" in sql


def test_steering_migration_extends_workflow_runs_status_check() -> None:
    sql = _migration()
    assert "workflow_runs_status_check" in sql
    assert "drop constraint if exists workflow_runs_status_check" in sql
    assert "pending_intervention" in sql


def test_steering_migration_intervention_unique_constraints() -> None:
    sql = _migration()
    assert "unique (run_id, interrupt_id)" in sql
    assert "unique (org_id, idempotency_key)" in sql


def test_steering_migration_intervention_statuses() -> None:
    sql = _migration()
    for status in ("pending", "approved", "denied", "guided", "expired", "cancelled"):
        assert status in sql


def test_steering_migration_attempt_primary_key_and_reset() -> None:
    sql = _migration()
    assert "primary key (run_id, agent_id, node_id, phase, tool_name, model_turn)" in sql
    assert "create index" in sql
    assert "drop table" not in sql
    assert "drop column" not in sql