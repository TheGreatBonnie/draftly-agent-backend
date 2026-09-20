"""live_docs_search: flag-gated Tavily fallback with host+prefix filtering.

Plan: plans/2026-09-20-tavily-rag.md (Task 15).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import draftly.persistence.repositories  # noqa: F401  (cycle order, see test_search_repoint.py)
import draftly.tools.search.live_docs_search  # noqa: F401
from tests.fakes import FakeTavilyClient

# The tools package __init__ shadows submodule names with the decorated
# functions; sys.modules holds the real module object.
live_mod = sys.modules["draftly.tools.search.live_docs_search"]

ROOT = "https://docs.example.com"


def _scope(org_id: str = "o"):
    from draftly.memory.scope import (
        MemoryScope,
        reset_memory_scope,
        set_memory_scope,
    )

    token = set_memory_scope(MemoryScope(org_id=org_id, namespace="documents"))
    return token, reset_memory_scope


def _public_row(root: str = ROOT) -> dict:
    return {
        "org_id": "o",
        "state": "REPOSITORY_SELECTED",
        "selected_repository": {
            "full_name": root,
            "source_type": "public_documentation",
            "documentation_config": {"root_url": root + "/"},
        },
    }


def _onboarding_repo(row: dict | None):
    repo = MagicMock()
    repo.get = AsyncMock(return_value=row)
    return repo


@pytest.mark.asyncio
async def test_flag_off_returns_empty_no_client(monkeypatch) -> None:
    monkeypatch.delenv("TAVILY_LIVE_FALLBACK_ENABLED", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    token, reset = _scope()
    try:
        with patch(
            "draftly.integrations.tavily.client.TavilyClient"
        ) as client_cls:
            results = await live_mod.live_docs_search(query="q", limit=5)
    finally:
        reset(token)

    assert results == []
    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_no_public_config_returns_empty(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_LIVE_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    token, reset = _scope()
    try:
        with (
            patch(
                "draftly.persistence.repositories.onboarding.OnboardingRepository",
                return_value=_onboarding_repo(None),
            ),
            patch(
                "draftly.integrations.tavily.client.TavilyClient"
            ) as client_cls,
        ):
            results = await live_mod.live_docs_search(query="q", limit=5)
    finally:
        reset(token)

    assert results == []
    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_returns_filtered_host_and_prefix(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_LIVE_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    fake = FakeTavilyClient(
        search_results=[
            {"title": "A", "url": f"{ROOT}/docs/a", "content": "alpha"},
            {"title": "Sibling", "url": f"{ROOT}/blog/b", "content": "beta"},
            {"title": "Off-host", "url": "https://other.dev/x", "content": "gamma"},
        ]
    )
    token, reset = _scope()
    try:
        with (
            patch(
                "draftly.persistence.repositories.onboarding.OnboardingRepository",
                return_value=_onboarding_repo(_public_row(f"{ROOT}/docs")),
            ),
            patch(
                "draftly.integrations.tavily.client.TavilyClient",
                return_value=fake,
            ),
        ):
            results = await live_mod.live_docs_search(query="q", limit=5)
    finally:
        reset(token)

    assert [r["source_url"] for r in results] == [f"{ROOT}/docs/a"]
    assert results[0]["metadata"]["source"] == "tavily"
    call = [c for c in fake.calls if c[0] == "search"][0]
    assert call[1] == "docs.example.com documentation: q"
    assert call[2]["include_domains"] == ["docs.example.com"]
    assert call[2]["include_domains_mode"] == "restrict"
    assert call[2]["max_results"] == 5


def test_tool_registered_in_documentation_group() -> None:
    from draftly.app.composition import tools as tools_mod
    from draftly.tools.search.live_docs_search import live_docs_search

    assert live_docs_search in tools_mod._DOCUMENTATION_TOOLS


def test_tool_in_registry_and_all_tools() -> None:
    from draftly.app.composition.tools import build_tools
    from draftly.tools.search.live_docs_search import live_docs_search

    registry = build_tools()

    assert registry.live_docs_search == [live_docs_search]
    assert live_docs_search in registry.all_tools
    assert live_docs_search in registry.documentation


def test_tool_exported_from_search_package() -> None:
    from draftly.tools import search as search_pkg

    assert "live_docs_search" in search_pkg.__all__


def test_context_agent_allowlisted_in_catalog() -> None:
    from draftly.agents.catalog import AGENT_CATALOG

    context_agent = next(a for a in AGENT_CATALOG if a.id == "context_agent")
    assert "live_docs_search" in context_agent.tool_keys


def test_docs_researcher_receives_live_tool() -> None:
    import draftly.agents.subagents as subagents
    from draftly.tools.search.live_docs_search import live_docs_search

    tools = SimpleNamespace(
        github_intelligence=[MagicMock()],
        slack_search=MagicMock(),
        slack_get_thread=MagicMock(),
        discord_search=MagicMock(),
        discord_get_thread=MagicMock(),
        semantic_search=[MagicMock()],
        keyword_search=[MagicMock()],
        hybrid_search=[MagicMock()],
        live_docs_search=[live_docs_search],
    )
    with (
        patch.object(subagents, "build_github_researcher", return_value=MagicMock()),
        patch.object(subagents, "build_slack_researcher", return_value=MagicMock()),
        patch.object(subagents, "build_discord_researcher", return_value=MagicMock()),
        patch.object(subagents, "Swarm", return_value=MagicMock()) as swarm_cls,
        patch.object(
            subagents, "build_docs_researcher", return_value=MagicMock()
        ) as docs_builder,
    ):
        subagents.build_research_swarm(MagicMock(), tools)

    docs_tools = docs_builder.call_args.args[1]
    # Registry attrs are per-tool lists; the swarm receives one entry each.
    flat = [t for group in docs_tools for t in (group if isinstance(group, list) else [group])]
    assert live_docs_search in flat
    swarm_cls.assert_called_once()
