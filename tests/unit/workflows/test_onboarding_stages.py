"""Unit tests for onboarding initialization stage functions."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
import structlog
from pydantic import ValidationError

import draftly.workflows.onboarding.stages as stages
from draftly.integrations.strands.models import RoleAwareModelResolver
from draftly.models.schemas import RoutingDecision
from draftly.workflows.onboarding.stages import (
    EvaluationScores,
    ExtractionOutput,
    KnowledgeExtractionResult,
    Procedure,
    Recommendation,
    RecommendationList,
    Relationship,
    run_initial_evaluation,
    run_knowledge_construction,
)


def fake_agent_result(model_instance=None, usage=None):
    # `is not None` (not `or`): an explicit empty dict must stay empty so the
    # zero-usage telemetry path can be tested.
    return SimpleNamespace(
        structured_output=model_instance,
        metrics=SimpleNamespace(
            accumulated_usage=(
                usage if usage is not None else {"inputTokens": 100, "outputTokens": 50}
            )
        ),
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
        mock_agent.invoke_async = AsyncMock(return_value=fake_agent_result(
            ExtractionOutput(
                facts=["Install via npm"],
                relationships=[
                    Relationship(source="npm", target="install", type="DOCUMENTED_BY")
                ],
                procedures=[Procedure(title="Run", steps=["Run npm start"])],
            )
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
        mock_agent.invoke_async = AsyncMock(return_value=fake_agent_result(
            ExtractionOutput.model_validate({
                "facts": ["Contains utils"],
                "relationships": [{"source": "npm", "target": "utils", "type": "contains"}],
                "procedures": [],
            })
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
        mock_agent.invoke_async = AsyncMock(return_value=fake_agent_result(
            ExtractionOutput.model_validate({
                "facts": ["Auth uses JWT"],
                "relationships": [{"source": "auth", "target": "jwt", "type": "IMPLEMENTS"}],
                "procedures": [],
            })
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
            fake_agent_result(ExtractionOutput(facts=["APIs exist"])),
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
async def test_knowledge_construction_publishes_granular_stage_progress():
    """Stage 2 should emit multiple increasing `stage_progress` events so the
    UI progress bar animates during slow LLM extraction (not a single jump)."""
    context = MagicMock()
    chunks = [
        {"id": f"chunk-{i}", "content": f"Documentation chunk number {i}.", "metadata": {}}
        for i in range(55)  # > CHUNK_BATCH_SIZE (50) -> two batches
    ]
    context.memory.recall = AsyncMock(return_value=chunks)
    context.memory.remember = AsyncMock(return_value={"id": "k-1"})
    context.docgraph.link = AsyncMock(return_value={"id": "edge-1"})
    context.candidates.enqueue = AsyncMock(return_value={"id": "c-1"})
    publish = AsyncMock()

    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(
            return_value=fake_agent_result(ExtractionOutput(facts=["f"]))
        )

        await run_knowledge_construction(
            context, org_id="test-org", publish=publish,
        )

    progress_calls = [
        c.args[1]
        for c in publish.call_args_list
        if c.args[0] == "stage_progress"
    ]
    assert progress_calls, "expected at least one stage_progress emission"
    for payload in progress_calls:
        assert payload["stage"] == "knowledge_construction"
        assert isinstance(payload["progress"], int)
        assert 0 <= payload["progress"] <= 95
    values = [p["progress"] for p in progress_calls]
    assert values == sorted(values), "stage_progress values should be monotonic"
    assert len(values) >= 2, "multiple batches should emit multiple progress values"


@pytest.mark.asyncio
async def test_initial_evaluation_publishes_granular_stage_progress():
    """Stage 3 should emit granular `stage_progress` events alongside
    `tool_progress` so the UI animates during LLM evaluation."""
    from draftly.workflows.onboarding.stages import run_initial_evaluation

    context = MagicMock()
    context.memory.recall = AsyncMock(return_value=[
        {
            "id": f"doc-{i}",
            "content": f"# Docs\n\ndocumentation body {i}.",
            "metadata": {"title": f"Doc {i}"},
        }
        for i in range(25)
    ])
    publish = AsyncMock()

    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(return_value=fake_agent_result(
            EvaluationScores(coverage=0.8, completeness=0.7, structure=0.6, length=0.5)
        ))

        await run_initial_evaluation(context, org_id="test-org", publish=publish)

    progress_calls = [
        c.args[1]
        for c in publish.call_args_list
        if c.args[0] == "stage_progress"
    ]
    assert progress_calls, "expected at least one stage_progress emission"
    for payload in progress_calls:
        assert payload["stage"] == "initial_evaluation"
        assert isinstance(payload["progress"], int)
    values = [p["progress"] for p in progress_calls]
    assert values == sorted(values), "stage_progress values should be monotonic"
    # 25 docs, emitting every 10 -> at least 2 granular updates
    assert len(values) >= 2


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
        mock_agent.invoke_async = AsyncMock(return_value=fake_agent_result(
            EvaluationScores(coverage=0.9, completeness=0.8, structure=0.7, length=0.6)
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
        mock_agent.invoke_async = AsyncMock(return_value=fake_agent_result(
            RecommendationList(items=[
                Recommendation(priority="high", title="Add API reference",
                               detail="Your docs lack API reference sections.",
                               category="coverage")
            ])
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


@pytest.mark.asyncio
async def test_knowledge_construction_batches_fact_storage_per_batch():
    """Task 6: facts are stored with one store_batch call per chunk batch.

    Replaces the per-fact remember() loop (1 embed + 1 txn per fact) with a
    single batched store at batch end.
    """
    context = MagicMock()
    chunks = [
        {"id": f"chunk-{i}", "content": f"Fact content {i}.", "metadata": {}}
        for i in range(3)
    ]
    context.memory.recall = AsyncMock(return_value=chunks)
    context.memory.store_batch = AsyncMock(
        return_value=[{"id": f"k-{i}"} for i in range(6)]
    )
    context.memory.remember = AsyncMock(return_value={"id": "k-1"})
    context.docgraph.link = AsyncMock(return_value={"id": "edge-1"})
    context.candidates.enqueue = AsyncMock(return_value={"id": "c-1"})
    publish = AsyncMock()

    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(return_value=fake_agent_result(
            ExtractionOutput(facts=["Fact A", "Fact B"])
        ))

        result = await run_knowledge_construction(
            context, org_id="test-org", publish=publish,
        )

    context.memory.store_batch.assert_awaited_once()
    stored_items = context.memory.store_batch.await_args.args[0]
    assert len(stored_items) == 6  # 3 chunks x 2 facts
    context.memory.remember.assert_not_called()
    assert result.knowledge_count == 6


# ============================================================
# Task 7: parallel LLM stages + enforced chunk timeout
# ============================================================


@pytest.mark.asyncio
async def test_knowledge_construction_enforces_chunk_timeout(monkeypatch):
    """A hung LLM call is cut off at CHUNK_TIMEOUT_SECONDS; the batch continues.

    The timed-out chunk lands in failed_chunks; other chunks still extract.
    """
    monkeypatch.setattr(stages, "CHUNK_TIMEOUT_SECONDS", 0.1)

    async def slow_llm(model, prompt, agent=None, *, output_model=None, telemetry=None):
        await asyncio.sleep(1.0)
        return ExtractionOutput(facts=["slow"])

    monkeypatch.setattr(stages, "_llm_generate", slow_llm)

    context = MagicMock()
    context.memory.recall = AsyncMock(return_value=[
        {"id": "chunk-hung", "content": "This call hangs.", "metadata": {}},
    ])
    context.memory.store_batch = AsyncMock(return_value=[])
    context.docgraph.link = AsyncMock(return_value={"id": "edge-1"})
    context.candidates.enqueue = AsyncMock(return_value={"id": "c-1"})
    publish = AsyncMock()

    with patch("draftly.workflows.onboarding.stages.Agent"):
        result = await run_knowledge_construction(
            context, org_id="test-org", publish=publish,
        )

    assert "chunk-hung" in result.failed_chunks
    assert result.knowledge_count == 0


@pytest.mark.asyncio
async def test_knowledge_construction_caps_llm_concurrency(monkeypatch):
    """LLM calls run concurrently but never exceed LLM_MAX_CONCURRENCY."""
    monkeypatch.setattr(stages, "CHUNK_TIMEOUT_SECONDS", 5)

    state = {"inflight": 0, "max_inflight": 0}
    lock = asyncio.Lock()

    async def fake_llm(model, prompt, agent=None, *, output_model=None, telemetry=None):
        async with lock:
            state["inflight"] += 1
            state["max_inflight"] = max(state["max_inflight"], state["inflight"])
        await asyncio.sleep(0.02)
        async with lock:
            state["inflight"] -= 1
        return ExtractionOutput(facts=["F"])

    monkeypatch.setattr(stages, "_llm_generate", fake_llm)

    context = MagicMock()
    context.memory.recall = AsyncMock(return_value=[
        {"id": f"chunk-{i}", "content": f"Content {i}.", "metadata": {}}
        for i in range(20)
    ])
    context.memory.store_batch = AsyncMock(
        return_value=[{"id": f"k-{i}"} for i in range(20)]
    )
    context.docgraph.link = AsyncMock(return_value={"id": "edge-1"})
    context.candidates.enqueue = AsyncMock(return_value={"id": "c-1"})
    publish = AsyncMock()

    with patch("draftly.workflows.onboarding.stages.Agent"):
        result = await run_knowledge_construction(
            context, org_id="test-org", publish=publish,
        )

    assert result.knowledge_count == 20
    # Overlap proves the calls are no longer serial...
    assert state["max_inflight"] > 1
    # ...but stay bounded by the configured cap.
    assert state["max_inflight"] <= stages.LLM_MAX_CONCURRENCY


# ============================================================
# Task 8: sampled LLM evaluation pass
# ============================================================


def _eval_docs(n: int, api_count: int = 0) -> list[dict]:
    """n docs; the first `api_count` mention 'api' (heuristic coverage marker)."""
    docs = []
    for i in range(n):
        content = (
            f"api usage guide number {i}." if i < api_count
            else f"body text number {i}."
        )
        docs.append({
            "id": f"doc-{i}",
            "content": f"# Doc {i}\n\n{content}",
            "metadata": {"title": f"Doc {i}"},
        })
    return docs


def test_sample_docs_is_deterministic_and_bounded():
    """_sample_docs returns exactly k docs, deterministically (no RNG)."""
    docs = _eval_docs(500)
    sample1 = stages._sample_docs(docs)
    sample2 = stages._sample_docs(docs)
    assert len(sample1) == stages.EVAL_LLM_SAMPLE_SIZE
    assert [d["id"] for d in sample1] == [d["id"] for d in sample2]
    # Small corpora pass through unchanged.
    small = _eval_docs(10)
    assert [d["id"] for d in stages._sample_docs(small)] == [
        d["id"] for d in small
    ]


@pytest.mark.asyncio
async def test_evaluation_llm_calls_are_sampled(monkeypatch):
    """500 docs produce exactly EVAL_LLM_SAMPLE_SIZE LLM calls, not 500."""
    context = MagicMock()
    context.model = MagicMock()
    context.memory.recall = AsyncMock(return_value=_eval_docs(500))
    publish = AsyncMock()

    calls = {"n": 0}

    async def fake_llm(model, prompt, agent=None, *, output_model=None, telemetry=None):
        calls["n"] += 1
        return EvaluationScores(coverage=0.8, completeness=0.8, structure=0.8, length=0.8)

    monkeypatch.setattr(stages, "_llm_generate", fake_llm)

    with patch("draftly.workflows.onboarding.stages.Agent"):
        await run_initial_evaluation(
            context, org_id="test-org", publish=publish,
        )

    assert calls["n"] == stages.EVAL_LLM_SAMPLE_SIZE


@pytest.mark.asyncio
async def test_evaluation_small_corpus_calls_llm_per_doc(monkeypatch):
    """Below the sample size every doc still gets an LLM call."""
    context = MagicMock()
    context.model = MagicMock()
    context.memory.recall = AsyncMock(return_value=_eval_docs(10))
    publish = AsyncMock()

    calls = {"n": 0}

    async def fake_llm(model, prompt, agent=None, *, output_model=None, telemetry=None):
        calls["n"] += 1
        return EvaluationScores(coverage=0.8, completeness=0.8, structure=0.8, length=0.8)

    monkeypatch.setattr(stages, "_llm_generate", fake_llm)

    with patch("draftly.workflows.onboarding.stages.Agent"):
        await run_initial_evaluation(context, org_id="test-org", publish=publish)

    assert calls["n"] == 10


@pytest.mark.asyncio
async def test_evaluation_heuristics_still_scan_full_corpus(monkeypatch):
    """Blend uses heuristic scores over ALL docs, LLM scores over the sample.

    100/500 docs mention 'api' → heuristic coverage = 0.2; every sampled doc
    scores 0.8 from the LLM → blended coverage = 0.4*0.2 + 0.6*0.8 = 0.56.
    """
    context = MagicMock()
    context.model = MagicMock()
    context.memory.recall = AsyncMock(return_value=_eval_docs(500, api_count=100))
    publish = AsyncMock()

    async def fake_llm(model, prompt, agent=None, *, output_model=None, telemetry=None):
        return EvaluationScores(coverage=0.8, completeness=0.8, structure=0.8, length=0.8)

    monkeypatch.setattr(stages, "_llm_generate", fake_llm)

    with patch("draftly.workflows.onboarding.stages.Agent"):
        result = await run_initial_evaluation(
            context, org_id="test-org", publish=publish,
        )

    assert abs(result.dimensions["coverage"] - 0.56) < 1e-9


def test_relationship_degrades_unknown_type_to_derived_from():
    rel = Relationship(source="npm", target="utils", type="contains")
    assert rel.type == "DERIVED_FROM"


def test_relationship_preserves_valid_type():
    rel = Relationship(source="a", target="b", type="IMPLEMENTS")
    assert rel.type == "IMPLEMENTS"


def test_extraction_parses_from_raw_provider_json():
    out = ExtractionOutput.model_validate({
        "facts": ["Install via npm"],
        "relationships": [{"source": "npm", "target": "install", "type": "DOCUMENTED_BY"}],
        "procedures": [{"title": "Run", "steps": ["npm start"]}],
    })
    assert out.facts == ["Install via npm"]
    assert out.relationships[0].type == "DOCUMENTED_BY"
    assert out.procedures[0].steps == ["npm start"]


def test_evaluation_scores_reject_out_of_range():
    with pytest.raises(ValidationError):
        EvaluationScores(coverage=1.5, completeness=0.5, structure=0.5, length=0.5)


def test_recommendation_list_validates_priority_literal():
    parsed = RecommendationList.model_validate({
        "items": [{"priority": "high", "title": "t", "detail": "d", "category": "c"}]
    })
    assert parsed.items[0].priority == "high"


@pytest.mark.asyncio
async def test_llm_generate_returns_validated_structured_output():
    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(
            return_value=fake_agent_result(ExtractionOutput(facts=["f"]))
        )
        out = await stages._llm_generate(
            MagicMock(), "prompt", output_model=ExtractionOutput
        )
    assert isinstance(out, ExtractionOutput)
    assert out.facts == ["f"]
    mock_agent_cls.assert_called_once_with(
        model=ANY, structured_output_model=ExtractionOutput,
    )
    mock_agent.invoke_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_generate_passes_token_budget_limits():
    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(
            return_value=fake_agent_result(
                EvaluationScores(coverage=0.5, completeness=0.5, structure=0.5, length=0.5)
            )
        )
        await stages._llm_generate(MagicMock(), "p", output_model=EvaluationScores)
    kwargs = mock_agent.invoke_async.call_args.kwargs
    assert kwargs["limits"] == {"total_tokens": stages.LLM_TOTAL_TOKENS_CAP}


@pytest.mark.asyncio
async def test_llm_generate_token_limits_none_when_cap_disabled(monkeypatch):
    monkeypatch.setattr(stages, "LLM_TOTAL_TOKENS_CAP", 0)
    monkeypatch.setattr(stages, "LLM_LIMITS", None)
    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(return_value=fake_agent_result())
        await stages._llm_generate(MagicMock(), "p", output_model=EvaluationScores)
    assert mock_agent.invoke_async.call_args.kwargs["limits"] is None


@pytest.mark.asyncio
async def test_llm_generate_records_token_usage(monkeypatch):
    from draftly.observability.metrics import Metrics

    fake_metrics = Metrics()
    monkeypatch.setattr(stages, "_metrics", fake_metrics)
    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(
            return_value=fake_agent_result(
                EvaluationScores(coverage=0.5, completeness=0.5, structure=0.5, length=0.5),
                usage={"inputTokens": 200, "outputTokens": 80},
            )
        )
        await stages._llm_generate(MagicMock(), "p", output_model=EvaluationScores)
    snapshot = fake_metrics.snapshot()
    assert snapshot["counters"]["draftly_tokens_input_total"] == 200.0
    assert snapshot["counters"]["draftly_tokens_output_total"] == 80.0


@pytest.mark.asyncio
async def test_llm_generate_skips_zero_token_usage(monkeypatch):
    from draftly.observability.metrics import Metrics

    fake_metrics = Metrics()
    monkeypatch.setattr(stages, "_metrics", fake_metrics)
    with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.invoke_async = AsyncMock(
            return_value=fake_agent_result(usage={}),
        )
        await stages._llm_generate(MagicMock(), "p", output_model=EvaluationScores)
    assert fake_metrics.snapshot()["counters"] == {}


def test_extraction_prompt_has_no_json_boilerplate():
    assert "markdown fences" not in stages.EXTRACTION_PROMPT
    assert "Return ONLY valid JSON" not in stages.EXTRACTION_PROMPT


def test_evaluation_prompt_has_no_json_boilerplate():
    assert "markdown fences" not in stages.EVALUATION_LLM_PROMPT
    assert "Return ONLY valid JSON" not in stages.EVALUATION_LLM_PROMPT


def test_describe_routing_extracts_provider_and_model():
    class OpenAIModel:
        config = {"model_id": "gpt-route-test"}

    assert stages._describe_routing(OpenAIModel()) == {
        "provider": "openai",
        "model": "gpt-route-test",
    }


def test_describe_routing_handles_offline_and_bare_model():
    assert stages._describe_routing(None) == {
        "provider": "none",
        "model": "deterministic/offline",
    }
    info = stages._describe_routing(object())  # no .config attribute
    assert info == {"provider": "object", "model": "object"}


@pytest.mark.asyncio
async def test_llm_generate_logs_routing(monkeypatch):
    from structlog.testing import capture_logs

    class OpenAIModel:
        config = {"model_id": "gpt-route-test"}

    # The module logger is already cached under cache_logger_on_first_use
    # (observability/logging.py), so patch stages.logger with a fresh proxy
    # that resolves against capture_logs' temporary processors.
    with capture_logs() as logs:
        monkeypatch.setattr(
            stages, "logger", structlog.get_logger("test.llm_generate_routing"),
        )
        with patch("draftly.workflows.onboarding.stages.Agent") as mock_agent_cls:
            mock_agent = mock_agent_cls.return_value
            mock_agent.invoke_async = AsyncMock(
                return_value=fake_agent_result(ExtractionOutput(facts=["f"]))
            )
            await stages._llm_generate(OpenAIModel(), "prompt", output_model=ExtractionOutput)

    routing = [line for line in logs if line.get("event") == "llm_generate"]
    assert len(routing) == 1
    assert routing[0]["provider"] == "openai"
    assert routing[0]["model"] == "gpt-route-test"
    assert routing[0]["output_model"] == "ExtractionOutput"
    assert routing[0]["prompt_chars"] == len("prompt")


# ============================================================
# Task 4: stage telemetry + routing wiring tests
# ============================================================


_ROUTING_DECISION = RoutingDecision(
    selected_model="model-a-demo", provider="openrouter", score=0.9,
    candidates_considered=1, profile="documentation_generation",
    task_type="documentation_generation",
)


class _FakeRoleResolver(RoleAwareModelResolver):
    """Stub resolver returning a fixed (model, decision); passes
    stages._resolve_stage_model's isinstance guard."""

    def __init__(self, model: Any, decision: Any | None) -> None:
        self._model = model
        self._decision = decision
        super().__init__(object())

    def for_role_with_decision(self, role, **kwargs):
        return self._model, self._decision


