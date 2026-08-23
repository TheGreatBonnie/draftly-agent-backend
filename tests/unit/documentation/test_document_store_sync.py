"""Sync-focused DocumentStore tests (offline, scripted client)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from draftly.integrations.database.client import DatabaseClient
from draftly.integrations.database.document_store import DocumentStore


@dataclass
class ScriptedClient:
    """Returns scripted rows in order and records every (sql, args) pair."""

    responses: list[Any] = field(default_factory=list)
    calls: list[tuple[str, tuple]] = field(default_factory=list)

    async def fetch_one(self, query: str, *args: Any) -> Any:
        self.calls.append((" ".join(query.split()), args))
        return self.responses.pop(0) if self.responses else None

    async def fetch_all(self, query: str, *args: Any) -> list[Any]:
        self.calls.append((" ".join(query.split()), args))
        rows = self.responses.pop(0) if self.responses else []
        return rows if isinstance(rows, list) else []

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((" ".join(query.split()), args))
        return "OK"


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": "doc-1",
        "org_id": "demo-org",
        "repository": "draftly/draftly-docs",
        "path": "guide.md",
        "title": None,
        "content": "c",
        "document_type": "general",
        "version": 1,
        "commit_sha": None,
        "status": "draft",
        "metadata": {},
        "stale": False,
        "outdated": False,
        "incomplete": False,
        "broken_links": False,
        "unsupported_claims": False,
        "created_at": None,
        "updated_at": None,
    }
    row.update(overrides)
    return row


async def test_upsert_document_persists_sync_columns_on_insert():
    client = ScriptedClient(
        responses=[None, _row(status="indexed", commit_sha="abc123", source_hash="deadbeef")]
    )
    store = DocumentStore(cast(DatabaseClient, client))

    await store.upsert_document(
        org_id="demo-org",
        repository="draftly/draftly-docs",
        path="guide.md",
        content="# Guide",
        status="indexed",
        commit_sha="abc123",
        source_hash="deadbeef",
    )

    insert_sql, insert_args = client.calls[-1]
    assert "INSERT INTO documentation" in insert_sql
    assert "commit_sha" in insert_sql
    assert "source_hash" in insert_sql
    assert "status" in insert_sql
    assert "abc123" in insert_args
    assert "deadbeef" in insert_args
    assert "indexed" in insert_args


async def test_upsert_document_persists_sync_columns_on_update():
    client = ScriptedClient(
        responses=[{"id": "doc-1"}, _row(commit_sha="def456", status="indexed")]
    )
    store = DocumentStore(cast(DatabaseClient, client))

    await store.upsert_document(
        repository="draftly/draftly-docs",
        path="guide.md",
        content="# Guide v2",
        status="indexed",
        commit_sha="def456",
        source_hash="cafebabe",
    )

    update_sql, update_args = client.calls[-1]
    assert "UPDATE documentation" in update_sql
    assert "commit_sha" in update_sql
    assert "source_hash" in update_sql
    assert "status" in update_sql
    assert "def456" in update_args


async def test_get_by_org_and_path_filters_on_org():
    client = ScriptedClient(responses=[_row(path="README.md")])
    store = DocumentStore(cast(DatabaseClient, client))

    doc = await store.get_by_org_and_path(org_id="demo-org", path="README.md")

    assert doc is not None
    assert doc["path"] == "README.md"
    sql, args = client.calls[-1]
    assert "org_id = $1" in sql
    assert args[0] == "demo-org"


async def test_list_by_org_returns_rows_for_org():
    client = ScriptedClient(responses=[[_row(path="a.md"), _row(path="b.md")]])
    store = DocumentStore(cast(DatabaseClient, client))

    docs = await store.list_by_org(org_id="demo-org")

    assert len(docs) == 2
