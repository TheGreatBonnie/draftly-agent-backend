"""Unit tests for onboarding initialization workflow."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from draftly.app.api.routes import onboarding
from draftly.workflows.onboarding.initialize import STAGES, run_onboarding_initialize
from draftly.workflows.state import WorkflowStatus


def _context():
    context = MagicMock()
    context.publisher = None
    context.repositories.onboarding.get = AsyncMock(return_value={
        "org_id": "test-org", "state": "INITIALIZING",
        "selected_repository": {"full_name": "owner/repo"},
    })
    context.repositories.onboarding.upsert = AsyncMock(return_value={})
    context.repositories.onboarding.mark_step = AsyncMock(return_value={})
    context.repositories.onboarding.mark_failed = AsyncMock(return_value={})
    context.repositories.onboarding.mark_step_and_set_state = AsyncMock(return_value={})
    context.repositories.github_installations.first_for_org = AsyncMock(
        return_value={"installation_id": 42}
    )
    context.repositories.jobs.update_status = AsyncMock(return_value={})
    return context


@pytest.fixture()
def installation_client():
    """Patch the installation-authed client builder used by the workflow."""
    with patch(
        "draftly.integrations.github.app_auth.build_installation_client",
        new=AsyncMock(return_value=MagicMock()),
    ) as builder:
        yield builder


@pytest.mark.asyncio
async def test_initialize_workflow_completes(installation_client):
    """SyncService runs through its stages; repo row ends COMPLETED."""
    from draftly.documentation.baseline import BaselineSnapshot
    from draftly.documentation.sync_service import SyncResult

    sync_result = SyncResult(
        commit_sha="abc123",
        repository="owner/repo",
        document_count=2,
        chunk_count=5,
        baseline=BaselineSnapshot(
            commit_sha="abc123", repository="owner/repo",
            document_count=2, section_count=4, chunk_count=5,
        ),
    )

    with patch("draftly.documentation.sync_service.SyncService") as service_cls:
        service_cls.return_value.sync = AsyncMock(return_value=sync_result)
        with patch(
            "draftly.workflows.onboarding.stages.run_knowledge_construction",
            new=AsyncMock(return_value=MagicMock(
                knowledge_count=10, relationship_count=5,
                candidate_count=3, failed_chunks=[],
            )),
        ):
            with patch(
                "draftly.workflows.onboarding.stages.run_initial_evaluation",
                new=AsyncMock(return_value=MagicMock(
                    score=0.72, dimensions={},
                )),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_health_report",
                    return_value=MagicMock(score=0.68, dimensions={}),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_recommendations",
                        new=AsyncMock(return_value=[
                            MagicMock(priority="high", title="Add API ref",
                                      detail="Missing", category="coverage"),
                        ]),
                    ):
                        state = await run_onboarding_initialize(
                            _context(),
                            org_id="test-org",
                            selected_repository={"full_name": "owner/repo"},
                        )

    assert state.status == WorkflowStatus.DELIVERED
    assert state.result["document_count"] == 2
    assert state.result["baseline"]["commit_sha"] == "abc123"
    installation_client.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_initialize_workflow_marks_job_completed(installation_client):
    """On success the jobs row's status is flipped to 'completed'."""
    from draftly.documentation.baseline import BaselineSnapshot
    from draftly.documentation.sync_service import SyncResult

    sync_result = SyncResult(
        commit_sha="abc123",
        repository="owner/repo",
        document_count=2,
        chunk_count=5,
        baseline=BaselineSnapshot(
            commit_sha="abc123", repository="owner/repo",
            document_count=2, section_count=4, chunk_count=5,
        ),
    )
    context = _context()

    with patch("draftly.documentation.sync_service.SyncService") as service_cls:
        service_cls.return_value.sync = AsyncMock(return_value=sync_result)
        with patch(
            "draftly.workflows.onboarding.stages.run_knowledge_construction",
            new=AsyncMock(return_value=MagicMock(
                knowledge_count=10, relationship_count=5,
                candidate_count=3, failed_chunks=[],
            )),
        ):
            with patch(
                "draftly.workflows.onboarding.stages.run_initial_evaluation",
                new=AsyncMock(return_value=MagicMock(score=0.72, dimensions={})),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_health_report",
                    return_value=MagicMock(score=0.68, dimensions={}),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_recommendations",
                        new=AsyncMock(return_value=[
                            MagicMock(priority="high", title="Add API ref",
                                      detail="Missing", category="coverage"),
                        ]),
                    ):
                        state = await run_onboarding_initialize(
                            context,
                            org_id="test-org",
                            selected_repository={"full_name": "owner/repo"},
                            run_id="run-job-123",
                        )

    assert state.status == WorkflowStatus.DELIVERED
    context.repositories.jobs.update_status.assert_awaited_once_with(
        job_id="run-job-123", status="completed"
    )


