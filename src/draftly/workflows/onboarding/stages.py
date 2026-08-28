"""Onboarding initialization stage functions.

Stages 2-5 of the onboarding workflow: knowledge extraction, evaluation,
health scoring, and recommendation generation.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, field_validator
from strands import Agent

from draftly.integrations.strands.models import RoleAwareModelResolver
from draftly.observability.metrics import Metrics
from draftly.observability.metrics import metrics as _metrics_default

logger = structlog.get_logger(__name__)

_metrics: Metrics = _metrics_default

EXTRACTION_PROMPT = """Extract structured knowledge from this documentation chunk.

Chunk content:
{content}"""


VALID_RELATION_TYPES = {"IMPLEMENTS", "DOCUMENTED_BY", "AFFECTS", "DERIVED_FROM"}


class Relationship(BaseModel):
    source: str = ""
    target: str = ""
    type: str = "DERIVED_FROM"

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, value: Any) -> str:
        normalized = str(value).upper()
        if normalized in VALID_RELATION_TYPES:
            return normalized
        return "DERIVED_FROM"


class Procedure(BaseModel):
    title: str = ""
    steps: list[str] = Field(default_factory=list)


class ExtractionOutput(BaseModel):
    facts: list[str] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    procedures: list[Procedure] = Field(default_factory=list)


class EvaluationScores(BaseModel):
    coverage: float = Field(ge=0.0, le=1.0)
    completeness: float = Field(ge=0.0, le=1.0)
    structure: float = Field(ge=0.0, le=1.0)
    length: float = Field(ge=0.0, le=1.0)


class Recommendation(BaseModel):
    priority: Literal["high", "medium", "low"] = "medium"
    title: str = ""
    detail: str = ""
    category: str = ""


class RecommendationList(BaseModel):
    items: list[Recommendation] = Field(default_factory=list)


@dataclass
class KnowledgeExtractionResult:
    knowledge_count: int = 0
    relationship_count: int = 0
    candidate_count: int = 0
    failed_chunks: list[str] = field(default_factory=list)


@dataclass
class EvaluationResult:
    score: float = 0.0
    dimensions: dict[str, float] = field(default_factory=dict)


@dataclass
class HealthResult:
    score: float = 0.0
    dimensions: dict[str, float] = field(default_factory=dict)


CHUNK_BATCH_SIZE = 50
CHUNK_TIMEOUT_SECONDS = 10

# Bounded LLM concurrency (Task 7). Configurable so deployments can trade
# stage latency against provider rate limits.
LLM_MAX_CONCURRENCY = max(1, int(os.environ.get("LLM_MAX_CONCURRENCY", "8")))

# Total-token budget per LLM call. Passed as a Strands `limits` bound; set the
# env var to "0" for no limit.
LLM_TOTAL_TOKENS_CAP = int(os.environ.get("LLM_TOTAL_TOKENS_CAP", "12000"))
LLM_LIMITS = {"total_tokens": LLM_TOTAL_TOKENS_CAP} if LLM_TOTAL_TOKENS_CAP > 0 else None

# Task 8: cap on LLM-evaluated docs in the initial evaluation pass. Heuristic
# dimensions still scan the full corpus; only the LLM blend is sampled.
EVAL_LLM_SAMPLE_SIZE = 25


def _sample_docs(docs: list[dict], k: int = EVAL_LLM_SAMPLE_SIZE) -> list[dict]:
    """Deterministic spread of up to ``k`` docs (no RNG) for the LLM eval pass."""
    if len(docs) <= k:
        return list(docs)
    step = len(docs) / k
    return [docs[int(i * step)] for i in range(k)]


def _record_usage(result: Any) -> None:
    """Increment token counters from an AgentResult's accumulated usage.

    Defensive by design: offline doubles carry no ``metrics``; zero totals are
    not recorded (parity with runner.py).
    """
    usage = getattr(result.metrics, "accumulated_usage", None)
    if not isinstance(usage, dict):
        return
    input_tokens = usage.get("inputTokens")
    output_tokens = usage.get("outputTokens")
    if input_tokens:
        _metrics.increment("draftly_tokens_input_total", float(input_tokens))
    if output_tokens:
        _metrics.increment("draftly_tokens_output_total", float(output_tokens))


def _describe_routing(model: Any) -> dict[str, str]:
    """Best-effort (provider, model) identity for terminal routing logs.

    WorkflowContext.model is a concrete Strands Model resolved at startup
    (app/lifecycle.py::_resolve_runtime_model), so the ModelRouter's own
    attempting/resolved lines never fire during stages. This supplies the
    equivalent visibility from the model we actually invoke.
    """
    if model is None:
        return {"provider": "none", "model": "deterministic/offline"}
    config = getattr(model, "config", None) or {}
    model_id = str(
        config.get("model_id")
        or getattr(model, "model", "")
        or type(model).__name__
    )
    provider = str(
        getattr(model, "provider", "")
        or type(model).__name__.removesuffix("Model").lower()
    )
    return {"provider": provider, "model": model_id}


def _resolve_stage_model(
    context: Any, role: str, *, prompt_text: str | None = None,
) -> tuple[Any, Any | None]:
    """Per-stage routed model + its RoutingDecision; offline → (None, None).

    Non-resolver ``context.model`` (legacy concrete models, test doubles)
    passes through unchanged as ``(model, None)`` — no routing, no telemetry.
    """
    model_or_resolver = getattr(context, "model", None)
    if model_or_resolver is None:
        return None, None
    if isinstance(model_or_resolver, RoleAwareModelResolver):
        try:
            return model_or_resolver.for_role_with_decision(
                role, prompt_text=prompt_text
            )
        except Exception as exc:
            logger.warning("stage_routing_failed role=%s err=%s", role, exc)
            return None, None
    return model_or_resolver, None


class _OutcomeRecorder:
    """Accumulates per-call routing outcomes; one flush_entry per stage.

    No-ops entirely when there is no routing decision or no performance
    repository on the context (offline / legacy). ``record`` feeds only the
    live EMA cache so the very next route() call sees each outcome; ``flush``
    persists the aggregated row once per stage.
    """

    def __init__(self, context: Any, decision: Any | None) -> None:
        self._task_type: str | None = None
        self._model_name: str | None = None
        self._repo: Any = None
        if decision is not None:
            repo = getattr(getattr(context, "repositories", None), "performance", None)
            if repo is not None:
                self._repo = repo
                self._task_type = getattr(
                    decision.task_type, "value", decision.task_type
                )
                self._model_name = decision.selected_model

    @property
    def enabled(self) -> bool:
        return self._repo is not None and self._task_type is not None

    async def record(self, success: bool, latency_ms: float) -> None:
        """Update the live EMA cache (never raises)."""
        if not self.enabled:
            return
        try:
            await self._repo.record_outcome(
                task_type=self._task_type, model_name=self._model_name,
                success=success, latency_ms=latency_ms, flush=False,
            )
        except Exception:
            logger.warning("routing_outcome_record_failed", exc_info=True)

    async def flush(self) -> None:
        """Persist the stage's aggregated row once (never raises)."""
        if not self.enabled:
            return
        try:
            await self._repo.flush_entry(self._task_type, self._model_name)
        except Exception:
            logger.warning("routing_outcome_flush_failed", exc_info=True)


