"""Stage 5 recommendations with gap evidence behind TAVILY_RESEARCH_ENABLED.

Public sources only. Failure or invalid output degrades to [] and never
invalidates a completed analysis.

Plan: plans/2026-09-20-tavily-rag.md (Task 13).
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from draftly.documentation.rag_retrieval import RagResult
from draftly.workflows.onboarding.stages import (
    EvaluationResult,
    HealthResult,
    RecommendationList,
    run_recommendations,
)
from tests.fakes import FakeTavilyClient


class StubRagRetrieval:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def retrieve(self, *, query: str, **kwargs):
        confidence = 0.9 if query == "api" else 0.3
        return RagResult(results=[], confidence=confidence, source="local")


def _context(*, flag_on: bool = True):
    context = MagicMock()
    context.model = None
    context.config = SimpleNamespace(
        tavily_api_key="tvly-test-key" if flag_on else None,
        tavily_base_url="https://api.tavily.com",
        tavily_request_timeout_seconds=60,
        tavily_research_poll_timeout_seconds=300,
        tavily_max_concurrency=4,
        tavily_credit_budget=None,
        tavily_research_enabled=flag_on,
    )
    return context


def _results() -> tuple[EvaluationResult, HealthResult]:
    eval_result = EvaluationResult(
        score=0.7,
        dimensions={
            "coverage": 0.7,
            "completeness": 0.7,
            "structure": 0.7,
            "length": 0.7,
        },
    )
    health_result = HealthResult(score=0.68, dimensions={"freshness": 0.5})
    return eval_result, health_result


@pytest.fixture(autouse=True)
def _stub_rag_dependencies():
    with (
        patch(
            "draftly.documentation.rag_retrieval.RagRetrieval", StubRagRetrieval
        ),
        patch(
            "draftly.integrations.database.client.DatabaseClient",
            return_value=MagicMock(),
        ),
        patch(
            "draftly.memory.embeddings.EmbeddingService",
            return_value=MagicMock(),
        ),
    ):
        yield


def _run(context, source_type: str = "public_documentation"):
    eval_result, health_result = _results()
    return run_recommendations(
        context,
        eval_result=eval_result,
        health_result=health_result,
        document_count=10,
        chunk_count=40,
        source_type=source_type,
    )


def _research_files_text(fake: FakeTavilyClient) -> str:
    research = [c for c in fake.calls if c[0] == "research"][0][1]
    assert research["model"] == "mini"
    assert "properties" in research["output_schema"]
    return "\n".join(
        base64.b64decode(f["content_b64"]).decode() for f in research["files"]
    )


def _rec_content() -> dict:
    return {
        "items": [
            {
                "priority": "high",
                "title": "Add usage examples",
                "detail": "Usage coverage is thin.",
                "category": "coverage",
            },
            {
                "priority": "medium",
                "title": "Document changelog",
                "detail": "No changelog found.",
                "category": "coverage",
            },
            {
                "priority": "low",
                "title": "Polish install guide",
                "detail": "Installation is sparse.",
                "category": "completeness",
            },
        ]
    }


@pytest.mark.asyncio
async def test_recommendations_from_research_with_gap_evidence() -> None:
    fake = FakeTavilyClient(research_content=_rec_content())
    context = _context()
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        recs = await _run(context)

    assert len(recs) == 3
    assert recs[0].priority == "high"
    assert recs[0].title == "Add usage examples"
    assert recs[0].detail == "Usage coverage is thin."
    assert recs[0].category == "coverage"
    text = _research_files_text(fake)
    # "api" probed confident; the other coverage topics are gap evidence.
    assert "usage" in text
    assert "changelog" in text


@pytest.mark.asyncio
async def test_recommendation_failure_returns_empty() -> None:
    fake = FakeTavilyClient(fail_codes=["upstream"])
    context = _context()
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        recs = await _run(context)

    assert recs == []


@pytest.mark.asyncio
async def test_recommendation_invalid_output_returns_empty() -> None:
    fake = FakeTavilyClient(research_content="not-a-dict")
    context = _context()
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        recs = await _run(context)

    assert recs == []


@pytest.mark.asyncio
async def test_recommendation_schema_validated() -> None:
    fake = FakeTavilyClient(
        research_content={
            "items": [
                {
                    "priority": "high",
                    "title": "T",
                    "detail": "D",
                    "category": "coverage",
                }
            ]
        }
    )
    context = _context()
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        recs = await _run(context)

    assert [(r.priority, r.title, r.detail, r.category) for r in recs] == [
        ("high", "T", "D", "coverage")
    ]


@pytest.mark.asyncio
async def test_github_source_keeps_legacy_recommendations() -> None:
    context = _context(flag_on=True)
    with (
        patch(
            "draftly.integrations.tavily.client.TavilyClient"
        ) as client_cls,
        patch(
            "draftly.workflows.onboarding.stages.build_draftly_agent",
            return_value=MagicMock(),
        ),
        patch(
            "draftly.workflows.onboarding.stages._llm_generate",
            new=AsyncMock(
                return_value=RecommendationList(
                    items=[
                        {
                            "priority": "high",
                            "title": "Legacy",
                            "detail": "D",
                            "category": "coverage",
                        }
                    ]
                )
            ),
        ),
    ):
        recs = await _run(context, source_type="github_repository")

    client_cls.assert_not_called()
    assert [r.title for r in recs] == ["Legacy"]