@pytest.mark.asyncio
async def test_initialize_workflow_marks_job_failed_on_error(installation_client):
    """On failure the jobs row's status is flipped to 'failed'."""
    context = _context()
    context.repositories.github_installations.first_for_org = AsyncMock(
        side_effect=RuntimeError("boom")
    )

    state = await run_onboarding_initialize(
        context,
        org_id="test-org",
        selected_repository={"full_name": "owner/repo"},
        run_id="run-job-456",
    )

    assert state.status == WorkflowStatus.FAILED
    context.repositories.jobs.update_status.assert_awaited_once_with(
        job_id="run-job-456", status="failed"
    )


@pytest.mark.asyncio
async def test_initialize_workflow_fails_without_repository():
    context = _context()
    state = await run_onboarding_initialize(context, org_id="test-org", selected_repository=None)
    assert state.status == WorkflowStatus.FAILED


@pytest.mark.asyncio
async def test_initialize_workflow_fails_without_installation(installation_client):
    context = _context()
    context.repositories.github_installations.first_for_org = AsyncMock(
        return_value=None
    )
    state = await run_onboarding_initialize(
        context,
        org_id="test-org",
        selected_repository={"full_name": "owner/repo"},
    )
    assert state.status == WorkflowStatus.FAILED
    assert any("installation" in err.lower() for err in state.errors)


@pytest.mark.asyncio
async def test_initialize_workflow_marks_failed_on_sync_error(installation_client):
    with patch("draftly.documentation.sync_service.SyncService") as service_cls:
        service_cls.return_value.sync = AsyncMock(side_effect=RuntimeError("github down"))
        context = _context()
        state = await run_onboarding_initialize(
            context, org_id="test-org",
            selected_repository={"full_name": "owner/repo"},
        )
    assert state.status == WorkflowStatus.FAILED
    context.repositories.onboarding.mark_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_initialize_workflow_fails_when_sync_stores_zero_documents(installation_client):
    """All-files-failed sync must not masquerade as DELIVERED (R-C)."""
    from draftly.documentation.sync_service import SyncResult

    sync_result = SyncResult(
        commit_sha="unknown",
        repository="owner/repo",
        document_count=0,
        chunk_count=0,
        failed_files=["README.md", "docs/guide.md"],
    )

    with patch("draftly.documentation.sync_service.SyncService") as service_cls:
        service_cls.return_value.sync = AsyncMock(return_value=sync_result)
        context = _context()
        state = await run_onboarding_initialize(
            context,
            org_id="test-org",
            selected_repository={"full_name": "owner/repo"},
        )

    assert state.status == WorkflowStatus.FAILED
    context.repositories.onboarding.mark_failed.assert_awaited_once()
    assert any("2 file" in err for err in state.errors)


@pytest.mark.asyncio
async def test_initialize_workflow_surfaces_partial_failures(installation_client):
    """Partial success stays DELIVERED but exposes failure count (R-D)."""
    from draftly.documentation.sync_service import SyncResult

    sync_result = SyncResult(
        commit_sha="abc123",
        repository="owner/repo",
        document_count=1,
        chunk_count=2,
        failed_files=["docs/broken.md"],
    )

    with patch("draftly.documentation.sync_service.SyncService") as service_cls:
        service_cls.return_value.sync = AsyncMock(return_value=sync_result)
        with patch(
            "draftly.workflows.onboarding.stages.run_knowledge_construction",
            new=AsyncMock(return_value=MagicMock(
                knowledge_count=5, relationship_count=2,
                candidate_count=1, failed_chunks=[],
            )),
        ):
            with patch(
                "draftly.workflows.onboarding.stages.run_initial_evaluation",
                new=AsyncMock(return_value=MagicMock(score=0.5, dimensions={})),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_health_report",
                    return_value=MagicMock(score=0.4, dimensions={}),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_recommendations",
                        new=AsyncMock(return_value=[]),
                    ):
                        state = await run_onboarding_initialize(
                            _context(),
                            org_id="test-org",
                            selected_repository={"full_name": "owner/repo"},
                        )

    assert state.status == WorkflowStatus.DELIVERED
    assert state.result["failed_files_count"] == 1


# --- Event publishing tests ---


@pytest.fixture
def mock_publisher():
    pub = AsyncMock()
    pub.publish = AsyncMock()
    return pub


@pytest.fixture
def fake_repositories():
    repos = MagicMock()
    repos.github_installations = AsyncMock()
    repos.github_installations.first_for_org = AsyncMock(
        return_value={"installation_id": 12345}
    )
    repos.onboarding = AsyncMock()
    repos.onboarding.get = AsyncMock(return_value=None)
    repos.onboarding.upsert = AsyncMock()
    repos.onboarding.mark_step = AsyncMock()
    repos.onboarding.mark_step_and_set_state = AsyncMock()
    repos.jobs.update_status = AsyncMock(return_value={})
    return repos