async def _llm_generate(
    model: Any,
    prompt: str,
    agent: Any | None = None,
    *,
    output_model: type[BaseModel] | None = None,
    telemetry: Callable[[bool, float], Awaitable[None]] | None = None,
) -> BaseModel | None:
    """Generate a schema-validated response from the LLM via a Strands Agent.

    ``agent`` may be supplied by callers that run many prompts (stages 2/3)
    so one client is reused instead of constructing an Agent per call.
    Returns ``result.structured_output`` (the validated model instance, or
    None when the provider returned nothing parseable). ``telemetry``, when
    given, is awaited with ``(success, latency_ms)`` after every invocation —
    including the failure path (the exception is re-raised after recording).
    """
    routing = _describe_routing(model)
    logger.info(
        "llm_generate",
        provider=routing["provider"],
        model=routing["model"],
        output_model=getattr(output_model, "__name__", "none"),
        prompt_chars=len(prompt),
    )
    if agent is None:
        agent = Agent(model=model, structured_output_model=output_model)
    start = time.monotonic()
    try:
        result = await agent.invoke_async(
            prompt, structured_output_model=output_model, limits=LLM_LIMITS,
        )
    except BaseException:
        if telemetry is not None:
            await telemetry(False, (time.monotonic() - start) * 1000.0)
        raise
    latency_ms = (time.monotonic() - start) * 1000.0
    _record_usage(result)
    if telemetry is not None:
        await telemetry(result.structured_output is not None, latency_ms)
    return result.structured_output


