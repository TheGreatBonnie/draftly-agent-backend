"""PR workflow doc-retrieval routing: RAG index + flag-gated live fallback.

The context/impact agents and the docs researcher receive the re-pointed
search tools plus live_docs_search; docs grounding carries no repo-token
tools. Graph topology and the citation rubric are unchanged.

Plan: plans/2026-09-20-tavily-rag.md (Task 16).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from draftly.integrations.strands import graph as strands_graph


class _TolerantModel:
    """Duck-type stand-in absorbing Strands Model attribute probes."""

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


@dataclass
class RecordingResolver:
    roles: list[str] = field(default_factory=list)

    def for_role(self, role: str, **_: Any) -> _TolerantModel:
        self.roles.append(role)
        return _TolerantModel()

    def __getattr__(self, name: str):
        if name.startswith("__") or name == "_roles":
            raise AttributeError(name)
        return None


@dataclass
class FakeToolsRegistry:
    """Every attribute is a tool group (iterable) like the real registry."""

    def __getattr__(self, name: str) -> list:
        def _tool() -> None:
            raise AssertionError("tools are never invoked during build")

        _tool.__name__ = f"fake_{name}"
        return [_tool]


def _builder():
    return strands_graph._BUILDERS["pull_request"]


def _tool_names(tools: list) -> set[str]:
    names = set()
    for tool in tools:
        names.add(getattr(tool, "__name__", repr(tool)))
    return names


def _build_with_stub_agents(grounding: str = "local"):
    import draftly.agents.documentation.analyzer as analyzer_mod
    import draftly.agents.documentation.context as context_mod

    with (
        patch.object(
            context_mod, "build_doc_context_agent", return_value=MagicMock()
        ) as context_builder,
        patch.object(
            analyzer_mod, "build_impact_agent", return_value=MagicMock()
        ) as impact_builder,
    ):
        _builder()(
            session_manager=None,
            tools_registry=FakeToolsRegistry(),
            model=RecordingResolver(),
            grounding=grounding,
        )
    return context_builder, impact_builder


@pytest.mark.parametrize("grounding", ["local", "github", "docs"])
def test_context_tools_include_live_docs_search(grounding: str) -> None:
    context_builder, _ = _build_with_stub_agents(grounding)

    names = _tool_names(context_builder.call_args.args[1])

    assert "fake_live_docs_search" in names
    assert "fake_semantic_search" in names
    assert "fake_keyword_search" in names
    assert "fake_hybrid_search" in names


@pytest.mark.parametrize("grounding", ["local", "github", "docs"])
def test_impact_tools_include_live_docs_search(grounding: str) -> None:
    _, impact_builder = _build_with_stub_agents(grounding)

    names = _tool_names(impact_builder.call_args.args[1])

    assert "fake_live_docs_search" in names
    assert "fake_semantic_search" in names


def test_docs_grounding_has_no_repo_token_tools() -> None:
    context_builder, _ = _build_with_stub_agents("docs")

    names = _tool_names(context_builder.call_args.args[1])

    # No repo-token tools in docs mode: index + live fallback only.
    assert not any(n.startswith("fake_github_") for n in names)
    assert "fake_live_docs_search" in names


def test_research_swarm_docs_tools_include_live() -> None:
    import draftly.agents.documentation.research_swarm as swarm_mod

    with (
        patch.object(
            swarm_mod, "build_docs_researcher", return_value=MagicMock()
        ) as docs_builder,
        # MagicMock agents look sessionful to Swarm; construction is not
        # under test here (covered by the graph-build tests above).
        patch.object(swarm_mod, "Swarm", return_value=MagicMock()),
    ):
        # _TolerantModel (not MagicMock): Swarm rejects sessionful models.
        swarm_mod.build_doc_research_swarm(
            _TolerantModel(), FakeToolsRegistry(), grounding="docs"
        )

    tools = docs_builder.call_args.args[1]
    flat = [t for group in tools for t in (group if isinstance(group, list) else [group])]
    assert {getattr(t, "__name__", "") for t in flat} >= {
        "fake_semantic_search",
        "fake_keyword_search",
        "fake_hybrid_search",
        "fake_live_docs_search",
    }


def test_retrieval_results_carry_url_for_citations() -> None:
    """RAG-backed doc results carry genuine URLs for citation coverage."""
    from draftly.documentation.rag_retrieval import RagResult

    result = RagResult(
        results=[
            {
                "id": "m1",
                "content": "c",
                "metadata": {},
                "url": "https://docs.example.com/a",
                "source_url": "https://docs.example.com/a",
                "score": 0.9,
            }
        ],
        confidence=0.9,
        source="local",
    )

    assert result.results[0]["source_url"].startswith("https://")
    assert result.results[0]["url"] == result.results[0]["source_url"]