@pytest.mark.asyncio
async def test_publishes_stage_change_events(mock_publisher, fake_repositories):
    """Each stage boundary should publish a stage_change envelope."""
    from draftly.workflows.context import WorkflowContext

    context = WorkflowContext(
        repositories=fake_repositories,
        publisher=mock_publisher,
    )
    with patch(
        "draftly.integrations.github.app_auth.build_installation_client",
        new=AsyncMock(return_value=MagicMock()),
    ):
        fake_sync_result = MagicMock()
        fake_sync_result.document_count = 5
        fake_sync_result.chunk_count = 20
        fake_sync_result.failed_files = []
        fake_sync_result.baseline = None
        fake_sync_result.last_committed_dates = [datetime.now(UTC), datetime.now(UTC)]

        with patch(
            "draftly.documentation.sync_service.SyncService"
        ) as service_cls:
            service_cls.return_value.sync = AsyncMock(return_value=fake_sync_result)
            with patch(
                "draftly.workflows.onboarding.stages.run_knowledge_construction",
                new=AsyncMock(return_value=MagicMock(
                    knowledge_count=10, relationship_count=5,
                    candidate_count=3, failed_chunks=[],
                )),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_initial_evaluation",
                    new=AsyncMock(return_value=MagicMock(
                        score=0.7, dimensions={},
                    )),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_health_report",
                        return_value=MagicMock(score=0.6, dimensions={}),
                    ):
                        with patch(
                            "draftly.workflows.onboarding.stages.run_recommendations",
                            new=AsyncMock(return_value=[
                                MagicMock(priority="high", title="Add docs",
                                          detail="Missing", category="coverage"),
                            ]),
                        ):
                            await run_onboarding_initialize(
                                context,
                                org_id="org_test123",
                                selected_repository={
                                    "full_name": "test/repo",
                                    "doc_include": ["*.md"],
                                    "doc_exclude": [],
                                },
                            )

    calls = mock_publisher.publish.call_args_list
    stage_events = [
        c
        for c in calls
        if hasattr(c, "args")
        and hasattr(c.args[0], "type")
        and c.args[0].type == "stage_change"
    ]
    result_events = [
        c
        for c in calls
        if hasattr(c, "args")
        and hasattr(c.args[0], "type")
        and c.args[0].type == "workflow_result"
    ]
    assert len(stage_events) >= 5, f"Expected >=5 stage_change events, got {len(stage_events)}"
    assert len(result_events) == 1, f"Expected 1 workflow_result event, got {len(result_events)}"


@pytest.mark.asyncio
async def test_publishes_workflow_result_on_failure(mock_publisher, fake_repositories):
    """On exception, a workflow_result event with status=FAILED should be published."""
    from draftly.workflows.context import WorkflowContext

    context = WorkflowContext(
        repositories=fake_repositories,
        publisher=mock_publisher,
    )
    with patch(
        "draftly.integrations.github.app_auth.build_installation_client",
        new=AsyncMock(side_effect=RuntimeError("auth failed")),
    ):
        await run_onboarding_initialize(
            context,
            org_id="org_test123",
            selected_repository={"full_name": "test/repo"},
        )

    calls = mock_publisher.publish.call_args_list
    result_events = [
        c
        for c in calls
        if hasattr(c, "args")
        and hasattr(c.args[0], "type")
        and c.args[0].type == "workflow_result"
    ]
    assert len(result_events) == 1
    envelope = result_events[0].args[0]
    assert envelope.payload.get("status") == "FAILED"


@pytest.mark.asyncio
async def test_does_not_publish_when_publisher_is_none():
    """When publisher is None, the workflow should still run without error."""
    context = _context()
    context.publisher = None
    with patch(
        "draftly.integrations.github.app_auth.build_installation_client",
        new=AsyncMock(return_value=MagicMock()),
    ):
        fake_sync_result = MagicMock()
        fake_sync_result.document_count = 1
        fake_sync_result.chunk_count = 3
        fake_sync_result.failed_files = []
        fake_sync_result.baseline = None
        fake_sync_result.last_committed_dates = []

        with patch(
            "draftly.documentation.sync_service.SyncService"
        ) as service_cls:
            service_cls.return_value.sync = AsyncMock(return_value=fake_sync_result)
            with patch(
                "draftly.workflows.onboarding.stages.run_knowledge_construction",
                new=AsyncMock(return_value=MagicMock(
                    knowledge_count=5, relationship_count=2,
                    candidate_count=1, failed_chunks=[],
                )),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_initial_evaluation",
                    new=AsyncMock(return_value=MagicMock(score=0.5, dimensions={})),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_health_report",
                        return_value=MagicMock(score=0.4, dimensions={}),
                    ):
                        with patch(
                            "draftly.workflows.onboarding.stages.run_recommendations",
                            new=AsyncMock(return_value=[]),
                        ):
                            state = await run_onboarding_initialize(
                                context,
                                org_id="org_test123",
                                selected_repository={"full_name": "test/repo"},
                            )

    assert state.status == WorkflowStatus.DELIVERED