@pytest.mark.asyncio
async def test_llm_generate_records_success_telemetry():
    calls = []

    async def telemetry(success, latency_ms):
        calls.append((success, latency_ms))

    class FakeAgent:
        async def invoke_async(self, prompt, **kwargs):
            return fake_agent_result(ExtractionOutput(facts=["f"]))

    with patch("draftly.workflows.onboarding.stages.Agent", return_value=FakeAgent()):
        out = await stages._llm_generate(
            MagicMock(), "p", output_model=ExtractionOutput, telemetry=telemetry,
        )

    assert out.facts == ["f"]
    assert len(calls) == 1
    assert calls[0][0] is True
    assert calls[0][1] >= 0


@pytest.mark.asyncio
async def test_llm_generate_records_failure_telemetry():
    calls = []

    async def telemetry(success, latency_ms):
        calls.append((success, latency_ms))

    class BoomAgent:
        async def invoke_async(self, prompt, **kwargs):
            raise RuntimeError("provider down")

    with patch("draftly.workflows.onboarding.stages.Agent", return_value=BoomAgent()):
        with pytest.raises(RuntimeError, match="provider down"):
            await stages._llm_generate(
                MagicMock(), "p", output_model=ExtractionOutput, telemetry=telemetry,
            )

    assert len(calls) == 1
    assert calls[0][0] is False          # failure still recorded
    assert calls[0][1] >= 0


