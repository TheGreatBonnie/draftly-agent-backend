from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from draftly.app.api.workflow_schemas import (
    WorkflowDefinitionCreate,
    WorkflowDefinitionPatch,
    WorkflowDefinitionResponse,
    WorkflowListResponse,
    WorkflowRunListResponse,
    WorkflowRunResponse,
    WorkflowTemplateResponse,
)
from draftly.persistence.repositories.workflows import (
    InvalidWorkflowCursor,
    WorkflowDefinitionsRepository,
    WorkflowRunsRepository,
)


def test_definition_accepts_supported_configuration() -> None:
    definition = WorkflowDefinitionCreate(
        name="PR documentation",
        slug="pr-documentation",
        workflow_key="github_pr",
        description=None,
        trigger_config={"event": "pull_request.opened"},
        condition_config={"branches": ["main"]},
        agent_config={"reviewer": {"enabled": True}},
        repository_config={"owner": "acme", "name": "docs"},
        evaluation_config={"required": True},
        review_config={"required": True},
        delivery_config={"provider": "github"},
    )

    assert definition.status == "draft"
    assert definition.workflow_key == "github_pr"


def test_definition_rejects_invalid_status_and_workflow_key() -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinitionCreate(
            name="Bad",
            slug="bad",
            workflow_key="unknown_workflow",
            status="running",
        )


def test_definition_rejects_provider_secret_recursively() -> None:
    with pytest.raises(ValidationError, match="secret"):
        WorkflowDefinitionCreate(
            name="PR docs",
            slug="pr-docs",
            workflow_key="github_pr",
            delivery_config={"github_token": "secret"},
        )

    with pytest.raises(ValidationError, match="api_key"):
        WorkflowDefinitionPatch(
            agent_config={"nested": {"api_key": "secret"}},
        )


def test_optional_display_fields_accept_none() -> None:
    run = WorkflowRunResponse(
        id="run-1",
        definition_id="def-1",
        title=None,
        repository=None,
        actor=None,
        target=None,
        current_stage=None,
        stage_states={},
        input=None,
        output=None,
        error=None,
    )
    assert run.title is None
    assert run.repository is None


def test_response_envelopes_are_constructible() -> None:
    definition = WorkflowDefinitionResponse(
        id="def-1",
        org_id="org-1",
        name="Docs",
        slug="docs",
        description=None,
        workflow_key="github_pr",
        status="active",
        version=1,
        trigger_config={},
        condition_config={},
        agent_config={},
        repository_config={},
        evaluation_config={},
        review_config={},
        delivery_config={},
        created_by="user-1",
        created_at=None,
        updated_at=None,
    )
    template = WorkflowTemplateResponse(
        id="template-1",
        org_id=None,
        slug="github-pr",
        name="GitHub PR",
        description=None,
        workflow_key="github_pr",
        defaults={},
        is_system=True,
        created_at=None,
        updated_at=None,
    )

    run = WorkflowRunResponse(
        id="run-1",
        definition_id="def-1",
        title=None,
        repository=None,
        actor=None,
        current_stage=None,
        stage_states={},
        input=None,
        output=None,
        error=None,
    )

    assert WorkflowListResponse(items=[definition], summary={}, total=1).total == 1
    assert WorkflowRunListResponse(items=[run], total=1).items[0].id == "run-1"
    assert template.is_system is True


def test_canonical_migrations_define_org_scoped_resources() -> None:
    migration_dir = Path(__file__).parents[2] / "src/draftly/persistence/migrations"
    for name in (
        "049_workflow_definitions.sql",
        "050_workflow_templates.sql",
        "051_workflow_runs.sql",
    ):
        assert (migration_dir / name).exists()


async def test_definition_list_bounds_limit_and_returns_cursor() -> None:
    db = AsyncMock()
    db.fetch_all.return_value = [
        {
            "id": f"def-{index}",
            "org_id": "org-1",
            "name": "Docs",
            "slug": "docs",
            "workflow_key": "github_pr",
            "status": "active",
            "version": 1,
            "updated_at": datetime(2026, 9, 10, tzinfo=UTC),
        }
        for index in range(200)
    ]
    db.fetch_one.return_value = {"total": 201}

    items, total, next_cursor = await WorkflowDefinitionsRepository(db).list(
        org_id="org-1", limit=999
    )

    assert items[0]["id"] == "def-0"
    assert total == 201
    assert next_cursor
    assert "org_id" in db.fetch_all.await_args.args[0]
    assert db.fetch_all.await_args.args[-1] == 200


