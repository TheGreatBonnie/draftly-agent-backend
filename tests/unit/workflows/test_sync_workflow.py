"""Unit tests for documentation sync workflow."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from draftly.workflows.documentation.documentation_sync import run_documentation_sync
from draftly.workflows.state import WorkflowStatus


def _context(installation):
    context = MagicMock()
    context.repositories.github_installations.first_for_org = AsyncMock(
        return_value=installation
    )
    return context


@pytest.fixture()
def installation_client():
    with patch(
        "draftly.integrations.github.app_auth.build_installation_client",
        new=AsyncMock(return_value=MagicMock()),
    ) as builder:
        yield builder


@pytest.mark.asyncio
async def test_sync_workflow_completes_delivered(installation_client):
    """Happy path: installation resolves, sync service succeeds."""
    from draftly.documentation.sync_service import SyncResult

    sync_result = SyncResult(
        commit_sha="abc123",
        repository="owner/repo",
        document_count=3,
        chunk_count=9,
    )

    with patch("draftly.documentation.sync_service.SyncService") as service_cls:
        service_cls.return_value.sync = AsyncMock(return_value=sync_result)
        state = await run_documentation_sync(
            _context({"installation_id": 42}),
            org_id="test-org",
            repository_full_name="owner/repo",
        )

    assert state.status == WorkflowStatus.DELIVERED
    assert state.result["document_count"] == 3
    assert state.result["chunk_count"] == 9
    installation_client.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_sync_workflow_fails_without_installation():
    """No GitHub App installation for the org → FAILED, not crash."""
    state = await run_documentation_sync(
        _context(None),
        org_id="test-org",
        repository_full_name="owner/repo",
    )
    assert state.status == WorkflowStatus.FAILED


@pytest.mark.asyncio
async def test_sync_workflow_fails_with_missing_params():
    state = await run_documentation_sync(_context(None))
    assert state.status == WorkflowStatus.FAILED