@pytest.mark.asyncio
async def test_initialize_stores_stage_results(installation_client):
    """Stage results should be persisted in selected_repository."""
    from draftly.documentation.baseline import BaselineSnapshot
    from draftly.documentation.sync_service import SyncResult

    sync_result = SyncResult(
        commit_sha="abc123",
        repository="owner/repo",
        document_count=5,
        chunk_count=20,
        last_committed_dates=[datetime.now(UTC), datetime.now(UTC)],
        baseline=BaselineSnapshot(
            commit_sha="abc123", repository="owner/repo",
            document_count=5, section_count=15, chunk_count=20,
        ),
    )

    ctx = _context()
    with patch("draftly.documentation.sync_service.SyncService") as service_cls:
        service_cls.return_value.sync = AsyncMock(return_value=sync_result)
        with patch(
            "draftly.workflows.onboarding.stages.run_knowledge_construction",
            new=AsyncMock(return_value=MagicMock(
                knowledge_count=10, relationship_count=5,
                candidate_count=3, failed_chunks=[],
            )),
        ):
            with patch(
                "draftly.workflows.onboarding.stages.run_initial_evaluation",
                new=AsyncMock(return_value=MagicMock(
                    score=0.72, dimensions={
                        "coverage": 0.8, "completeness": 0.6,
                        "structure": 0.7, "length": 0.7,
                    },
                )),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_health_report",
                    return_value=MagicMock(score=0.68, dimensions={
                        "coverage": 0.8, "structure": 0.7,
                        "freshness": 1.0, "completeness": 0.6,
                    }),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_recommendations",
                        new=AsyncMock(return_value=[
                            MagicMock(priority="high", title="Add API ref",
                                      detail="Missing", category="coverage"),
                        ]),
                    ):
                        state = await run_onboarding_initialize(
                            ctx,
                            org_id="test-org",
                            selected_repository={"full_name": "owner/repo"},
                        )

    assert state.status == WorkflowStatus.DELIVERED

    # Verify mark_step_and_set_state was called with stage results
    ctx.repositories.onboarding.mark_step_and_set_state.assert_awaited_once()
    call_kwargs = ctx.repositories.onboarding.mark_step_and_set_state.call_args
    stored_selected = (
        call_kwargs.kwargs.get("selected_repository")
        or call_kwargs[1].get("selected_repository")
    )
    assert stored_selected["knowledge_count"] == 10
    assert stored_selected["eval_score"] == 0.72
    assert stored_selected["health_score"] == 0.68
    assert len(stored_selected["recommendations"]) == 1
    assert stored_selected["recommendations"][0]["priority"] == "high"
    assert stored_selected["document_count"] == 5
    assert stored_selected["chunk_count"] == 20


@pytest.mark.asyncio
async def test_publishes_stage_progress_for_all_stages(mock_publisher, fake_repositories):
    from draftly.workflows.context import WorkflowContext

    context = WorkflowContext(
        repositories=fake_repositories,
        publisher=mock_publisher,
    )
    with patch(
        "draftly.integrations.github.app_auth.build_installation_client",
        new=AsyncMock(return_value=MagicMock()),
    ):
        fake_sync_result = MagicMock()
        fake_sync_result.document_count = 5
        fake_sync_result.chunk_count = 20
        fake_sync_result.failed_files = []
        fake_sync_result.baseline = None
        fake_sync_result.last_committed_dates = [datetime.now(UTC)]

        with patch(
            "draftly.documentation.sync_service.SyncService"
        ) as service_cls:
            service_cls.return_value.sync = AsyncMock(return_value=fake_sync_result)
            with patch(
                "draftly.workflows.onboarding.stages.run_knowledge_construction",
                new=AsyncMock(return_value=MagicMock(
                    knowledge_count=10, relationship_count=5,
                    candidate_count=3, failed_chunks=[],
                )),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_initial_evaluation",
                    new=AsyncMock(return_value=MagicMock(score=0.7, dimensions={})),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_health_report",
                        return_value=MagicMock(score=0.6, dimensions={}),
                    ):
                        with patch(
                            "draftly.workflows.onboarding.stages.run_recommendations",
                            new=AsyncMock(return_value=[]),
                        ):
                            await run_onboarding_initialize(
                                context,
                                org_id="org_test123",
                                selected_repository={"full_name": "test/repo"},
                            )

    calls = mock_publisher.publish.call_args_list
    progress_events = [
        c.args[0]
        for c in calls
        if hasattr(c, "args")
        and hasattr(c.args[0], "type")
        and c.args[0].type == "stage_progress"
    ]
    stages_with_progress = {e.payload["stage"] for e in progress_events}
    expected_stages = {
        "repository_ingestion", "knowledge_construction",
        "initial_evaluation", "health_report", "recommendations",
    }
    assert expected_stages.issubset(stages_with_progress), (
        f"Missing stage_progress for stages: {expected_stages - stages_with_progress}"
    )