async def test_definition_list_rejects_malformed_cursor() -> None:
    db = AsyncMock()

    with pytest.raises(InvalidWorkflowCursor):
        await WorkflowDefinitionsRepository(db).list(org_id="org-1", cursor="not-a-cursor")


async def test_run_get_is_organization_scoped() -> None:
    db = AsyncMock()
    db.fetch_one.return_value = None

    result = await WorkflowRunsRepository(db).get(org_id="org-2", run_id="run-1")

    assert result is None
    query, run_id, org_id = db.fetch_one.await_args.args
    assert "org_id = $2" in query
    assert org_id == "org-2"
    assert run_id == "run-1"


async def test_page_results_join_and_compact_mapping() -> None:
    db = AsyncMock()
    db.fetch_all.return_value = [
        {
            "page_id": "readme",
            "path": "README.md",
            "status": "passed",
            "latest_version": 2,
            "evaluation_attempt": 3,
            "escalation_reason": None,
            "score": 0.97,
            "metrics": [
                {"name": "accuracy", "passed": True, "blocking": True},
                {"name": "quality_score", "passed": False, "blocking": True, "reason": "low"},
                {"name": "style", "passed": False, "blocking": False},
            ],
            "revision_feedback": ["Add usage example"],
        },
        {
            "page_id": "api",
            "path": "docs/api.md",
            "status": "awaiting_human_review",
            "latest_version": 1,
            "evaluation_attempt": 2,
            "escalation_reason": "Blocking eager review gate: quality below threshold",
            "score": 0.71,
            "metrics": [],
            "revision_feedback": ["Document error codes"],
        },
    ]

    results = await WorkflowRunsRepository(db).page_results(org_id="org-1", run_id="run-1")

    query, org_id, run_id = db.fetch_all.await_args.args
    assert "r.org_id = $1" in query
    assert "e.artifact_id = s.latest_artifact_id" in query
    assert "ORDER BY s.path ASC" in query
    assert org_id == "org-1"
    assert run_id == "run-1"

    assert [page["path"] for page in results] == ["README.md", "docs/api.md"]
    assert set(results[0]) == {
        "page_id",
        "path",
        "status",
        "version",
        "attempts",
        "score",
        "failed_metrics",
        "feedback",
        "escalation_reason",
    }
    assert results[0]["failed_metrics"] == ["quality_score"]
    assert results[0]["feedback"] == ["Add usage example"]
    assert results[0]["version"] == 2
    assert results[0]["attempts"] == 3
    assert results[0]["score"] == 0.97
    assert "content" not in results[0] and "evidence" not in results[0]
    assert results[1]["failed_metrics"] == []
    assert results[1]["escalation_reason"] == (
        "Blocking eager review gate: quality below threshold"
    )
    assert results[1]["attempts"] == 2


async def test_page_results_returns_empty_for_historical_run() -> None:
    db = AsyncMock()
    db.fetch_all.return_value = []

    assert await WorkflowRunsRepository(db).page_results(
        org_id="org-1", run_id="legacy-run"
    ) == []


async def test_summary_uses_unpaginated_aggregate_query() -> None:
    db = AsyncMock()
    db.fetch_one.side_effect = [
        {
            "total": 42,
            "active": 2,
            "successful": 30,
            "failed": 5,
            "pending_review": 5,
            "pending_intervention": 2,
            "avg_duration_seconds": 12.5,
        },
        {"active": 1, "paused": 2, "draft": 3, "archived": 4},
    ]

    result = await WorkflowDefinitionsRepository(db).summary(org_id="org-1", days=30)

    assert result["runs"]["total"] == 42
    assert result["runs"]["pending_intervention"] == 2
    assert result["definitions"]["draft"] == 3
    assert all("org_id" in call.args[0] for call in db.fetch_one.await_args_list)
