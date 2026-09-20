"""Task 1: additive documentation page-workflow migration contract checks."""

from pathlib import Path


def _migration() -> str:
    path = (
        Path(__file__).parents[2]
        / "src/draftly/persistence/migrations/060_documentation_page_workflow.sql"
    )
    assert (
        path.exists()
    ), "060_documentation_page_workflow.sql is required for the durable page workflow"
    return path.read_text(encoding="utf-8").lower()


def test_migration_extends_draft_revisions_with_artifact_identity() -> None:
    sql = _migration()
    assert "alter table draft_revisions add column if not exists version integer" in sql
    assert "alter table draft_revisions add column if not exists content_hash text" in sql
    assert "unique index if not exists uq_draft_revisions_run_path_version" in sql
    assert "on draft_revisions (run_id, path, version) where version is not null" in sql


def test_migration_defines_page_states_table() -> None:
    sql = _migration()
    assert "create table if not exists documentation_page_states" in sql
    assert "primary key (run_id, page_id)" in sql
    assert "latest_version integer not null default 0 check (latest_version >= 0)" in sql
    assert "next_version integer not null default 1 check (next_version >= 1)" in sql
    assert "evaluation_attempt integer not null default 0 check (evaluation_attempt >= 0)" in sql
    assert "escalation_reason text" in sql
    for status in (
        "pending",
        "writing",
        "evaluating",
        "revising",
        "passed",
        "awaiting_human_review",
        "failed",
    ):
        assert f"'{status}'" in sql


def test_migration_defines_page_evaluations_table() -> None:
    sql = _migration()
    assert "create table if not exists documentation_page_evaluations" in sql
    assert "check (length(content_hash) = 64)" in sql
    assert "attempt integer not null check (attempt >= 1)" in sql
    assert "score double precision not null check (score >= 0 and score <= 1)" in sql
    assert "metrics jsonb not null default '[]'::jsonb" in sql
    assert "revision_feedback jsonb not null default '[]'::jsonb" in sql
    assert "unique (run_id, artifact_id)" in sql
    for status in ("passed", "revision_required", "awaiting_human_review"):
        assert f"'{status}'" in sql


def test_migration_defines_workflow_tasks_table() -> None:
    sql = _migration()
    assert "create table if not exists documentation_workflow_tasks" in sql
    assert "primary key (run_id, task_id)" in sql
    assert "check (infrastructure_retries between 0 and 1)" in sql
    assert "lease_owner text" in sql
    assert "lease_expires_at timestamptz" in sql
    assert "status text not null default 'pending'" in sql
    for task_type in ("write", "evaluate", "cross_page_review"):
        assert f"'{task_type}'" in sql
    for task_status in ("pending", "running", "completed", "failed", "cancelled"):
        assert f"'{task_status}'" in sql
    assert "create index if not exists idx_documentation_tasks_ready" in sql
    assert "on documentation_workflow_tasks (run_id, status, lease_expires_at)" in sql


def test_migration_is_additive() -> None:
    sql = _migration()
    assert "drop table" not in sql
    assert "drop column" not in sql
    assert "drop constraint" not in sql