@pytest.mark.asyncio
async def test_publishes_stage_progress_even_without_sync_callback(
    mock_publisher, fake_repositories
):
    from draftly.workflows.context import WorkflowContext

    context = WorkflowContext(
        repositories=fake_repositories,
        publisher=mock_publisher,
    )
    with patch(
        "draftly.integrations.github.app_auth.build_installation_client",
        new=AsyncMock(return_value=MagicMock()),
    ):
        fake_sync_result = MagicMock()
        fake_sync_result.document_count = 3
        fake_sync_result.chunk_count = 10
        fake_sync_result.failed_files = []
        fake_sync_result.baseline = None
        fake_sync_result.last_committed_dates = []

        with patch(
            "draftly.documentation.sync_service.SyncService"
        ) as service_cls:
            service_cls.return_value.sync = AsyncMock(return_value=fake_sync_result)
            with patch(
                "draftly.workflows.onboarding.stages.run_knowledge_construction",
                new=AsyncMock(return_value=MagicMock(
                    knowledge_count=5, relationship_count=2,
                    candidate_count=1, failed_chunks=[],
                )),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_initial_evaluation",
                    new=AsyncMock(return_value=MagicMock(score=0.5, dimensions={})),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_health_report",
                        return_value=MagicMock(score=0.4, dimensions={}),
                    ):
                        with patch(
                            "draftly.workflows.onboarding.stages.run_recommendations",
                            new=AsyncMock(return_value=[]),
                        ):
                            await run_onboarding_initialize(
                                context,
                                org_id="org_test123",
                                selected_repository={"full_name": "test/repo"},
                            )

    calls = mock_publisher.publish.call_args_list
    repo_progress = [
        c.args[0]
        for c in calls
        if hasattr(c, "args")
        and hasattr(c.args[0], "type")
        and c.args[0].type == "stage_progress"
        and c.args[0].payload.get("stage") == "repository_ingestion"
    ]
    assert len(repo_progress) >= 1, (
        "stage_progress for repository_ingestion must be emitted even without sync callback"
    )


@pytest.mark.asyncio
async def test_repository_ingestion_emits_final_100_progress(mock_publisher, fake_repositories):
    """The repository_ingestion bar must fill to 100% before the stage completes.

    Regression: the stage topped out at 92% (or 0% when sync reported no
    progress) and then flipped straight to a check mark, so the active bar
    never visually completed.
    """
    from draftly.workflows.context import WorkflowContext

    context = WorkflowContext(
        repositories=fake_repositories,
        publisher=mock_publisher,
    )
    with patch(
        "draftly.integrations.github.app_auth.build_installation_client",
        new=AsyncMock(return_value=MagicMock()),
    ):
        fake_sync_result = MagicMock()
        fake_sync_result.document_count = 3
        fake_sync_result.chunk_count = 10
        fake_sync_result.failed_files = []
        fake_sync_result.baseline = None
        fake_sync_result.last_committed_dates = []

        with patch(
            "draftly.documentation.sync_service.SyncService"
        ) as service_cls:
            service_cls.return_value.sync = AsyncMock(return_value=fake_sync_result)
            with patch(
                "draftly.workflows.onboarding.stages.run_knowledge_construction",
                new=AsyncMock(return_value=MagicMock(
                    knowledge_count=5, relationship_count=2,
                    candidate_count=1, failed_chunks=[],
                )),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_initial_evaluation",
                    new=AsyncMock(return_value=MagicMock(score=0.5, dimensions={})),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_health_report",
                        return_value=MagicMock(score=0.4, dimensions={}),
                    ):
                        with patch(
                            "draftly.workflows.onboarding.stages.run_recommendations",
                            new=AsyncMock(return_value=[]),
                        ):
                            await run_onboarding_initialize(
                                context,
                                org_id="org_test123",
                                selected_repository={"full_name": "test/repo"},
                            )

    repo_progress = _stage_progress_events(mock_publisher, "repository_ingestion")
    assert len(repo_progress) >= 2, (
        "Expected at least the flush emit plus a final 100 emit"
    )
    assert repo_progress[-1].payload["progress"] == 100, (
        f"Last repository_ingestion stage_progress must be 100, got "
        f"{repo_progress[-1].payload.get('progress')}"
    )


def _overall_progress_events(mock_publisher) -> list:
    """Mirror _stage_progress_events: publish() is always called positionally."""
    return [
        c.args[0]
        for c in mock_publisher.publish.call_args_list
        if hasattr(c, "args")
        and hasattr(c.args[0], "type")
        and c.args[0].type == "overall_progress"
    ]


@pytest.mark.asyncio
async def test_emits_overall_progress_events(mock_publisher, fake_repositories):
    """The backend is the single source of truth for the overall aggregate."""
    from draftly.workflows.context import WorkflowContext

    context = WorkflowContext(
        repositories=fake_repositories,
        publisher=mock_publisher,
    )
    with patch(
        "draftly.integrations.github.app_auth.build_installation_client",
        new=AsyncMock(return_value=MagicMock()),
    ):
        fake_sync_result = MagicMock()
        fake_sync_result.document_count = 3
        fake_sync_result.chunk_count = 10
        fake_sync_result.failed_files = []
        fake_sync_result.baseline = None
        fake_sync_result.last_committed_dates = []

        with patch("draftly.documentation.sync_service.SyncService") as service_cls:
            service_cls.return_value.sync = AsyncMock(return_value=fake_sync_result)
            with patch(
                "draftly.workflows.onboarding.stages.run_knowledge_construction",
                new=AsyncMock(return_value=MagicMock(
                    knowledge_count=5, relationship_count=2,
                    candidate_count=1, failed_chunks=[],
                )),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_initial_evaluation",
                    new=AsyncMock(return_value=MagicMock(score=0.5, dimensions={})),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_health_report",
                        return_value=MagicMock(score=0.4, dimensions={}),
                    ):
                        with patch(
                            "draftly.workflows.onboarding.stages.run_recommendations",
                            new=AsyncMock(return_value=[]),
                        ):
                            await run_onboarding_initialize(
                                context,
                                org_id="org_test123",
                                selected_repository={"full_name": "test/repo"},
                            )

    overall = _overall_progress_events(mock_publisher)
    assert len(overall) >= 1
    assert overall[-1].payload["progress"] == 100, (
        "Overall must reach 100 when the last stage completes"
    )
    # overall must be monotonic non-decreasing across the run in this happy path
    values = [e.payload["progress"] for e in overall]
    assert values == sorted(values), f"overall_progress must be monotonic, got {values}"


