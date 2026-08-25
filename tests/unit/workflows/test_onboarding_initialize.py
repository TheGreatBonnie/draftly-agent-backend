"""Unit tests for onboarding initialization workflow."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from draftly.workflows.onboarding.initialize import run_onboarding_initialize
from draftly.workflows.state import WorkflowStatus


def _context():
    context = MagicMock()
    context.repositories.onboarding.get = AsyncMock(return_value={
        "org_id": "test-org", "state": "INITIALIZING",
        "selected_repository": {"full_name": "owner/repo"},
    })
    context.repositories.onboarding.upsert = AsyncMock(return_value={})
    context.repositories.onboarding.mark_step = AsyncMock(return_value={})
    context.repositories.onboarding.mark_failed = AsyncMock(return_value={})
    context.repositories.github_installations.first_for_org = AsyncMock(
        return_value={"installation_id": 42}
    )
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
        state = await run_onboarding_initialize(
            _context(),
            org_id="test-org",
            selected_repository={"full_name": "owner/repo"},
        )

    assert state.status == WorkflowStatus.DELIVERED
    assert state.result["failed_files_count"] == 1
