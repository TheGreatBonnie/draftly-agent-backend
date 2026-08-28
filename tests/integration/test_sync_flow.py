"""Integration test for full documentation sync flow."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from draftly.documentation.sync_service import SyncService


@pytest.mark.asyncio
async def test_full_sync_flow():
    """End-to-end: GitHub → discover → fetch → parse → chunk → embed → store."""
    # Mock GitHub client
    github = MagicMock()
    github.get_installation_token = AsyncMock(return_value="token-123")
    github.get_repository = AsyncMock(
        return_value={"default_branch": "main", "commit_sha": "abc123"}
    )
    github.get_tree = AsyncMock(return_value=[
        {"path": "README.md", "type": "blob"},
        {"path": "docs/guide.md", "type": "blob"},
    ])
    github.get_last_commit_date = AsyncMock(return_value=None)
    github.get_file_contents = AsyncMock(
        side_effect=lambda owner, repo, path, ref, token: {
            "README.md": "# My Project\n\nThis is the README.",
            "docs/guide.md": (
                "# Guide\n\n## Getting Started\n\nInstall instructions.\n\n"
                "## Usage\n\nRun the app."
            ),
    }[path])

    # Mock context
    context = MagicMock()
    documents = MagicMock()
    documents.get_by_org_and_path = AsyncMock(return_value=None)
    documents.upsert = AsyncMock(return_value={"id": "doc-1"})
    context.repositories.documents = documents
    context.repositories.github_installations.first_for_org = AsyncMock(
        return_value={"installation_id": 123}
    )
    memory = MagicMock()
    memory.delete_by_metadata = AsyncMock(return_value=0)
    memory.store_batch = AsyncMock(return_value=[{"id": "mem-1"}])
    context.memory = memory

    # Run sync
    service = SyncService(github=github, context=context)
    result = await service.sync(
        org_id="test-org",
        repository_full_name="owner/repo",
        include=["README.md", "*.md", "docs/**"],
        exclude=[],
    )

    # Verify
    assert result.document_count == 2
    assert result.chunk_count >= 2
    assert result.commit_sha == "abc123"
    assert result.baseline is not None
    assert result.baseline.document_count == 2