# ============================================================
# Task 10: live throttled progress during repository_ingestion
# ============================================================


def _fake_sync_result(document_count=3, chunk_count=9):
    fake = MagicMock()
    fake.document_count = document_count
    fake.chunk_count = chunk_count
    fake.failed_files = []
    fake.baseline = None
    fake.last_committed_dates = []
    return fake


def _stage_progress_events(mock_publisher, stage="repository_ingestion"):
    return [
        c.args[0]
        for c in mock_publisher.publish.call_args_list
        if hasattr(c, "args")
        and hasattr(c.args[0], "type")
        and c.args[0].type == "stage_progress"
        and c.args[0].payload.get("stage") == stage
    ]


async def _run_workflow_with_sync(mock_publisher, fake_repositories, sync_impl):
    """Run the full workflow with SyncService replaced by sync_impl.

    ``sync_impl(on_progress)`` is awaited inside sync().
    """
    from draftly.workflows.context import WorkflowContext

    context = WorkflowContext(
        repositories=fake_repositories,
        publisher=mock_publisher,
    )

    class FakeSyncService:
        def __init__(self, github=None, context=None):
            pass

        async def sync(self, *, org_id, repository_full_name, include, exclude, on_progress=None):
            return await sync_impl(on_progress)

    with patch(
        "draftly.integrations.github.app_auth.build_installation_client",
        new=AsyncMock(return_value=MagicMock()),
    ):
        with patch(
            "draftly.documentation.sync_service.SyncService", FakeSyncService
        ):
            with patch(
                "draftly.workflows.onboarding.stages.run_knowledge_construction",
                new=AsyncMock(return_value=MagicMock(
                    knowledge_count=1, relationship_count=1,
                    candidate_count=1, failed_chunks=[],
                )),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_initial_evaluation",
                    new=AsyncMock(return_value=MagicMock(score=0.5, dimensions={})),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_health_report",
                        return_value=MagicMock(score=0.4, dimensions={}),
                    ):
                        with patch(
                            "draftly.workflows.onboarding.stages.run_recommendations",
                            new=AsyncMock(return_value=[]),
                        ):
                            return await run_onboarding_initialize(
                                context,
                                org_id="org_test123",
                                selected_repository={"full_name": "test/repo"},
                            )


@pytest.mark.asyncio
async def test_progress_flushes_during_sync(mock_publisher, fake_repositories):
    """Task 10: stage_progress must be published WHILE sync() is running.

    The fake sync waits (bounded) until a repository_ingestion stage_progress
    with progress > 0 is observed mid-run before returning.
    """
    async def sync_impl(on_progress):
        if on_progress:
            on_progress(2, 6)
        for _ in range(200):
            if any(
                e.payload.get("progress", 0) > 0
                for e in _stage_progress_events(mock_publisher)
            ):
                return _fake_sync_result()
            await asyncio.sleep(0.01)
        pytest.fail(
            "stage_progress for repository_ingestion was never published during sync"
        )

    state = await _run_workflow_with_sync(mock_publisher, fake_repositories, sync_impl)
    assert state.status == WorkflowStatus.DELIVERED


@pytest.mark.asyncio
async def test_progress_flusher_torn_down_after_success(mock_publisher, fake_repositories):
    """No pending progress-flusher task may survive a successful run."""

    async def sync_impl(on_progress):
        if on_progress:
            on_progress(1, 3)
        await asyncio.sleep(0)
        return _fake_sync_result()

    await _run_workflow_with_sync(mock_publisher, fake_repositories, sync_impl)
    await asyncio.sleep(0)

    current = asyncio.current_task()
    leftovers = [
        t
        for t in asyncio.all_tasks()
        if t is not current and not t.done() and "_progress_loop" in repr(t.get_coro())
    ]
    assert leftovers == [], f"flusher task leaked: {leftovers}"


