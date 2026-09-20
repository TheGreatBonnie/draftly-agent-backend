"""Unit tests for sync service."""

import asyncio
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

    async def get_last_commit_date(self, owner, repo, path, ref, token):
        return None


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

    async def delete_by_metadata(self, *, namespace, key, value, org_id=None):
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


@pytest.mark.asyncio
async def test_sync_processes_files_concurrently():
    """Prove that get_file_contents calls overlap and failures do not abort siblings."""

    class ConcurrentFakeGitHubClient(FakeGitHubClient):
        def __init__(self):
            super().__init__()
            self.tree = [{"path": f"doc{i}.md", "type": "blob"} for i in range(20)]
            self.active_requests = 0
            self.max_active_requests = 0
            self.lock = asyncio.Lock()
            self.barrier_event = asyncio.Event()

        async def get_file_contents(self, owner, repo, path, ref, token):
            async with self.lock:
                self.active_requests += 1
                if self.active_requests > self.max_active_requests:
                    self.max_active_requests = self.active_requests

            # Wait a bit to let other requests catch up and measure overlap
            await asyncio.sleep(0.05)

            async with self.lock:
                self.active_requests -= 1

            if path == "doc5.md":
                raise RuntimeError("Fake network error")

            return f"# Content for {path}"

        async def get_last_commit_date(self, owner, repo, path, ref, token):
            return None

    github = ConcurrentFakeGitHubClient()
    documents = FakeDocuments()
    memory = FakeMemory()

    service = SyncService(
        github=github, context=_context(documents, memory, {"installation_id": 42})
    )
    result = await service.sync(
        org_id="test-org",
        repository_full_name="owner/repo",
        include=["*.md"],
        exclude=[],
    )

    # 19 files succeeded, 1 failed
    assert result.document_count == 19
    assert result.failed_files == ["doc5.md"]
    # Check that maximum concurrency was greater than 1 (overlap proved)
    assert github.max_active_requests > 1



@pytest.mark.asyncio
async def test_sync_chunk_metadata_carries_citation_fields():
    """GitHub chunks gain genuine URL + page-type provenance (AC7)."""
    github = FakeGitHubClient(
        tree=[{"path": "docs/deploy.md", "type": "blob"}],
        files={"docs/deploy.md": "# Deploy\n\nSteps here."},
    )
    documents = FakeDocuments()
    memory = FakeMemory()

    service = SyncService(
        github=github, context=_context(documents, memory, {"installation_id": 42})
    )
    await service.sync(
        org_id="test-org",
        repository_full_name="owner/repo",
        include=["docs/**"],
        exclude=[],
    )

    assert memory.stored, "expected chunks to be stored"
    meta = memory.stored[0].metadata
    assert meta["source_url"].startswith(
        "https://github.com/owner/repo/blob/main/docs/deploy.md"
    )
    assert meta["page_type"] == "how-to"
    assert meta["section"] == meta["heading_path"]