@pytest.mark.asyncio
async def test_knowledge_construction_records_routing_outcomes(monkeypatch):
    """Routed stage: one live-EMA record per chunk, one flush at stage end."""
    async def fake_llm(model, prompt, agent=None, *, output_model=None, telemetry=None):
        if telemetry is not None:
            await telemetry(True, 42.0)
        return ExtractionOutput(facts=["F"])

    monkeypatch.setattr(stages, "_llm_generate", fake_llm)

    context = MagicMock()
    context.model = _FakeRoleResolver(MagicMock(), _ROUTING_DECISION)
    context.memory.recall = AsyncMock(return_value=[
        {"id": f"chunk-{i}", "content": f"Content {i}.", "metadata": {}}
        for i in range(stages.CHUNK_BATCH_SIZE)
    ])
    context.memory.store_batch = AsyncMock(
        return_value=[{"id": f"k-{i}"} for i in range(stages.CHUNK_BATCH_SIZE)]
    )
    context.docgraph.link = AsyncMock(return_value={"id": "edge-1"})
    context.candidates.enqueue = AsyncMock(return_value={"id": "c-1"})
    context.repositories.performance = AsyncMock()
    publish = AsyncMock()

    with patch("draftly.workflows.onboarding.stages.Agent"):
        result = await run_knowledge_construction(
            context, org_id="test-org", publish=publish,
        )

    perf = context.repositories.performance
    assert result.knowledge_count == stages.CHUNK_BATCH_SIZE
    assert perf.record_outcome.call_count == stages.CHUNK_BATCH_SIZE
    assert perf.flush_entry.call_count == 1
    call = perf.record_outcome.call_args
    assert call.kwargs["task_type"] == "documentation_generation"
    assert call.kwargs["model_name"] == "model-a-demo"
    assert call.kwargs["success"] is True
    assert call.kwargs["flush"] is False