@pytest.mark.asyncio
async def test_progress_flusher_torn_down_on_failure(mock_publisher, fake_repositories):
    """A failing sync must not leave the flusher loop pending."""

    async def sync_impl(on_progress):
        raise RuntimeError("sync exploded")

    state = await _run_workflow_with_sync(mock_publisher, fake_repositories, sync_impl)
    assert state.status == WorkflowStatus.FAILED
    await asyncio.sleep(0)

    current = asyncio.current_task()
    leftovers = [
        t
        for t in asyncio.all_tasks()
        if t is not current and not t.done() and "_progress_loop" in repr(t.get_coro())
    ]
    assert leftovers == [], f"flusher task leaked after failure: {leftovers}"


@pytest.mark.asyncio
async def test_idle_progress_loop_publishes_no_duplicates(mock_publisher, fake_repositories):
    """Dirty-flag gate: one on_progress then idle → exactly one mid-sync flush
    plus the final flush and the close-at-100 emit, and nothing from the idle
    window. A naive interval loop would spam duplicates."""
    async def sync_impl(on_progress):
        if on_progress:
            on_progress(1, 3)
        await asyncio.sleep(0.15)  # idle window after the single callback
        return _fake_sync_result()

    await _run_workflow_with_sync(mock_publisher, fake_repositories, sync_impl)

    events = _stage_progress_events(mock_publisher)
    assert len(events) == 3, (
        f"expected exactly 3 repository_ingestion stage_progress events "
        f"(mid-sync + final + close-at-100), got {len(events)}"
    )
    assert events[-1].payload["progress"] == 100


# ============================================================
# Task 9: route enqueues to RQ when enabled, falls back otherwise
# ============================================================


def _enqueue_recorder(monkeypatch):
    """Patch enqueue_job at the routes.onboarding module boundary."""
    mock = MagicMock(return_value=MagicMock(id="rq-job-1"))
    monkeypatch.setattr("draftly.app.api.routes.onboarding.enqueue_job", mock)
    return mock


@pytest.mark.asyncio
async def test_execute_initialization_enqueues_to_rq_when_enabled(monkeypatch):
    """When RQ is enabled, the route enqueues and returns immediately.

    The in-process worker is bypassed entirely — execution is the RQ
    worker's job. A jobs row is inserted so /stream-ticket can resolve
    the run_id.
    """
    request = MagicMock()
    request.app.state.draftly.rq_queues = {"default": MagicMock()}
    request.app.state.draftly.task_handlers = {"onboarding.initialize": MagicMock()}
    request.app.state.draftly.settings = MagicMock(rq_enabled=True)

    enqueue_mock = _enqueue_recorder(monkeypatch)

    async def fake_acquire(*args, **kwargs):
        return True

    async def fake_release(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "draftly.app.api.routes.onboarding._try_acquire_init_lock", fake_acquire
    )
    monkeypatch.setattr(
        "draftly.app.api.routes.onboarding._release_init_lock", fake_release
    )
    monkeypatch.setattr(
        "draftly.app.api.routes.workflows._tickets",
        lambda request: MagicMock(issue=AsyncMock(return_value="tkt-fallback")),
        raising=False,
    )

    repos = MagicMock()
    repos.onboarding.get = AsyncMock(return_value=None)
    repos.onboarding.upsert = AsyncMock()
    jobs_upsert = repos.jobs.upsert_on_conflict = AsyncMock()

    result = await onboarding._execute_initialization(
        repos, "org-1", worker=None, selected_repository={"full_name": "o/r"},
        request=request,
    )

    assert result["state"] == "INITIALIZING"
    assert "run_id" in result and "ticket" in result
    enqueue_mock.assert_called_once()
    # enqueue_job is a sync function in this module-level monkeypatch; the
    # call lives on call_args, not await_args.
    kwargs = enqueue_mock.call_args.kwargs
    assert kwargs["task_name"] == "onboarding.initialize"
    assert kwargs["org_id"] == "org-1"
    assert kwargs["run_id"] == result["run_id"]
    # Task 1: the app-wired store is used and pins the RQ job id so
    # /stream-ticket can later resolve run_id -> job_id. Registered via
    # conflict-tolerant upsert_on_conflict per Task 10.
    jobs_upsert.assert_awaited_once()
    jobs_upsert.assert_awaited_with(
        run_id=result["run_id"],
        org_id="org-1",
        name="onboarding.initialize",
        job_type="onboarding",
        schedule="manual",
        configuration={"rq_job_id": "rq-job-1"},
    )


@pytest.mark.asyncio
async def test_execute_initialization_reconciles_jobs_row_when_resumed(monkeypatch):
    """Task 2: when the init lock is held (resumed path), reconcile a jobs
    row for the stored run_id idempotently so /stream-ticket never 404s."""
    request = MagicMock()
    request.app.state.draftly.settings = MagicMock(rq_enabled=True)
    request.app.state.draftly.rq_queues = {"default": MagicMock()}
    request.app.state.draftly.task_handlers = {"onboarding.initialize": MagicMock()}

    async def fake_acquire(*args, **kwargs):
        return False

    monkeypatch.setattr(
        "draftly.app.api.routes.onboarding._try_acquire_init_lock", fake_acquire
    )

    repos = MagicMock()
    repos.onboarding.get = AsyncMock(
        return_value={
            "state": "INITIALIZING",
            "selected_repository": {"init_run_id": "stored-run-1"},
        }
    )
    repos_jobs = repos.jobs
    repos_jobs.upsert_on_conflict = AsyncMock()

    result = await onboarding._execute_initialization(
        repos, "org-1", worker=None, selected_repository={"full_name": "o/r"},
        request=request,
    )

    assert result == {
        "state": "INITIALIZING",
        "run_id": "stored-run-1",
        "resumed": True,
    }
    repos_jobs.upsert_on_conflict.assert_awaited_once_with(
        run_id="stored-run-1",
        org_id="org-1",
        name="onboarding.initialize",
        job_type="onboarding",
        schedule="manual",
        configuration={},
    )


