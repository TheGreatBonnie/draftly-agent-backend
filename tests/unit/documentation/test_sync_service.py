"""Unit tests for sync service."""

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from draftly.documentation.sync_service import SyncService


@dataclass
class FakeGitHubClient:
    tree: list = field(default_factory=list)
    files: dict = field(default_factory=dict)
    repository: dict = field(
        default_factory=lambda: {"default_branch": "main", "commit_sha": "abc123"}
    )
    calls: dict = field(default_factory=dict)

    async def get_tree(self, owner, repo, ref, token):
        return self.tree

    async def get_file_contents(self, owner, repo, path, ref, token):
        return self.files.get(path, "")

    async def get_repository(self, repository, token=None):
        self.calls["get_repository"] = (repository, token)
        return self.repository

    async def get_installation_token(self, installation_id):
        return "fake-token"


@dataclass
class FakeDocuments:
    """Mirrors the DocumentRepository interface used by SyncService."""

    documents: list = field(default_factory=list)

    async def get_by_org_and_path(self, *, org_id, path):
        for doc in self.documents:
            if doc["org_id"] == org_id and doc["path"] == path:
                return doc
        return None

    async def upsert(self, **kwargs):
        self.documents.append(kwargs)
        return {"id": f"doc-{len(self.documents)}"}


@dataclass
class FakeMemory:
    """Mirrors DomainMemoryRepository interface used by SyncService."""

    stored: list = field(default_factory=list)
    deleted: list = field(default_factory=list)

    async def delete_by_metadata(self, *, namespace, key, value):
        self.deleted.append((namespace, key, value))
        return 0

    async def store_batch(self, items):
        self.stored.extend(items)
        return [{"id": f"mem-{i}"} for i in range(len(items))]


def _context(documents, memory, installation):
    context = MagicMock()
    context.repositories.documents = documents
    context.repositories.github_installations.first_for_org = AsyncMock(
        return_value=installation
    )
    context.memory = memory
    return context


@pytest.mark.asyncio
async def test_sync_discovers_and_stores_documents():
    github = FakeGitHubClient(
        tree=[
            {"path": "README.md", "type": "blob"},
            {"path": "src/main.py", "type": "blob"},
        ],
        files={"README.md": "# Hello\n\nWorld."},
    )
    documents = FakeDocuments()
    memory = FakeMemory()

    service = SyncService(
        github=github, context=_context(documents, memory, {"installation_id": 42})
    )
    result = await service.sync(
        org_id="test-org",
        repository_full_name="owner/repo",
        include=["README.md", "*.md"],
        exclude=[],
    )

    assert result.document_count == 1
    assert result.commit_sha == "abc123"
    # Document upsert persists sync columns so hash-skip works next run
    record = documents.documents[0]
    assert record["status"] == "indexed"
    assert record["commit_sha"] == "abc123"
    assert record["source_hash"]
    # Chunks are real Document models; stale cleanup ran first
    assert memory.deleted
    chunk = memory.stored[0]
    assert chunk.namespace == "documents"
    assert chunk.metadata["document_id"]


@pytest.mark.asyncio
async def test_sync_get_repository_uses_minted_token():
    github = FakeGitHubClient(
        tree=[{"path": "README.md", "type": "blob"}],
        files={"README.md": "# x"},
    )
    service = SyncService(
        github=github,
        context=_context(FakeDocuments(), FakeMemory(), {"installation_id": 42}),
    )

    await service.sync(
        org_id="test-org",
        repository_full_name="owner/repo",
        include=["README.md"],
        exclude=[],
    )

    repository, token = github.calls["get_repository"]
    assert repository == "owner/repo"
    assert token == "fake-token"


@pytest.mark.asyncio
async def test_sync_skips_unchanged_documents():
    import hashlib
    content = "# Hello\n\nWorld."
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    github = FakeGitHubClient(
        tree=[{"path": "README.md", "type": "blob"}],
        files={"README.md": content},
    )
    documents = FakeDocuments(
        documents=[
            {
                "org_id": "test-org",
                "path": "README.md",
                "source_hash": content_hash,
                "metadata": {"chunk_count": 2},
            }
        ]
    )
    memory = FakeMemory()

    service = SyncService(
        github=github, context=_context(documents, memory, {"installation_id": 42})
    )
    result = await service.sync(
        org_id="test-org",
        repository_full_name="owner/repo",
        include=["README.md"],
        exclude=[],
    )

    assert result.document_count == 0  # Skipped
    assert not memory.stored


@pytest.mark.asyncio
async def test_sync_reprocesses_orphaned_documents_without_chunks():
    """A matching hash without stored chunks is an orphan, not a skip."""
    import hashlib

    content = "# Hello\n\nWorld."
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    github = FakeGitHubClient(
        tree=[{"path": "README.md", "type": "blob"}],
        files={"README.md": content},
    )
    documents = FakeDocuments(
        documents=[
            {
                "org_id": "test-org",
                "path": "README.md",
                "source_hash": content_hash,
            }
        ]
    )
    memory = FakeMemory()

    service = SyncService(
        github=github, context=_context(documents, memory, {"installation_id": 42})
    )
    result = await service.sync(
        org_id="test-org",
        repository_full_name="owner/repo",
        include=["README.md"],
        exclude=[],
    )

    assert result.skipped_count == 0
    assert result.document_count == 1
    assert memory.stored


@pytest.mark.asyncio
async def test_sync_raises_without_installation():
    github = FakeGitHubClient()
    service = SyncService(github=github, context=_context(FakeDocuments(), FakeMemory(), None))

    with pytest.raises(RuntimeError, match="No GitHub installation"):
        await service.sync(org_id="org-x", repository_full_name="owner/repo")
