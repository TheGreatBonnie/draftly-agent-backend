"""Stage 3 stratified eval behind TAVILY_RESEARCH_ENABLED (public only).

Deterministic heuristics unchanged; the 25-doc semantic sample becomes a
stratified retrieval scored by one Research call. Blend 0.4/0.6 unchanged;
research failure degrades to pure heuristics.

Plan: plans/2026-09-20-tavily-rag.md (Task 12).
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from draftly.workflows.onboarding.stages import (
    EvaluationScores,
    run_initial_evaluation,
)
from tests.fakes import FakeTavilyClient


def _doc(content: str, *, title: str = "", page_type: str = "index") -> dict:
    return {
        "id": f"doc-{title or content[:8]}",
        "content": content,
        "metadata": {"title": title, "page_type": page_type},
    }


def _context(docs: list, *, flag_on: bool = True):
    context = MagicMock()
    context.model = None
    context.memory.recall = AsyncMock(return_value=docs)
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


def _run(context, source_type: str = "public_documentation"):
    return run_initial_evaluation(
        context, org_id="o", publish=AsyncMock(), source_type=source_type
    )


def _files_text(fake: FakeTavilyClient) -> str:
    research = [c for c in fake.calls if c[0] == "research"][0][1]
    return "\n".join(
        base64.b64decode(f["content_b64"]).decode() for f in research["files"]
    )


@pytest.mark.asyncio
async def test_stratified_sample_covers_page_types() -> None:
    docs = [
        _doc("# Ref A\n\napi reference.", title="Ref A", page_type="reference"),
        _doc("# How B\n\nguide steps.", title="How B", page_type="how-to"),
        _doc("plain words here", title="", page_type="index"),
    ]
    fake = FakeTavilyClient(
        research_content={
            "coverage": 0.5,
            "completeness": 0.5,
            "structure": 0.5,
            "length": 0.5,
        }
    )
    context = _context(docs)
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        await _run(context)

    text = _files_text(fake)
    assert "Ref A" in text
    assert "How B" in text
    assert "plain words here" in text


@pytest.mark.asyncio
async def test_semantic_dimension_from_research() -> None:
    docs = [_doc("plain words here", title="")]
    fake = FakeTavilyClient(
        research_content={
            "coverage": 0.8,
            "completeness": 0.6,
            "structure": 0.7,
            "length": 0.9,
        }
    )
    context = _context(docs)
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        result = await _run(context)

    # Heuristics are all zero for this doc; dims = 0.6 * research.
    assert result.dimensions["coverage"] == pytest.approx(0.48)
    assert result.dimensions["completeness"] == pytest.approx(0.36)
    assert result.dimensions["structure"] == pytest.approx(0.42)
    assert result.dimensions["length"] == pytest.approx(0.54)
    assert result.score == pytest.approx(
        0.3 * 0.48 + 0.3 * 0.36 + 0.2 * 0.42 + 0.2 * 0.54
    )


@pytest.mark.asyncio
async def test_blend_unchanged_040_060_and_weights() -> None:
    content = "# Guide\n\napi " + "word " * 210 + "\n```code```\n[a](b)\n"
    docs = [_doc(content, title="Guide")]
    fake = FakeTavilyClient(
        research_content={
            "coverage": 0.5,
            "completeness": 0.5,
            "structure": 0.5,
            "length": 0.5,
        }
    )
    context = _context(docs)
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        result = await _run(context)

    # Heuristics all 1.0; dims = 0.4*1.0 + 0.6*0.5 = 0.7; weights 30/30/20/20.
    for dim in ("coverage", "completeness", "structure", "length"):
        assert result.dimensions[dim] == pytest.approx(0.7)
    assert result.score == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_research_failure_falls_back_to_heuristics() -> None:
    content = "# Guide\n\napi " + "word " * 210 + "\n```code```\n[a](b)\n"
    docs = [_doc(content, title="Guide")]
    fake = FakeTavilyClient(fail_codes=["upstream"])
    context = _context(docs)
    with patch(
        "draftly.integrations.tavily.client.TavilyClient", return_value=fake
    ):
        result = await _run(context)

    for dim in ("coverage", "completeness", "structure", "length"):
        assert result.dimensions[dim] == pytest.approx(1.0)
    assert result.score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_credit_limit_raises() -> None:
    from draftly.integrations.tavily.errors import TavilyError, TavilyErrorCode

    docs = [_doc("plain words here", title="")]
    fake = FakeTavilyClient(fail_codes=["credit_limit"])
    context = _context(docs)
    with (
        patch(
            "draftly.integrations.tavily.client.TavilyClient", return_value=fake
        ),
        pytest.raises(TavilyError) as exc_info,
    ):
        await _run(context)

    assert exc_info.value.code is TavilyErrorCode.CREDIT_LIMIT


@pytest.mark.asyncio
async def test_github_source_keeps_legacy_eval() -> None:
    docs = [_doc("plain words here", title="")]
    context = _context(docs, flag_on=True)
    with (
        patch(
            "draftly.integrations.tavily.client.TavilyClient"
        ) as client_cls,
        patch(
            "draftly.workflows.onboarding.stages._agent_pool", return_value=None
        ),
    ):
        with patch(
            "draftly.workflows.onboarding.stages._llm_generate",
            new=AsyncMock(
                return_value=EvaluationScores(
                    coverage=0.5, completeness=0.5, structure=0.5, length=0.5
                )
            ),
        ):
            result = await _run(context, source_type="github_repository")

    client_cls.assert_not_called()
    assert result.score >= 0.0
