"""Unit tests for onboarding state repository."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

import pytest

from draftly.persistence.repositories.onboarding import OnboardingRepository


@dataclass
class FakeClient:
    responses: list[Any] = field(default_factory=list)
    calls: list[Any] = field(default_factory=list)

    async def fetch_one(self, query, *args):
        self.calls.append(("fetch_one", query, args))
        return self.responses.pop(0) if self.responses else None

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))
        return "OK"


@pytest.mark.asyncio
async def test_get_returns_none_when_not_found():
    client = FakeClient(responses=[None])
    repo = OnboardingRepository(client)
    result = await repo.get("org-123")
    assert result is None


@pytest.mark.asyncio
async def test_get_returns_state_when_found():
    client = FakeClient(
        responses=[
            {
                "org_id": "org-123",
                "state": "WORKSPACE_CREATED",
                "completed_steps": ["workspace"],
                "failure": None,
                "selected_repository": None,
            }
        ]
    )
    repo = OnboardingRepository(client)
    result = await repo.get("org-123")
    assert result is not None
    assert result["state"] == "WORKSPACE_CREATED"


@pytest.mark.asyncio
async def test_get_converts_driver_row_to_plain_dict():
    # asyncpg fetchrow returns a Record (mapping, not dict). Mirrors production.
    row = MappingProxyType(
        {
            "org_id": "org-123",
            "state": "WORKSPACE_CREATED",
            "updated_at": datetime(2026, 8, 24, 9, 41, 13, tzinfo=UTC),
        }
    )
    client = FakeClient(responses=[row])
    repo = OnboardingRepository(client)
    result = await repo.get("org-123")
    assert isinstance(result, dict)
    assert result["state"] == "WORKSPACE_CREATED"


@pytest.mark.asyncio
async def test_get_decodes_json_text_columns():
    # JSON-as-text columns come back as strings from the driver.
    row = MappingProxyType(
        {
            "org_id": "org-123",
            "state": "WORKSPACE_CREATED",
            "completed_steps": '["workspace"]',
            "failure": None,
            "selected_repository": '{"workspace_name": "Authly"}',
        }
    )
    client = FakeClient(responses=[row])
    repo = OnboardingRepository(client)
    result = await repo.get("org-123")
    assert result["completed_steps"] == ["workspace"]
    assert result["selected_repository"] == {"workspace_name": "Authly"}
    assert result["failure"] is None


@pytest.mark.asyncio
async def test_upsert_inserts_new_state():
    client = FakeClient(responses=[None])
    repo = OnboardingRepository(client)
    await repo.upsert("org-123", state="NOT_STARTED")
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_mark_step_adds_step_atomically():
    # Atomic single-statement append: one execute, then a read-back.
    client = FakeClient(
        responses=[
            {"org_id": "org-123", "completed_steps": ["workspace", "github"]},
        ]
    )
    repo = OnboardingRepository(client)
    result = await repo.mark_step("org-123", "github")
    assert "github" in (result.get("completed_steps") or [])
    sql, _args = client.calls[0][1:]
    assert "ON CONFLICT (org_id)" in sql
    assert "EXCLUDED.completed_steps" in sql
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_mark_failed_sets_failure():
    client = FakeClient(responses=[None])
    repo = OnboardingRepository(client)
    await repo.mark_failed("org-123", "initialize", {"detail": "auth failed"})
    assert len(client.calls) == 2
