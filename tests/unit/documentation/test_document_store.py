"""DocumentStore.upsert_document tests (offline, scripted client)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

from draftly.integrations.database.client import DatabaseClient
from draftly.integrations.database.document_store import DocumentStore

METADATA: dict[str, Any] = {
    "source_url": "https://github.com/draftly/draftly-docs/blob/main/guide.md",
    "branch": "main",
    "section_count": 4,
    "chunk_count": 3,
}


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


async def test_upsert_document_insert_sets_title_and_document_type():
    client = ScriptedClient(responses=[None, _row(title="Guide", document_type="tutorial")])
    store = DocumentStore(cast(DatabaseClient, client))

    record = await store.upsert_document(
        org_id="demo-org",
        repository="draftly/draftly-docs",
        path="guide.md",
        title="Guide",
        document_type="tutorial",
        content="# Guide",
    )

    assert record["title"] == "Guide"
    assert record["document_type"] == "tutorial"

    insert_sql, insert_args = client.calls[-1]
    assert "INSERT INTO documentation" in insert_sql
    assert "title" in insert_sql
    assert "document_type" in insert_sql
    assert "Guide" in insert_args
    assert "tutorial" in insert_args


async def test_upsert_document_update_refreshes_title_and_document_type_when_provided():
    client = ScriptedClient(
        responses=[
            {"id": "doc-1"},
            _row(title="Updated", document_type="how-to"),
        ]
    )
    store = DocumentStore(cast(DatabaseClient, client))

    record = await store.upsert_document(
        repository="draftly/draftly-docs",
        path="guide.md",
        title="Updated",
        document_type="how-to",
        content="# Updated",
    )

    assert record["title"] == "Updated"
    assert record["document_type"] == "how-to"

    update_sql, update_args = client.calls[-1]
    assert update_sql.startswith("UPDATE documentation")
    assert "title = $" in update_sql
    assert "document_type = $" in update_sql
    assert "Updated" in update_args
    assert "how-to" in update_args


async def test_upsert_document_without_title_leaves_existing_columns_untouched():
    client = ScriptedClient(responses=[{"id": "doc-1"}, _row()])
    store = DocumentStore(cast(DatabaseClient, client))

    await store.upsert_document(
        repository="draftly/draftly-docs",
        path="guide.md",
        content="# Rewritten",
    )

    update_sql, _ = client.calls[-1]
    assert update_sql.startswith("UPDATE documentation")
    assert "title = $" not in update_sql
    assert "document_type = $" not in update_sql
    assert "content = $" in update_sql


def _assert_metadata_serialized_for_jsonb(sql: str, args: tuple) -> None:
    """asyncpg requires JSONB params pre-serialized as strings (R-A/R-B)."""
    assert "::jsonb" in sql
    assert json.dumps(METADATA) in args
    assert not any(isinstance(arg, dict) for arg in args)


async def test_upsert_document_insert_serializes_metadata_for_jsonb():
    client = ScriptedClient(responses=[None, _row()])
    store = DocumentStore(cast(DatabaseClient, client))

    await store.upsert_document(
        org_id="demo-org",
        repository="draftly/draftly-docs",
        path="guide.md",
        title="Guide",
        content="# Guide",
        status="indexed",
        metadata=METADATA,
    )

    insert_sql, insert_args = client.calls[-1]
    assert "INSERT INTO documentation" in insert_sql
    _assert_metadata_serialized_for_jsonb(insert_sql, insert_args)


async def test_upsert_document_update_serializes_metadata_for_jsonb():
    client = ScriptedClient(responses=[{"id": "doc-1"}, _row()])
    store = DocumentStore(cast(DatabaseClient, client))

    await store.upsert_document(
        repository="draftly/draftly-docs",
        path="guide.md",
        title="Guide v2",
        content="# Guide v2",
        status="indexed",
        metadata=METADATA,
    )

    update_sql, update_args = client.calls[-1]
    assert "UPDATE documentation" in update_sql
    _assert_metadata_serialized_for_jsonb(update_sql, update_args)


async def test_insert_serializes_metadata_for_jsonb():
    client = ScriptedClient(responses=[_row()])
    store = DocumentStore(cast(DatabaseClient, client))

    await store.insert(
        org_id="demo-org",
        path="guide.md",
        title="Guide",
        content="# Guide",
        document_type="tutorial",
        metadata=METADATA,
    )

    insert_sql, insert_args = client.calls[-1]
    assert "INSERT INTO documentation" in insert_sql
    _assert_metadata_serialized_for_jsonb(insert_sql, insert_args)
