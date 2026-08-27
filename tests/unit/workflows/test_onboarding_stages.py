"""Unit tests for onboarding initialization stage functions."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from draftly.workflows.onboarding.stages import (
    KnowledgeExtractionResult,
    run_knowledge_construction,
)


@pytest.mark.asyncio
async def test_knowledge_construction_extracts_from_chunks():
    """Should extract knowledge from document chunks and return counts."""
    context = MagicMock()
    context.memory.recall = AsyncMock(return_value=[
        {"id": "chunk-1", "content": "Use `npm install` to install dependencies.", "metadata": {}},
        {"id": "chunk-2", "content": "Run `npm start` to start the dev server.", "metadata": {}},
    ])
    context.memory.remember = AsyncMock(return_value={"id": "k-1"})
    context.docgraph.link = AsyncMock(return_value={"id": "edge-1"})
    context.candidates.enqueue = AsyncMock(return_value={"id": "c-1"})
    publish = AsyncMock()

    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(return_value=(
            '{"facts": ["Install via npm"], '
            '"relationships": [{"source": "npm", "target": "install", "type": "DOCUMENTED_BY"}], '
            '"procedures": [{"steps": ["Run npm start"]}]}'
        ))

        result = await run_knowledge_construction(
            context, org_id="test-org", publish=publish,
        )

    assert isinstance(result, KnowledgeExtractionResult)
    assert result.knowledge_count >= 0
    assert result.relationship_count >= 0
    assert result.candidate_count >= 0


@pytest.mark.asyncio
async def test_knowledge_construction_maps_invalid_relation_type():
    """Invalid relation types should be mapped to DERIVED_FROM."""
    context = MagicMock()
    context.memory.recall = AsyncMock(return_value=[
        {"id": "chunk-1", "content": "The npm package contains utilities.", "metadata": {}},
    ])
    context.memory.remember = AsyncMock(return_value={"id": "k-1"})
    context.docgraph.link = AsyncMock(return_value={"id": "edge-1"})
    context.candidates.enqueue = AsyncMock(return_value={"id": "c-1"})
    publish = AsyncMock()

    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(return_value=(
            '{"facts": ["Contains utils"], '
            '"relationships": [{"source": "npm", "target": "utils", "type": "contains"}], '
            '"procedures": []}'
        ))

        await run_knowledge_construction(
            context, org_id="test-org", publish=publish,
        )

    context.docgraph.link.assert_awaited_once()
    call_kwargs = context.docgraph.link.call_args
    assert call_kwargs.kwargs["relation_type"] == "DERIVED_FROM"


@pytest.mark.asyncio
async def test_knowledge_construction_preserves_valid_relation_type():
    """Valid relation types should pass through unchanged."""
    context = MagicMock()
    context.memory.recall = AsyncMock(return_value=[
        {"id": "chunk-1", "content": "Auth module implements JWT.", "metadata": {}},
    ])
    context.memory.remember = AsyncMock(return_value={"id": "k-1"})
    context.docgraph.link = AsyncMock(return_value={"id": "edge-1"})
    context.candidates.enqueue = AsyncMock(return_value={"id": "c-1"})
    publish = AsyncMock()

    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(return_value=(
            '{"facts": ["Auth uses JWT"], '
            '"relationships": [{"source": "auth", "target": "jwt", "type": "IMPLEMENTS"}], '
            '"procedures": []}'
        ))

        await run_knowledge_construction(
            context, org_id="test-org", publish=publish,
        )

    context.docgraph.link.assert_awaited_once()
    call_kwargs = context.docgraph.link.call_args
    assert call_kwargs.kwargs["relation_type"] == "IMPLEMENTS"
    """Should return zero counts when no chunks exist."""
    context = MagicMock()
    context.memory.recall = AsyncMock(return_value=[])
    publish = AsyncMock()

    result = await run_knowledge_construction(
        context, org_id="test-org", publish=publish,
    )

    assert result.knowledge_count == 0
    assert result.relationship_count == 0
    assert result.candidate_count == 0
    assert result.failed_chunks == []


@pytest.mark.asyncio
async def test_knowledge_construction_skips_failed_chunks():
    """Should skip chunks that timeout or fail extraction, not abort."""
    context = MagicMock()
    context.memory.recall = AsyncMock(return_value=[
        {"id": "chunk-ok", "content": "Good content about APIs.", "metadata": {}},
        {"id": "chunk-bad", "content": "", "metadata": {}},
    ])
    context.memory.remember = AsyncMock(return_value={"id": "k-1"})
    context.docgraph.link = AsyncMock(return_value={"id": "edge-1"})
    context.candidates.enqueue = AsyncMock(return_value={"id": "c-1"})
    publish = AsyncMock()

    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(side_effect=[
            '{"facts": ["APIs exist"], "relationships": [], "procedures": []}',
            RuntimeError("chunk failed"),
        ])

        result = await run_knowledge_construction(
            context, org_id="test-org", publish=publish,
        )

    assert result.knowledge_count >= 0
    assert "chunk-bad" in result.failed_chunks


@pytest.mark.asyncio
async def test_initial_evaluation_scores_corpus():
    """Should score documentation corpus on 4 dimensions using heuristics only."""
    from draftly.workflows.onboarding.stages import run_initial_evaluation

    context = MagicMock()
    context.model = None  # Disable LLM to test heuristic-only path
    context.memory.recall = AsyncMock(return_value=[
        {
            "id": "doc-1",
            "content": (
                "# Getting Started\n\n## Installation\n\nRun npm install.\n\n## Usage\n\n"
                "```js\nconst app = require('./app');\n```\n\nSee [docs](https://example.com)."
            ),
            "metadata": {"title": "README"},
        },
        {
            "id": "doc-2",
            "content": (
                "# API Reference\n\n## GET /users\n\nReturns all users.\n\n"
                "## POST /users\n\nCreates a user."
            ),
            "metadata": {"title": "API"},
        },
    ])

    result = await run_initial_evaluation(context, org_id="test-org")

    assert 0.0 <= result.score <= 1.0
    assert "coverage" in result.dimensions
    assert "completeness" in result.dimensions
    assert "structure" in result.dimensions
    assert "length" in result.dimensions


@pytest.mark.asyncio
async def test_initial_evaluation_empty_corpus():
    """Should return 0 score for empty corpus."""
    from draftly.workflows.onboarding.stages import run_initial_evaluation

    context = MagicMock()
    context.model = None
    context.memory.recall = AsyncMock(return_value=[])

    result = await run_initial_evaluation(context, org_id="test-org")

    assert result.score == 0.0
    assert all(v == 0.0 for v in result.dimensions.values())


@pytest.mark.asyncio
async def test_initial_evaluation_weights_dimensions():
    """Score should be weighted: coverage 30%, completeness 30%, structure 20%, length 20%."""
    from draftly.workflows.onboarding.stages import run_initial_evaluation

    context = MagicMock()
    context.model = None  # Disable LLM to test heuristic-only path
    context.memory.recall = AsyncMock(return_value=[
        {
            "id": "doc-1",
            "content": "# Title\n\n## Section\n\nSome text with `code`.\n\n```python\nprint('hello')\n```\n\n[Link](https://example.com)",
            "metadata": {"title": "Test"},
        },
    ])

    result = await run_initial_evaluation(context, org_id="test-org")

    expected = (
        0.3 * result.dimensions["coverage"]
        + 0.3 * result.dimensions["completeness"]
        + 0.2 * result.dimensions["structure"]
        + 0.2 * result.dimensions["length"]
    )
    assert abs(result.score - expected) < 0.01


@pytest.mark.asyncio
async def test_initial_evaluation_blends_llm_scores():
    """Should blend heuristic and LLM scores when model is available."""
    from draftly.workflows.onboarding.stages import run_initial_evaluation

    context = MagicMock()
    context.memory.recall = AsyncMock(return_value=[
        {
            "id": "doc-1",
            "content": "# Getting Started\n\n## Installation\n\nRun npm install.",
            "metadata": {"title": "README"},
        },
    ])

    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(return_value=(
            '{"coverage": 0.9, "completeness": 0.8, "structure": 0.7, "length": 0.6}'
        ))

        result = await run_initial_evaluation(context, org_id="test-org")

    assert 0.0 <= result.score <= 1.0
    assert all(0.0 <= v <= 1.0 for v in result.dimensions.values())


@pytest.mark.asyncio
async def test_initial_evaluation_llm_failure_falls_back_to_heuristics():
    """Should fall back to heuristic-only scores when LLM fails."""
    from draftly.workflows.onboarding.stages import run_initial_evaluation

    context = MagicMock()
    context.memory.recall = AsyncMock(return_value=[
        {
            "id": "doc-1",
            "content": "# Title\n\n## Section\n\nSome content here.",
            "metadata": {"title": "Test"},
        },
    ])

    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(side_effect=RuntimeError("model down"))

        result = await run_initial_evaluation(context, org_id="test-org")

    # Should still return valid scores from heuristics
    assert 0.0 <= result.score <= 1.0
    assert "coverage" in result.dimensions
    assert "completeness" in result.dimensions


@pytest.mark.asyncio
async def test_health_report_aggregates_scores():
    """Should compute weighted health from evaluation + baseline stats."""
    from draftly.workflows.onboarding.stages import (
        EvaluationResult,
        run_health_report,
    )

    eval_result = EvaluationResult(
        score=0.7,
        dimensions={"coverage": 0.8, "completeness": 0.6, "structure": 0.7, "length": 0.7},
    )

    result = run_health_report(
        eval_result=eval_result,
        document_count=25,
        section_count=75,
        last_committed_dates=[],
    )

    assert 0.0 <= result.score <= 1.0
    assert "coverage" in result.dimensions
    assert "freshness" in result.dimensions
    assert result.dimensions["freshness"] == 0.5  # no dates = neutral


def test_health_report_sync_not_async():
    """Health report is pure math — should be sync, not async."""
    import inspect

    from draftly.workflows.onboarding.stages import run_health_report
    assert not inspect.iscoroutinefunction(run_health_report)


@pytest.mark.asyncio
async def test_recommendations_generates_suggestions():
    """Should generate recommendations from health + evaluation results."""
    from draftly.workflows.onboarding.stages import (
        EvaluationResult,
        HealthResult,
        run_recommendations,
    )

    context = MagicMock()

    eval_result = EvaluationResult(score=0.5, dimensions={
        "coverage": 0.3, "completeness": 0.5, "structure": 0.6, "length": 0.6,
    })
    health_result = HealthResult(score=0.52, dimensions={
        "coverage": 0.3, "structure": 0.6, "freshness": 0.8, "completeness": 0.5,
    })

    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(return_value=(
            '[{"priority": "high", "title": "Add API reference", '
            '"detail": "Your docs lack API reference sections.", "category": "coverage"}]'
        ))

        recs = await run_recommendations(
            context, eval_result=eval_result, health_result=health_result,
            document_count=10, chunk_count=40,
        )

    assert len(recs) >= 1
    assert recs[0].priority == "high"
    assert recs[0].title == "Add API reference"


@pytest.mark.asyncio
async def test_recommendations_handles_llm_failure():
    """Should return empty list if LLM fails, not raise."""
    from draftly.workflows.onboarding.stages import (
        EvaluationResult,
        HealthResult,
        run_recommendations,
    )

    context = MagicMock()

    eval_result = EvaluationResult(score=0.7, dimensions={
        "coverage": 0.7, "completeness": 0.7, "structure": 0.7, "length": 0.7,
    })
    health_result = HealthResult(score=0.7, dimensions={
        "coverage": 0.7, "structure": 0.7, "freshness": 0.8, "completeness": 0.7,
    })

    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(side_effect=RuntimeError("model down"))

        recs = await run_recommendations(
            context, eval_result=eval_result, health_result=health_result,
            document_count=30, chunk_count=150,
        )

    assert recs == []


def test_health_report_fresh_docs():
    """All recent dates → freshness near 1.0."""
    from draftly.workflows.onboarding.stages import EvaluationResult, run_health_report

    eval_result = EvaluationResult(score=0.7, dimensions={
        "coverage": 0.8, "completeness": 0.6, "structure": 0.7, "length": 0.7,
    })
    recent = datetime.now(UTC) - timedelta(days=5)
    result = run_health_report(
        eval_result=eval_result,
        document_count=10,
        section_count=30,
        last_committed_dates=[recent, recent, recent],
    )
    assert result.dimensions["freshness"] > 0.9


def test_health_report_stale_docs():
    """All old dates → freshness near 0.0."""
    from draftly.workflows.onboarding.stages import EvaluationResult, run_health_report

    eval_result = EvaluationResult(score=0.7, dimensions={
        "coverage": 0.8, "completeness": 0.6, "structure": 0.7, "length": 0.7,
    })
    old = datetime.now(UTC) - timedelta(days=120)
    result = run_health_report(
        eval_result=eval_result,
        document_count=10,
        section_count=30,
        last_committed_dates=[old, old],
    )
    assert result.dimensions["freshness"] < 0.1


def test_health_report_mixed_dates():
    """Fresh + stale → average in between."""
    from draftly.workflows.onboarding.stages import EvaluationResult, run_health_report

    eval_result = EvaluationResult(score=0.7, dimensions={
        "coverage": 0.8, "completeness": 0.6, "structure": 0.7, "length": 0.7,
    })
    fresh = datetime.now(UTC) - timedelta(days=10)
    stale = datetime.now(UTC) - timedelta(days=80)
    result = run_health_report(
        eval_result=eval_result,
        document_count=10,
        section_count=30,
        last_committed_dates=[fresh, stale],
    )
    assert 0.2 < result.dimensions["freshness"] < 0.9


def test_health_report_no_dates():
    """All None → freshness 0.5 (neutral)."""
    from draftly.workflows.onboarding.stages import EvaluationResult, run_health_report

    eval_result = EvaluationResult(score=0.7, dimensions={
        "coverage": 0.8, "completeness": 0.6, "structure": 0.7, "length": 0.7,
    })
    result = run_health_report(
        eval_result=eval_result,
        document_count=10,
        section_count=30,
        last_committed_dates=[None, None, None],
    )
    assert result.dimensions["freshness"] == 0.5
