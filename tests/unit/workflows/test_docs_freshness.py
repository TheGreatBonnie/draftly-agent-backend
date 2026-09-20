"""Public-docs freshness: targeted re-extract with hash semantics.

Content-hash equal → skip; differs → replace; gone after retries → delete
chunks; never-indexed failures stay failed. Deploy lag is absorbed by
bounded retries. Flag off → inert.

Plan: plans/2026-09-20-tavily-rag.md (Task 17).
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from draftly.documentation.source_models import PublicDocumentationConfig
from draftly.workflows.documentation.refresh import (
    handle_pr_merged_for_docs,
    refresh_public_documentation,
)
from tests.fakes import FakeTavilyClient

ROOT = "https://docs.example.com"
U_SAME = f"{ROOT}/same"
U_CHANGED = f"{ROOT}/changed"
U_GONE = f"{ROOT}/gone"
U_NEW_FAIL = f"{ROOT}/never-indexed"


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


class FakeDocuments:
    def __init__(self, rows: dict[str, dict] | None = None) -> None:
        self.rows: dict[str, dict] = dict(rows or {})

    async def get_by_org_and_path(
        self, *, org_id: str, path: str
    ) -> dict[str, Any] | None:
        return self.rows.get(path)

    async def upsert(self, **kwargs: Any) -> dict[str, Any]:
        # Mirror ON CONFLICT UPDATE: keep the existing row id.
        existing = self.rows.get(kwargs["path"], {})
        record = {"id": existing.get("id", f"doc-{len(self.rows)}"), **kwargs}
        self.rows[kwargs["path"]] = record
        return record

    async def delete(self, document_id: str) -> bool:
        for path, row in list(self.rows.items()):
            if row.get("id") == document_id:
                del self.rows[path]
                return True
        return False


class FakeMemory:
    def __init__(self) -> None:
        self.deleted: list[tuple] = []
        self.batches: list[list] = []

    async def delete_by_metadata(
        self, *, namespace: str, key: str, value: str, org_id: str
    ) -> None:
        self.deleted.append((namespace, key, value, org_id))

    async def store_batch(self, items: list) -> list[dict]:
        self.batches.append(items)
        return [{"id": f"m{i}"} for i in range(len(items))]


def _context(documents, memory, *, flag_on: bool = True):
    context = MagicMock()
    context.repositories.documents = documents
    context.memory = memory
    context.config = SimpleNamespace(
        tavily_api_key="tvly-test-key" if flag_on else None,
        tavily_base_url="https://api.tavily.com",
        tavily_request_timeout_seconds=60,
        tavily_research_poll_timeout_seconds=300,
        tavily_max_concurrency=4,
        tavily_credit_budget=None,
        tavily_public_ingestion_enabled=flag_on,
    )
    return context


def _seed() -> FakeDocuments:
    return FakeDocuments(
        rows={
            U_SAME: {
                "id": "doc-same",
                "source_hash": _hash("# Same\n\nBody."),
                "metadata": {"chunk_count": 1},
            },
            U_CHANGED: {
                "id": "doc-changed",
                "source_hash": "stale",
                "metadata": {"chunk_count": 1},
            },
            U_GONE: {
                "id": "doc-gone",
                "source_hash": "stale",
                "metadata": {"chunk_count": 1},
            },
        }
    )


@pytest.mark.asyncio
async def test_hash_equal_skips_hash_differs_replaces_gone_deletes() -> None:
    documents, memory = _seed(), FakeMemory()
    context = _context(documents, memory)
    fake = FakeTavilyClient(
        extract_pages={
            U_SAME: "# Same\n\nBody.",
            U_CHANGED: "# Changed\n\nNew body.",
        }
    )
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        result = await refresh_public_documentation(
            documents=documents,
            memory=memory,
            config=context.config,
            org_id="o",
            urls=[U_SAME, U_CHANGED, U_GONE, U_NEW_FAIL],
            retry_base_seconds=0.01,
        )

    assert result.skipped == 1
    assert result.replaced == 1
    assert result.failed == [U_NEW_FAIL]
    assert result.deleted == [U_GONE]
    # replaced page cycled its chunks; the gone page's chunks are deleted
    # and its row removed; the untouched page was never deleted.
    deleted_ids = [d[2] for d in memory.deleted]
    assert "doc-changed" in deleted_ids
    assert "doc-gone" in deleted_ids
    assert "doc-same" not in deleted_ids
    assert U_GONE not in documents.rows
    assert U_SAME in documents.rows


@pytest.mark.asyncio
async def test_retry_after_deploy_eligible_failures() -> None:
    documents, memory = FakeDocuments(), FakeMemory()
    context = _context(documents, memory)
    fake = FakeTavilyClient(
        extract_sequence={f"{ROOT}/slow": ["", "", "# Slow\n\nArrived."]}
    )
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        result = await refresh_public_documentation(
            documents=documents,
            memory=memory,
            config=context.config,
            org_id="o",
            urls=[f"{ROOT}/slow"],
            max_retries=3,
            retry_base_seconds=0.01,
        )

    assert result.replaced == 1
    assert result.failed == []
    assert len([c for c in fake.calls if c[0] == "extract"]) == 3


@pytest.mark.asyncio
async def test_pr_merged_triggers_resync_of_changed_urls() -> None:
    documents, memory = FakeDocuments(), FakeMemory()
    context = _context(documents, memory)
    fake = FakeTavilyClient(
        extract_pages={f"{ROOT}/a": "# A\n\nBody A."}
    )
    event = {"doc_urls": [f"{ROOT}/a", f"{ROOT}/missing"]}
    corpus = PublicDocumentationConfig(root_url=ROOT + "/")
    with (
        patch(
            "draftly.integrations.tavily.client.TavilyClient", return_value=fake
        ) as client_cls,
        patch(
            "draftly.tools.search._rag.resolve_public_config",
            new=AsyncMock(return_value=corpus),
        ),
    ):
        result = await handle_pr_merged_for_docs(context, "o", event)

    assert result is not None
    assert result.replaced == 1
    assert result.failed == [f"{ROOT}/missing"]
    client_cls.assert_called_once()


@pytest.mark.asyncio
async def test_pr_hook_inert_without_urls() -> None:
    context = _context(FakeDocuments(), FakeMemory())
    with patch(
        "draftly.integrations.tavily.client.TavilyClient"
    ) as client_cls:
        result = await handle_pr_merged_for_docs(context, "o", {"doc_urls": []})

    assert result is None
    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_flag_off_no_freshness_trigger() -> None:
    context = _context(FakeDocuments(), FakeMemory(), flag_on=False)
    with patch(
        "draftly.integrations.tavily.client.TavilyClient"
    ) as client_cls:
        direct = await refresh_public_documentation(
            documents=FakeDocuments(),
            memory=FakeMemory(),
            config=context.config,
            org_id="o",
            urls=[U_SAME],
        )
        hooked = await handle_pr_merged_for_docs(
            context, "o", {"doc_urls": [U_SAME]}
        )

    assert direct.skipped == 0 and direct.replaced == 0
    assert direct.failed == [] and direct.deleted == []
    assert hooked is None
    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_manual_refresh_route() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from draftly.app.api.auth import get_verified_token
    from draftly.app.api.routes import onboarding

    state = MagicMock()
    state.dependencies.repositories.onboarding.get = AsyncMock(
        return_value={
            "org_id": "o",
            "state": "REPOSITORY_SELECTED",
            "selected_repository": {
                "full_name": ROOT,
                "source_type": "public_documentation",
                "documentation_config": {"root_url": ROOT + "/"},
            },
        }
    )
    state.settings = SimpleNamespace(
        tavily_api_key="tvly-test-key",
        tavily_base_url="https://api.tavily.com",
        tavily_request_timeout_seconds=60,
        tavily_max_concurrency=4,
        tavily_public_ingestion_enabled=True,
    )
    app = FastAPI()
    app.include_router(onboarding.router)
    app.dependency_overrides[get_verified_token] = lambda: {
        "sub": "tester",
        "org_id": "o",
    }
    app.state.draftly = state
    client = TestClient(app)

    with (
        patch(
            "draftly.workflows.documentation.refresh.refresh_public_documentation",
            new=AsyncMock(
                return_value=MagicMock(
                    skipped=1, replaced=2, failed=[], deleted=[],
                )
            ),
        ) as refresh_mock,
        patch(
            "draftly.memory.service.MemoryService", return_value=MagicMock()
        ),
    ):
        resp = client.post(
            "/onboarding/documentation/refresh", json={"urls": [f"{ROOT}/a"]}
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["replaced"] == 2
    refresh_mock.assert_awaited_once()
    assert refresh_mock.await_args.kwargs["urls"] == [f"{ROOT}/a"]