EXPECTED_TOPICS = {
    "readme", "getting started", "installation", "api",
    "usage", "examples", "changelog", "contributing",
}
CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```")
HEADING_PATTERN = re.compile(r"^#{1,3}\s+", re.MULTILINE)
WORD_RANGE = (200, 5000)

EVALUATION_LLM_PROMPT = (
    "Evaluate this documentation's quality. Rate each dimension from 0.0 (poor) "
    "to 1.0 (excellent): coverage, completeness, structure, length.\n\n"
    "Documentation title: {title}\n"
    "Content:\n"
    "{content}"
)

HEURISTIC_WEIGHT = 0.4
LLM_WEIGHT = 0.6

RECOMMENDATION_PROMPT = (
    "You are a documentation quality advisor. Based on the metrics below, "
    "generate 3-5 prioritized recommendations for improving the documentation.\n\n"
    "Health Score: {health_score:.2f}/1.0\n"
    "Document Count: {document_count}\n"
    "Chunk Count: {chunk_count}\n\n"
    "Dimension Scores (0-1):\n"
    "- Coverage: {coverage:.2f}\n"
    "- Completeness: {completeness:.2f}\n"
    "- Structure: {structure:.2f}\n"
    "- Length: {length:.2f}\n\n"
    "Low-scoring dimensions need the most attention."
)


async def run_knowledge_construction(
    context: Any,
    *,
    org_id: str,
    publish: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> KnowledgeExtractionResult:
    """Stage 2: Extract knowledge from synced document chunks via LLM."""
    from draftly.memory.candidates.models import MemoryCandidate
    from draftly.memory.models.knowledge import Knowledge

    result = KnowledgeExtractionResult()

    chunks = await context.memory.recall(
        namespace="documents",
        query="*",
        limit=500,
        org_id=org_id,
    )

    if not chunks:
        logger.info("knowledge_construction_no_chunks org=%s", org_id)
        return result

    total = len(chunks)
    # Resolve routed model + recorder for this stage
    stage_model, routing_decision = _resolve_stage_model(
        context, "knowledge_extractor",
    )
    recorder = _OutcomeRecorder(context, routing_decision)
    # Task 7: one Agent per stage, reused for every chunk — constructing an
    # Agent per call re-did provider setup for all 500 calls. Guarded so an
    # unresolvable model degrades to per-call construction (previous behavior).
    try:
        agent = Agent(model=stage_model, structured_output_model=ExtractionOutput)
    except Exception:
        agent = None
    sem = asyncio.Semaphore(LLM_MAX_CONCURRENCY)

    async def _extract(chunk: dict) -> tuple[ExtractionOutput | None, str]:
        """LLM-extract one chunk under the concurrency cap and chunk timeout."""
        content = chunk.get("content", "")
        cid = chunk.get("id", "unknown")
        if not content.strip():
            return None, cid
        prompt = EXTRACTION_PROMPT.format(content=content[:2000])
        try:
            async with sem:
                extracted = await asyncio.wait_for(
                    _llm_generate(
                        stage_model, prompt, agent=agent,
                        output_model=ExtractionOutput,
                        telemetry=recorder.record,
                    ),
                    timeout=CHUNK_TIMEOUT_SECONDS,
                )
            return extracted, cid
        except Exception as exc:
            # TimeoutError subclasses Exception on 3.11+ — hung providers are
            # recorded as failed chunks instead of stalling the workflow.
            logger.warning("knowledge_extraction_chunk_failed chunk=%s err=%s", cid, exc)
            return None, cid

    for i in range(0, total, CHUNK_BATCH_SIZE):
        batch = chunks[i : i + CHUNK_BATCH_SIZE]
        facts: list[Knowledge] = []

        extracted_results = await asyncio.gather(
            *(_extract(chunk) for chunk in batch)
        )

        for chunk, (extracted, chunk_id) in zip(batch, extracted_results):
            if extracted is None:
                # Empty content or a failed/timed-out extraction.
                result.failed_chunks.append(chunk_id)
                continue

            # Collect extracted facts; stored in one batch at batch end
            # (Task 6: 1 embed_batch + 1 transaction per batch instead of
            # per-fact remember() round trips).
            for fact in extracted.facts:
                facts.append(
                    Knowledge(
                        namespace="knowledge",
                        content=fact,
                        org_id=org_id,
                        topic=chunk.get("metadata", {}).get("title"),
                        source_quality=0.7,
                    )
                )

            try:
                for rel in extracted.relationships:
                    await context.docgraph.link(
                        source_key=rel.source,
                        target_key=rel.target,
                        relation_type=rel.type,
                        org_id=org_id,
                    )
                    result.relationship_count += 1

                # Store procedure patterns
                for proc in extracted.procedures:
                    candidate = MemoryCandidate(
                        org_id=org_id,
                        candidate_type="procedure_pattern",
                        payload=proc.model_dump(),
                        source_type="document_chunk",
                        source_id=chunk_id,
                        evidence=[chunk.get("content", "")[:200]],
                        confidence=0.6,
                    )
                    await context.candidates.enqueue(candidate)
                    result.candidate_count += 1
            except Exception as exc:
                logger.warning(
                    "knowledge_postprocess_failed chunk=%s err=%s", chunk_id, exc
                )
                result.failed_chunks.append(chunk_id)

        # Store all facts of this batch in one call (single embed_batch +
        # single transaction per batch). A store failure marks the batch's
        # chunks failed rather than losing the count silently.
        if facts:
            try:
                await context.memory.store_batch(facts)
                result.knowledge_count += len(facts)
            except Exception as exc:
                logger.warning(
                    "knowledge_fact_store_failed count=%d err=%s", len(facts), exc
                )
                result.failed_chunks.extend(
                    chunk.get("id", "unknown") for chunk in batch
                )

        processed = min(i + CHUNK_BATCH_SIZE, total)
        await publish("tool_progress", {
            "name": "knowledge_extraction",
            "processed": processed,
            "total": total,
        })
        # Granular stage progress so the UI bar animates during slow LLM
        # extraction. Band keeps values above the start (10) emitted in
        # initialize.py and below the closing 100 emitted after this returns.
        pct = processed / total
        await publish("stage_progress", {
            "stage": "knowledge_construction",
            "progress": min(int(10 + pct * 85), 95),
        })
        logger.info(
            "knowledge_construction_progress",
            stage="knowledge_construction",
            done=processed,
            total=total,
        )

    logger.info(
        "knowledge_construction_done",
        stage="knowledge_construction",
        org_id=org_id,
        facts=result.knowledge_count,
        rels=result.relationship_count,
        procs=result.candidate_count,
        failed=len(result.failed_chunks),
    )
    await recorder.flush()
    return result


async def run_initial_evaluation(
    context: Any,
    *,
    org_id: str,
    publish: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
) -> EvaluationResult:
    """Stage 3: Score documentation corpus quality using heuristics + LLM."""
    docs = await context.memory.recall(
        namespace="documents",
        query="*",
        limit=500,
        org_id=org_id,
    )

    if not docs:
        return EvaluationResult(score=0.0, dimensions={
            "coverage": 0.0, "completeness": 0.0,
            "structure": 0.0, "length": 0.0,
        })

    # Resolve routed model + recorder for this stage
    stage_model, routing_decision = _resolve_stage_model(
        context, "initial_evaluator",
    )
    recorder = _OutcomeRecorder(context, routing_decision)

    total = len(docs)
    coverage_hits = 0
    completeness_sum = 0.0
    structure_hits = 0
    length_hits = 0

    for doc in docs:
        content = doc.get("content", "")
        title = doc.get("metadata", {}).get("title", "").lower()
        text = f"{title} {content}".lower()

        if any(topic in text for topic in EXPECTED_TOPICS):
            coverage_hits += 1

        has_title = bool(title)
        has_headings = bool(HEADING_PATTERN.search(content))
        has_code = bool(CODE_BLOCK_PATTERN.search(content))
        has_links = bool(re.search(r"\[.+\]\(.+\)", content))
        completeness_sum += sum([has_title, has_headings, has_code, has_links]) / 4.0

        headings = HEADING_PATTERN.findall(content)
        if headings:
            structure_hits += 1

        word_count = len(content.split())
        if WORD_RANGE[0] <= word_count <= WORD_RANGE[1]:
            length_hits += 1

    heuristic = {
        "coverage": coverage_hits / max(total, 1),
        "completeness": completeness_sum / max(total, 1),
        "structure": structure_hits / max(total, 1),
        "length": length_hits / max(total, 1),
    }

    # LLM-based semantic evaluation (blended with heuristics)
    llm_scores: dict[str, float] = {dim: 0.0 for dim in heuristic}
    llm_count = 0

    if stage_model is not None:
        # Task 7: bounded-concurrency, timeout-guarded LLM evaluation with a
        # single reused Agent (same pattern as the extraction stage).
        try:
            agent = Agent(model=stage_model, structured_output_model=EvaluationScores)
        except Exception:
            agent = None
        sem = asyncio.Semaphore(LLM_MAX_CONCURRENCY)

        async def _evaluate(doc: dict) -> EvaluationScores | None:
            content = doc.get("content", "")
            title = doc.get("metadata", {}).get("title", "Untitled")
            if not content.strip():
                return None
            prompt = EVALUATION_LLM_PROMPT.format(
                title=title, content=content[:2000],
            )
            try:
                async with sem:
                    scores = await asyncio.wait_for(
                        _llm_generate(
                            stage_model, prompt, agent=agent,
                            output_model=EvaluationScores,
                            telemetry=recorder.record,
                        ),
                        timeout=CHUNK_TIMEOUT_SECONDS,
                    )
                return scores
            except Exception as exc:
                logger.warning("evaluation_llm_chunk_failed doc=%s err=%s", title, exc)
                return None

        # Task 8: sample the LLM pass — heuristics above already scanned the
        # full corpus, so only EVAL_LLM_SAMPLE_SIZE docs get an LLM call.
        eval_docs = _sample_docs(docs)
        eval_total = len(eval_docs)
        eval_results = await asyncio.gather(
            *(_evaluate(doc) for doc in eval_docs)
        )

        for i, scores in enumerate(eval_results):
            if scores:
                for dim in llm_scores:
                    llm_scores[dim] += getattr(scores, dim)
                llm_count += 1

            if publish and (i + 1) % 10 == 0:
                processed = i + 1
                await publish("tool_progress", {
                    "name": "initial_evaluation",
                    "processed": processed,
                    "total": eval_total,
                })
                # Granular stage progress so the UI bar animates during the
                # (slow) LLM evaluation pass.
                pct = processed / eval_total
                await publish("stage_progress", {
                    "stage": "initial_evaluation",
                    "progress": min(int(pct * 100), 95),
                })

        logger.info(
            "initial_evaluation_sampled sampled=%d total=%d", llm_count, total
        )

    # Blend: prefer LLM when available, fall back to heuristic
    dimensions = {}
    for dim in heuristic:
        if llm_count > 0:
            avg_llm = llm_scores[dim] / llm_count
            dimensions[dim] = HEURISTIC_WEIGHT * heuristic[dim] + LLM_WEIGHT * avg_llm
        else:
            dimensions[dim] = heuristic[dim]

    score = (
        0.3 * dimensions["coverage"]
        + 0.3 * dimensions["completeness"]
        + 0.2 * dimensions["structure"]
        + 0.2 * dimensions["length"]
    )

    logger.info(
        "initial_evaluation_done",
        stage="initial_evaluation",
        org_id=org_id,
        score=score,
        llm_docs=llm_count,
        total=total,
    )
    await recorder.flush()
    return EvaluationResult(score=score, dimensions=dimensions)


def run_health_report(
    *,
    eval_result: EvaluationResult,
    document_count: int,
    section_count: int,
    last_committed_dates: list[datetime | None] | None = None,
) -> HealthResult:
    """Stage 4: Compute composite health score from evaluation + baseline stats."""
    doc_count_score = min(document_count / 50, 1.0)
    section_ratio = section_count / max(document_count, 1)
    section_ratio_score = min(section_ratio / 5, 1.0)

    health_score = (
        0.7 * eval_result.score
        + 0.15 * doc_count_score
        + 0.15 * section_ratio_score
    )

    # Compute freshness from per-file commit dates
    from draftly.documentation.validator import STALE_DAYS, DocumentationValidator

    validator = DocumentationValidator()
    dates = last_committed_dates or []
    days_list = [
        d for d in (validator.check_freshness(dt) for dt in dates)
        if d is not None
    ]
    if days_list:
        avg_days = sum(days_list) / len(days_list)
        freshness = max(0.0, 1.0 - avg_days / STALE_DAYS)
    else:
        freshness = 0.5  # unknown = neutral

    dimensions = {
        "coverage": eval_result.dimensions.get("coverage", 0.0),
        "structure": eval_result.dimensions.get("structure", 0.0),
        "freshness": freshness,
        "completeness": eval_result.dimensions.get("completeness", 0.0),
    }

    logger.info(
        "health_report_done", stage="health_report", score=health_score
    )
    return HealthResult(score=health_score, dimensions=dimensions)


async def run_recommendations(
    context: Any,
    *,
    eval_result: EvaluationResult,
    health_result: HealthResult,
    document_count: int,
    chunk_count: int,
) -> list[Recommendation]:
    """Stage 5: Generate prioritized recommendations via LLM."""
    prompt = RECOMMENDATION_PROMPT.format(
        health_score=health_result.score,
        document_count=document_count,
        chunk_count=chunk_count,
        coverage=eval_result.dimensions.get("coverage", 0.0),
        completeness=eval_result.dimensions.get("completeness", 0.0),
        structure=eval_result.dimensions.get("structure", 0.0),
        length=eval_result.dimensions.get("length", 0.0),
    )

    # Resolve routed model + recorder for this stage
    stage_model, routing_decision = _resolve_stage_model(
        context, "recommender", prompt_text=prompt,
    )
    recorder = _OutcomeRecorder(context, routing_decision)

    try:
        # Task 7: reuse one Agent for the single recommendation call (symmetry
        # with the extraction/evaluation stages).
        try:
            agent = Agent(model=stage_model, structured_output_model=RecommendationList)
        except Exception:
            agent = None
        parsed = await _llm_generate(
            stage_model, prompt, agent=agent, output_model=RecommendationList,
            telemetry=recorder.record,
        )
        if parsed is None:
            return []
        return list(parsed.items)
    except Exception as exc:
        logger.warning("recommendations_generation_failed err=%s", exc)
        return []
    finally:
        await recorder.flush()
