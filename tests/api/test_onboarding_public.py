"""Onboarding routes for the public_documentation source (Stage 0).

Same state machine and transitions as the GitHub path; no GitHub App
installation required. Plan: plans/2026-09-20-tavily-rag.md (Task 8).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.routes import onboarding
from tests.fakes import FakeTavilyClient

ROOT = "https://docs.example.com"


def _state(selected: dict, state: str = "GITHUB_CONNECTED") -> MagicMock:
    st = MagicMock()
    repos = st.dependencies.repositories
    repos.onboarding.get = AsyncMock(
        return_value={
            "org_id": "test-org",
            "state": state,
            "selected_repository": selected,
        }
    )
    repos.onboarding.upsert = AsyncMock(return_value={})
    repos.onboarding.mark_step = AsyncMock(return_value={})
    repos.repository_config.upsert = AsyncMock(return_value={})
    return st


@pytest.fixture()
def settings() -> SimpleNamespace:
    return SimpleNamespace(
        rq_enabled=False,
        tavily_api_key="tvly-test-key",
        tavily_base_url="https://api.tavily.com",
        tavily_request_timeout_seconds=60,
        tavily_max_concurrency=4,
        tavily_public_ingestion_enabled=True,
    )


def _client(state: MagicMock, settings: SimpleNamespace) -> TestClient:
    from draftly.app.api.auth import get_verified_token

    app = FastAPI()
    app.include_router(onboarding.router)
    app.dependency_overrides[get_verified_token] = lambda: {
        "sub": "tester",
        "org_id": "test-org",
    }
    state.settings = settings
    app.state.draftly = state
    return TestClient(app)


def _public_body(**overrides) -> dict:
    body = {
        "full_name": ROOT,
        "source_type": "public_documentation",
        "documentation_config": {"root_url": ROOT + "/"},
    }
    body.update(overrides)
    return body


def test_select_repository_github_still_requires_installation(
    settings: SimpleNamespace,
) -> None:
    state = _state({})
    client = _client(state, settings)

    resp = client.post("/onboarding/repository", json={"full_name": "owner/repo"})

    assert resp.status_code == 409
    assert "GitHub not connected" in resp.json()["detail"]


def test_select_repository_public_skips_github_access(
    settings: SimpleNamespace,
) -> None:
    state = _state({})
    client = _client(state, settings)

    resp = client.post("/onboarding/repository", json=_public_body())

    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "REPOSITORY_SELECTED"
    saved = state.dependencies.repositories.onboarding.upsert.await_args.kwargs[
        "selected_repository"
    ]
    assert saved["source_type"] == "public_documentation"
    assert saved["documentation_config"]["root_url"] == ROOT + "/"


def test_select_repository_public_rejects_http_root(
    settings: SimpleNamespace,
) -> None:
    state = _state({})
    client = _client(state, settings)

    body = _public_body(documentation_config={"root_url": "http://docs.example.com/"})
    resp = client.post("/onboarding/repository", json=body)

    assert resp.status_code == 422


def test_select_repository_public_missing_config_rejected(
    settings: SimpleNamespace,
) -> None:
    state = _state({})
    client = _client(state, settings)

    resp = client.post(
        "/onboarding/repository",
        json={"full_name": ROOT, "source_type": "public_documentation"},
    )

    assert resp.status_code == 422


def test_select_repository_public_rejected_when_flag_off(
    settings: SimpleNamespace,
) -> None:
    settings.tavily_public_ingestion_enabled = False
    state = _state({})
    client = _client(state, settings)

    resp = client.post("/onboarding/repository", json=_public_body())

    assert resp.status_code == 409


def test_select_repository_public_rejected_without_key(
    settings: SimpleNamespace,
) -> None:
    settings.tavily_api_key = None
    state = _state({})
    client = _client(state, settings)

    resp = client.post("/onboarding/repository", json=_public_body())

    assert resp.status_code == 409


def _public_selected(**overrides) -> dict:
    selected = {
        "full_name": ROOT,
        "source_type": "public_documentation",
        "documentation_config": {"root_url": ROOT + "/"},
    }
    selected.update(overrides)
    return selected


def test_discover_public_returns_candidates(settings: SimpleNamespace) -> None:
    state = _state(_public_selected(), state="REPOSITORY_SELECTED")
    client = _client(state, settings)
    fake = FakeTavilyClient(
        map_urls=[f"{ROOT}/a", f"{ROOT}/b", "https://other.example.com/x"]
    )

    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        resp = client.post("/onboarding/documentation/discover")

    assert resp.status_code == 200, resp.text
    assert resp.json()["candidates"] == [f"{ROOT}/a", f"{ROOT}/b"]
    assert resp.json()["count"] == 2


def test_discover_public_rejected_when_flag_off(settings: SimpleNamespace) -> None:
    settings.tavily_public_ingestion_enabled = False
    state = _state(_public_selected(), state="REPOSITORY_SELECTED")
    client = _client(state, settings)

    resp = client.post("/onboarding/documentation/discover")

    assert resp.status_code == 409


def test_confirm_sources_public_persists_config(settings: SimpleNamespace) -> None:
    state = _state(_public_selected(), state="REPOSITORY_SELECTED")
    client = _client(state, settings)

    resp = client.post(
        "/onboarding/sources", json={"include": ["/docs/.*"], "exclude": ["/internal/.*"]}
    )

    assert resp.status_code == 200, resp.text
    saved = state.dependencies.repositories.onboarding.upsert.await_args.kwargs[
        "selected_repository"
    ]
    assert saved["documentation_config"]["include_paths"] == ["/docs/.*"]
    assert saved["documentation_config"]["exclude_paths"] == ["/internal/.*"]
    # no repositories-table row for public sources (no migration)
    state.dependencies.repositories.repository_config.upsert.assert_not_called()