# ============================================================
# Task 3: structlog lifecycle lines for all five init stages
# ============================================================


@pytest.mark.asyncio
async def test_initialize_emits_stage_lifecycle_logs(installation_client, monkeypatch):
    from structlog.testing import capture_logs

    import draftly.workflows.onboarding.initialize as init_mod
    from draftly.documentation.baseline import BaselineSnapshot
    from draftly.documentation.sync_service import SyncResult

    sync_result = SyncResult(
        commit_sha="abc123",
        repository="owner/repo",
        document_count=2,
        chunk_count=5,
        baseline=BaselineSnapshot(
            commit_sha="abc123", repository="owner/repo",
            document_count=2, section_count=4, chunk_count=5,
        ),
    )

    with capture_logs() as logs:
        # Fresh proxy: cache_logger_on_first_use=True (logging.py:99) means
        # the module logger is already bound to the real config, so only a
        # fresh proxy resolves against capture_logs' temporary processors.
        monkeypatch.setattr(
            init_mod, "logger", structlog.get_logger("test.initialize.stage_lifecycle")
        )
        with patch("draftly.documentation.sync_service.SyncService") as service_cls:
            service_cls.return_value.sync = AsyncMock(return_value=sync_result)
            with patch(
                "draftly.workflows.onboarding.stages.run_knowledge_construction",
                new=AsyncMock(return_value=MagicMock(
                    knowledge_count=10, relationship_count=5,
                    candidate_count=3, failed_chunks=[],
                )),
            ):
                with patch(
                    "draftly.workflows.onboarding.stages.run_initial_evaluation",
                    new=AsyncMock(return_value=MagicMock(
                        score=0.72, dimensions={},
                    )),
                ):
                    with patch(
                        "draftly.workflows.onboarding.stages.run_health_report",
                        return_value=MagicMock(score=0.68, dimensions={}),
                    ):
                        with patch(
                            "draftly.workflows.onboarding.stages.run_recommendations",
                            new=AsyncMock(return_value=[
                                MagicMock(priority="high", title="Add API ref",
                                          detail="Missing", category="coverage"),
                            ]),
                        ):
                            state = await run_onboarding_initialize(
                                _context(), org_id="test-org",
                                selected_repository={"full_name": "owner/repo"},
                            )

    assert state.status == WorkflowStatus.DELIVERED
    starts = [line for line in logs if line.get("event") == "stage_start"]
    completes = [line for line in logs if line.get("event") == "stage_complete"]
    assert [line["stage"] for line in starts] == list(STAGES)
    assert [line["stage"] for line in completes] == list(STAGES)
    assert all(line["duration_ms"] >= 0 for line in completes)
    assert all("stats" in line for line in completes)


@pytest.mark.asyncio
async def test_execute_initialization_falls_back_to_in_process_when_rq_disabled(monkeypatch):
    """Without rq_queues/task_handlers, the route uses worker.run_task."""
    request = MagicMock()
    request.app.state.draftly.rq_queues = None
    request.app.state.draftly.task_handlers = None

    async def fake_acquire(*args, **kwargs):
        return True

    async def fake_release(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "draftly.app.api.routes.onboarding._try_acquire_init_lock", fake_acquire
    )
    monkeypatch.setattr(
        "draftly.app.api.routes.onboarding._release_init_lock", fake_release
    )
    enqueue_mock = _enqueue_recorder(monkeypatch)

    repos = MagicMock()
    repos.onboarding.get = AsyncMock(return_value=None)
    repos.onboarding.upsert = AsyncMock()
    repos.jobs.upsert_on_conflict = AsyncMock()

    worker = MagicMock()
    worker.task_runner.has_task = MagicMock(return_value=True)
    worker.run_task = AsyncMock(return_value={"org_id": "org-1", "state": "COMPLETED"})

    monkeypatch.setattr(
        "draftly.app.api.routes.workflows._tickets",
        lambda request: MagicMock(issue=AsyncMock(return_value="tkt-fallback")),
        raising=False,
    )

    await onboarding._execute_initialization(
        repos, "org-1", worker=worker, selected_repository={"full_name": "o/r"},
        request=request,
    )
    # Let the background coroutine actually run.
    await asyncio.sleep(0)

    enqueue_mock.assert_not_called()
    worker.run_task.assert_awaited_once()
    assert worker.run_task.await_args.args[0] == "onboarding.initialize"
