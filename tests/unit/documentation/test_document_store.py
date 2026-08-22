"""DocumentStore.upsert_document tests (offline, scripted client)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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


async def test_upsert_document_insert_sets_title_and_document_type():
    client = ScriptedClient(responses=[None, _row(title="Guide", document_type="tutorial")])
    store = DocumentStore(client)

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
    store = DocumentStore(client)

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
    store = DocumentStore(client)

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
