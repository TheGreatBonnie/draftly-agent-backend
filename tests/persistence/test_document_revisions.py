from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from draftly.persistence.repositories.document_revisions import (
    DocumentRevisionRepository,
    RevisionConflict,
)
from draftly.persistence.repositories.documents import DocumentRepository

DOC_ID = "11111111-1111-1111-1111-111111111111"
REVISION_ID = "22222222-2222-2222-2222-222222222222"


def _document(*, org_id: str = "org-1", source_hash: str | None = "sha-old") -> dict:
    return {
        "id": DOC_ID,
        "org_id": org_id,
        "source_hash": source_hash,
        "updated_at": datetime(2026, 9, 1, tzinfo=UTC),
        "draft_revision_id": None,
    }


class FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database

    async def fetchrow(self, query: str, *args):
        if "FOR UPDATE" in query and "FROM documentation" in query:
            document_id, org_id = args[:2]
            document = self.database.documents.get(document_id)
            if document and document["org_id"] == org_id:
                return document
            return None
        if "MAX(revision_number)" in query:
            document_id, org_id = args
            numbers = [
                revision["revision_number"]
                for revision in self.database.revisions.values()
                if revision["document_id"] == document_id and revision["org_id"] == org_id
            ]
            return {"revision_number": max(numbers, default=0)}
        if "FROM documentation_revisions" in query and "COUNT" not in query:
            revision_id, document_id, org_id = args[:3]
            revision = self.database.revisions.get(revision_id)
            if (
                revision
                and revision["document_id"] == document_id
                and revision["org_id"] == org_id
            ):
                return revision
            return None
        if "INSERT INTO documentation_revisions" in query:
            (
                revision_id,
                document_id,
                org_id,
                revision_number,
                origin,
                title,
                content,
                base_source_hash,
                base_document_updated_at,
                created_by,
            ) = args
            revision = {
                "id": revision_id,
                "document_id": document_id,
                "org_id": org_id,
                "revision_number": revision_number,
                "origin": origin,
                "status": "draft",
                "title": title,
                "content": content,
                "base_source_hash": base_source_hash,
                "created_by": created_by,
                "created_at": datetime(2026, 9, 2, tzinfo=UTC),
            }
            self.database.revisions[revision_id] = revision
            return revision
        return None

    async def fetch(self, query: str, *args):
        if "FROM documentation_revisions" in query:
            document_id, org_id, limit = args[:3]
            return [
                revision
                for revision in self.database.revisions.values()
                if revision["document_id"] == document_id and revision["org_id"] == org_id
            ][:limit]
        return []

    async def execute(self, query: str, *args):
        if "UPDATE documentation_revisions" in query:
            revision_id, document_id, org_id = args
            revision = self.database.revisions.get(revision_id)
            if revision and revision["document_id"] == document_id and revision["org_id"] == org_id:
                revision["status"] = "superseded"
        if "UPDATE documentation" in query:
            revision_id, document_id, org_id = args
            document = self.database.documents[document_id]
            if document["org_id"] == org_id:
                document["draft_revision_id"] = revision_id


class FakeDatabase:
    def __init__(self) -> None:
        self.documents = {DOC_ID: _document()}
        self.revisions: dict[str, dict] = {}

    @asynccontextmanager
    async def transaction(self, **kwargs):
        yield FakeConnection(self)


@pytest.mark.asyncio
async def test_create_draft_validates_source_and_updates_current_pointer() -> None:
    repository = DocumentRevisionRepository(database=FakeDatabase())

    revision = await repository.create_draft(
        document_id=DOC_ID,
        org_id="org-1",
        content="# Updated",
        title="Updated title",
        base_source_hash="sha-old",
        base_revision_id=None,
        created_by="user-1",
    )

    assert revision.revision_number == 1
    assert revision.status == "draft"
    assert revision.origin == "manual"
    assert repository.database.documents[DOC_ID]["draft_revision_id"] == revision.id


@pytest.mark.asyncio
async def test_create_draft_rejects_stale_source_before_insert() -> None:
    database = FakeDatabase()
    repository = DocumentRevisionRepository(database=database)
    database.documents[DOC_ID]["source_hash"] = "sha-new"

    with pytest.raises(RevisionConflict):
        await repository.create_draft(
            document_id=DOC_ID,
            org_id="org-1",
            content="# Stale",
            title=None,
            base_source_hash="sha-old",
            base_revision_id=None,
            created_by="user-1",
        )

    assert database.revisions == {}


@pytest.mark.asyncio
async def test_foreign_document_is_not_found() -> None:
    database = FakeDatabase()
    repository = DocumentRevisionRepository(database=database)

    with pytest.raises(RevisionConflict, match="not found"):
        await repository.create_draft(
            document_id=DOC_ID,
            org_id="org-foreign",
            content="# No access",
            title=None,
            base_source_hash="sha-old",
            base_revision_id=None,
            created_by="user-1",
        )


@pytest.mark.asyncio
async def test_restore_supersedes_current_draft_and_creates_new_revision() -> None:
    database = FakeDatabase()
    database.revisions[REVISION_ID] = {
        "id": REVISION_ID,
        "document_id": DOC_ID,
        "org_id": "org-1",
        "revision_number": 1,
        "origin": "manual",
        "status": "draft",
        "title": "Old",
        "content": "# Old",
        "base_source_hash": "sha-old",
        "created_by": "user-1",
        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    database.documents[DOC_ID]["draft_revision_id"] = REVISION_ID
    repository = DocumentRevisionRepository(database=database)

    restored = await repository.restore_revision(
        document_id=DOC_ID,
        revision_id=REVISION_ID,
        org_id="org-1",
        created_by="user-2",
    )

    assert restored.origin == "restore"
    assert restored.revision_number == 2
    assert database.revisions[REVISION_ID]["status"] == "superseded"


class ProjectionStore:
    async def get_for_org(self, *, document_id: str, org_id: str):
        return {"id": document_id, "org_id": org_id, "content": "# detail"}

    async def list_projection_by_org(self, **kwargs):
        return [{"id": DOC_ID, "title": "Docs", "has_draft": True}]


async def test_document_repository_exposes_scoped_detail_and_projection() -> None:
    repository = DocumentRepository(store=ProjectionStore())

    detail = await repository.get_for_org(document_id=DOC_ID, org_id="org-1")
    projection = await repository.list_projection_by_org(
        org_id="org-1", repository=None, status=None, query=None, limit=25, cursor=None
    )

    assert detail["org_id"] == "org-1"
    assert projection[0]["has_draft"] is True
