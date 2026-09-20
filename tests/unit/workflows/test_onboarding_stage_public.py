"""Stage 1 branches by source_type behind TAVILY_PUBLIC_INGESTION_ENABLED.

Public mode skips the GitHub installation gate and syncs through
TavilyDocumentationSource; every later stage and event is identical.

Plan: plans/2026-09-20-tavily-rag.md (Task 8).
"""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from draftly.documentation.baseline import BaselineSnapshot
from draftly.documentation.sync_service import SyncResult
from draftly.workflows.onboarding.initialize import run_onboarding_initialize
from draftly.workflows.state import WorkflowStatus

ROOT = "https://docs.example.com"


def _context(*, flag_on: bool):
    context = MagicMock()
    context.publisher = None
    context.repositories.onboarding.get = AsyncMock(
        return_value={"org_id": "test-org", "state": "INITIALIZING"}
    )
    context.repositories.onboarding.upsert = AsyncMock(return_value={})
    context.repositories.onboarding.mark_step = AsyncMock(return_value={})
    context.repositories.onboarding.mark_failed = AsyncMock(return_value={})
    context.repositories.onboarding.mark_step_and_set_state = AsyncMock(
        return_value={}
    )
    context.repositories.github_installations.first_for_org = AsyncMock(
        return_value={"installation_id": 42}
    )
    context.repositories.jobs.update_status = AsyncMock(return_value={})
    context.config = SimpleNamespace(
        tavily_api_key="tvly-test-key" if flag_on else None,
        tavily_base_url="https://api.tavily.com",
        tavily_request_timeout_seconds=60,
        tavily_max_concurrency=4,
        tavily_public_ingestion_enabled=flag_on,
    )
    return context


def _public_selected() -> dict:
    return {
        "full_name": ROOT,
        "source_type": "public_documentation",
        "documentation_config": {"root_url": ROOT + "/"},
    }


def _public_sync_result() -> SyncResult:
    return SyncResult(
        commit_sha="public:abc123",
        repository=ROOT,
        document_count=2,
        chunk_count=5,
        baseline=BaselineSnapshot(
            commit_sha="public:abc123",
            repository=ROOT,
            document_count=2,
            section_count=4,
            chunk_count=5,
        ),
    )


def _stage_patches():
    stack = ExitStack()
    stack.enter_context(
        patch(
            "draftly.workflows.onboarding.stages.run_knowledge_construction",
            new=AsyncMock(
                return_value=MagicMock(
                    knowledge_count=10,
                    relationship_count=5,
                    candidate_count=3,
                    failed_chunks=[],
                )
            ),
        )
    )
    stack.enter_context(
        patch(
            "draftly.workflows.onboarding.stages.run_initial_evaluation",
            new=AsyncMock(return_value=MagicMock(score=0.72, dimensions={})),
        )
    )
    stack.enter_context(
        patch(
            "draftly.workflows.onboarding.stages.run_health_report",
            return_value=MagicMock(score=0.68, dimensions={}),
        )
    )
    stack.enter_context(
        patch(
            "draftly.workflows.onboarding.stages.run_recommendations",
            new=AsyncMock(
                return_value=[
                    MagicMock(
                        priority="high",
                        title="Add API ref",
                        detail="Missing",
                        category="coverage",
                    ),
                ]
            ),
        )
    )
    return stack


@pytest.mark.asyncio
async def test_stage1_public_ingestion_uses_tavily_source() -> None:
    context = _context(flag_on=True)
    with (
        patch(
            "draftly.documentation.tavily_source.TavilyDocumentationSource"
        ) as source_cls,
        patch("draftly.documentation.sync_service.SyncService") as sync_cls,
        patch(
            "draftly.integrations.github.app_auth.build_installation_client",
            new=AsyncMock(),
        ) as builder,
        _stage_patches(),
    ):
        source_cls.return_value.sync = AsyncMock(return_value=_public_sync_result())
        state = await run_onboarding_initialize(
            context, org_id="test-org", selected_repository=_public_selected()
        )

    assert state.status == WorkflowStatus.DELIVERED
    source_cls.assert_called_once()
    sync_cls.assert_not_called()
    builder.assert_not_awaited()
    call = source_cls.return_value.sync.await_args
    assert call.kwargs["org_id"] == "test-org"
    assert str(call.kwargs["config"].root_url) == ROOT + "/"


@pytest.mark.asyncio
async def test_stage1_metrics_mirror_github_flow() -> None:
    context = _context(flag_on=True)
    with (
        patch("draftly.documentation.tavily_source.TavilyDocumentationSource") as source_cls,
        _stage_patches(),
    ):
        source_cls.return_value.sync = AsyncMock(return_value=_public_sync_result())
        state = await run_onboarding_initialize(
            context, org_id="test-org", selected_repository=_public_selected()
        )

    assert state.status == WorkflowStatus.DELIVERED
    assert state.result["document_count"] == 2
    assert state.result["baseline"]["commit_sha"] == "public:abc123"


@pytest.mark.asyncio
async def test_stage1_flag_off_falls_back_to_github_path() -> None:
    context = _context(flag_on=False)
    with (
        patch("draftly.documentation.sync_service.SyncService") as sync_cls,
        patch(
            "draftly.documentation.tavily_source.TavilyDocumentationSource"
        ) as source_cls,
        patch(
            "draftly.integrations.github.app_auth.build_installation_client",
            new=AsyncMock(return_value=MagicMock()),
        ),
        _stage_patches(),
    ):
        sync_cls.return_value.sync = AsyncMock(return_value=_public_sync_result())
        state = await run_onboarding_initialize(
            context, org_id="test-org", selected_repository=_public_selected()
        )

    assert state.status == WorkflowStatus.DELIVERED
    sync_cls.assert_called_once()
    source_cls.assert_not_called()