@pytest.mark.asyncio
async def test_knowledge_construction_offline_never_records(monkeypatch):
    """Offline (context.model is None) → deterministic path, no telemetry."""
    async def fake_llm(model, prompt, agent=None, *, output_model=None, telemetry=None):
        if telemetry is not None:
            await telemetry(False, 0.0)   # recorder is disabled → no-op
        return ExtractionOutput(facts=["F"])

    monkeypatch.setattr(stages, "_llm_generate", fake_llm)

    context = MagicMock()
    context.model = None
    context.memory.recall = AsyncMock(return_value=[
        {"id": "chunk-1", "content": "Content 1.", "metadata": {}},
    ])
    context.memory.store_batch = AsyncMock(return_value=[{"id": "k-1"}])
    context.docgraph.link = AsyncMock(return_value={"id": "edge-1"})
    context.candidates.enqueue = AsyncMock(return_value={"id": "c-1"})
    context.repositories.performance = AsyncMock()
    publish = AsyncMock()

    with patch("draftly.workflows.onboarding.stages.Agent"):
        result = await run_knowledge_construction(
            context, org_id="test-org", publish=publish,
        )

    assert result.knowledge_count == 1
    assert context.repositories.performance.record_outcome.call_count == 0
    assert context.repositories.performance.flush_entry.call_count == 0
