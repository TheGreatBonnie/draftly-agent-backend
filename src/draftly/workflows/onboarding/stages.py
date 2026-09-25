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
from strands import Agent  # noqa: F401 -- patched by tests/unit/workflows/test_onboarding_stages.py

from draftly.agents.factory import build_draftly_agent
from draftly.integrations.strands.models import RoleAwareModelResolver
from draftly.observability.metrics import Metrics
from draftly.observability.metrics import metrics as _metrics_default
from draftly.steering.context import SteeringRuntime
from draftly.steering.decisions import AgentRole

logger = structlog.get_logger(__name__)

_metrics: Metrics = _metrics_default

EXTRACTION_PROMPT = """Extract structured knowledge from this documentation chunk.

Chunk content:
{content}"""

EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured knowledge facts from documentation chunks into "
    "the requested schema."
)

EVALUATION_SYSTEM_PROMPT = (
    "You evaluate documentation quality dimensions (coverage, completeness, "
    "structure, length) from the requested schema."
)

RECOMMENDER_SYSTEM_PROMPT = (
    "You are a documentation quality advisor producing prioritized "
    "improvement recommendations."
)

LLM_GENERATE_FALLBACK_PROMPT = (
    "You are a structured-output helper. Respond with the requested schema "
    "and nothing else."
)


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


class ChunkExtraction(ExtractionOutput):
    chunk_id: str


class GroupedExtractionOutput(BaseModel):
    items: list[ChunkExtraction] = Field(default_factory=list)


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
EXTRACTION_GROUP_MAX_CHUNKS = 4
EXTRACTION_GROUP_MAX_CHARS = 6000
EXTRACTION_PERSIST_GROUPS = 4
# Per-chunk/LLM-call timeout in seconds. "0" disables the per-call deadline so
# slower providers (e.g. kimi-k2.5, which streams a structured tool call before
# returning) are given time to complete instead of being cut off mid-stream
# (observed as every chunk failing with a bare TimeoutError). Mirrors the
# LLM_TOTAL_TOKENS_CAP=0 -> no-limit convention. Deployments can restore a hard
# ceiling by setting CHUNK_TIMEOUT_SECONDS to a positive value.
CHUNK_TIMEOUT_SECONDS = int(os.environ.get("CHUNK_TIMEOUT_SECONDS", "0"))

# Per-batch ceiling for stage 2 (extraction + store). Replaces the removed
# overall workflow watchdog (INIT_WORKFLOW_TIMEOUT_SECONDS): a hung provider
# or embedder stalls one batch for at most this long, is recorded as failed,
# and the stage continues. Positive default; set env to 0 to disable.
KNOWLEDGE_BATCH_TIMEOUT_SECONDS = int(
    os.environ.get("KNOWLEDGE_BATCH_TIMEOUT_SECONDS", "600")
)

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

# Cap on stage_progress events emitted per LLM-heavy stage regardless of corpus
# size. Protects SSE volume and keeps the Redis event stream (MAX_STREAM_LEN)
# safe.
EMIT_TICK_BUDGET = 50


def tick_interval(total_units: int, budget: int = EMIT_TICK_BUDGET) -> int:
    """Number of work units between progress emits, so a stage emits <= budget
    ticks total (plus the final one), independent of corpus size."""
    if total_units <= 0:
        return 1
    # Integer ceiling (-(-n // b)) so stages.py needs no `import math`
    return max(1, -(-total_units // budget))


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
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
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
        agent = build_draftly_agent(
            role=AgentRole.RESEARCH,
            system_prompt=LLM_GENERATE_FALLBACK_PROMPT,
            model=model,
            structured_output_model=output_model,
            runtime=runtime or SteeringRuntime.disabled(),
            agent_id=agent_id or "onboarding-llm",
            node_id=node_id or "onboarding-llm",
        )
    start = time.monotonic()
    try:
        result = await agent.invoke_async(
            prompt, structured_output_model=output_model, limits=LLM_LIMITS,
        )
    except BaseException as exc:
        logger.warning(
            "llm_generate_failed", latency_ms=round((time.monotonic() - start) * 1000),
            err_type=type(exc).__name__,
        )
        if telemetry is not None:
            await telemetry(False, (time.monotonic() - start) * 1000.0)
        raise
    latency_ms = (time.monotonic() - start) * 1000.0
    _record_usage(result)
    usage = getattr(getattr(result, "metrics", None), "accumulated_usage", None)
    usage = usage if isinstance(usage, dict) else {}
    logger.info(
        "llm_generate_done", latency_ms=round(latency_ms),
        input_tokens=int(usage.get("inputTokens") or 0),
        output_tokens=int(usage.get("outputTokens") or 0),
        structured_output=bool(result.structured_output is not None),
    )
    if telemetry is not None:
        await telemetry(result.structured_output is not None, latency_ms)
    return result.structured_output


def _agent_pool(
    model: Any,
    output_model: type[BaseModel] | None,
    size: int,
    *,
    role: AgentRole = AgentRole.WRITER,
    system_prompt: str = "",
    runtime: SteeringRuntime | None = None,
    agent_id_prefix: str = "onboarding-agent",
    node_id: str = "onboarding",
) -> asyncio.Queue | None:
    """Build ``size`` Strands Agents so concurrent calls never share one.

    A Strands ``Agent`` supports only one in-flight ``invoke_async``; reusing a
    single agent across concurrent ``asyncio.gather`` calls raises
    ``ConcurrencyException`` (observed in production as
    ``knowledge_extraction_chunk_failed`` / ``evaluation_llm_chunk_failed`` with
    *"Agent is already processing a request. Concurrent invocations are not
    supported."*). Giving each concurrent slot its own agent retains the
    Task-7 provider-setup reuse (bounded construction) while serializing per
    agent. Each pooled agent gets a distinct identity
    (``{agent_id_prefix}-{i}``) sharing only the parent run runtime. Returns
    ``None`` when no agent can be built so callers fall back to per-call
    construction inside ``_llm_generate`` (``agent=None``). The pool is sized
    to ``LLM_MAX_CONCURRENCY`` so a held ``Semaphore`` permit can always
    acquire an agent — never a deadlock.
    """
    pool: asyncio.Queue = asyncio.Queue()
    effective_runtime = runtime or SteeringRuntime.disabled()
    for i in range(max(1, size)):
        try:
            pool.put_nowait(
                build_draftly_agent(
                    role=role,
                    system_prompt=system_prompt,
                    model=model,
                    structured_output_model=output_model,
                    runtime=effective_runtime,
                    agent_id=f"{agent_id_prefix}-{i}",
                    node_id=node_id,
                )
            )
        except Exception:
            # Unresolvable model degrades to per-call construction (previous
            # behavior), which _llm_generate handles with agent=None.
            break
    return pool if not pool.empty() else None


async def _llm_with_chunk_timeout(coro: Awaitable[Any]) -> Any:
    """Await an LLM coroutine, optionally bounded by ``CHUNK_TIMEOUT_SECONDS``.

    ``CHUNK_TIMEOUT_SECONDS > 0`` enforces a per-call deadline so a hung
    provider is recorded as a failed chunk rather than stalling the batch.
    ``0`` disables the deadline, letting slower providers complete.
    """
    if CHUNK_TIMEOUT_SECONDS > 0:
        return await asyncio.wait_for(coro, timeout=CHUNK_TIMEOUT_SECONDS)
    return await coro


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


def _group_extraction_chunks(chunks: list[dict]) -> list[list[dict]]:
    """Pack chunks from one page without changing their retrieval granularity."""
    pages: dict[str, list[dict]] = {}
    for chunk in chunks:
        meta = chunk.get("metadata") or {}
        page = meta.get("document_id") or meta.get("path") or meta.get("source_url")
        # Unattributed chunks stay independent; do not merge unrelated pages.
        key = str(page) if page else f"__chunk__:{chunk.get('id', id(chunk))}"
        pages.setdefault(key, []).append(chunk)

    groups: list[list[dict]] = []
    for page_chunks in pages.values():
        page_chunks.sort(key=lambda c: (c.get("metadata") or {}).get("start_line") or 0)
        current: list[dict] = []
        chars = 0
        for chunk in page_chunks:
            size = len(chunk.get("content") or "")
            if not (chunk.get("content") or "").strip():
                if current:
                    groups.append(current)
                    current, chars = [], 0
                groups.append([chunk])
                continue
            if current and (
                len(current) >= EXTRACTION_GROUP_MAX_CHUNKS
                or chars + size > EXTRACTION_GROUP_MAX_CHARS
            ):
                groups.append(current)
                current, chars = [], 0
            current.append(chunk)
            chars += size
        if current:
            groups.append(current)
    return groups


async def run_knowledge_construction(
    context: Any,
    *,
    org_id: str,
    publish: Callable[[str, dict[str, Any]], Awaitable[None]],
    source_type: str = "github_repository",
) -> KnowledgeExtractionResult:
    """Stage 2: extract with independent agents and persist completed groups."""
    if _research_synthesis_enabled(context) and source_type == "public_documentation":
        return await _run_research_synthesis(context, org_id=org_id, publish=publish)
    from draftly.memory.candidates.models import MemoryCandidate
    from draftly.memory.candidates.service import CandidateService
    from draftly.memory.docgraph.service import DocGraphService
    from draftly.memory.models.knowledge import Knowledge

    result = KnowledgeExtractionResult()
    started = time.monotonic()
    chunks = await context.memory.recall(
        namespace="documents", query="*", limit=500, org_id=org_id,
    )
    if not chunks:
        logger.info("knowledge_construction_no_chunks org=%s", org_id)
        return result

    groups = _group_extraction_chunks(chunks)
    total = len(chunks)
    stage_model, routing_decision = _resolve_stage_model(context, "knowledge_extractor")
    recorder = _OutcomeRecorder(context, routing_decision)
    request_count = 0
    throttled_count = 0
    persistence_ms = 0.0
    emit_every = tick_interval(total)

    async def _bounded(coro: Awaitable[Any]) -> Any:
        if KNOWLEDGE_BATCH_TIMEOUT_SECONDS > 0:
            return await asyncio.wait_for(coro, timeout=KNOWLEDGE_BATCH_TIMEOUT_SECONDS)
        return await coro

    async def _call(prompt: str, schema: type[BaseModel]) -> BaseModel | None:
        nonlocal request_count, throttled_count
        request_count += 1
        try:
            agent = build_draftly_agent(
                role=AgentRole.WRITER,
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                model=stage_model,
                structured_output_model=schema,
                runtime=SteeringRuntime.disabled(),
                agent_id=f"onboarding-extraction-{request_count}",
                node_id="onboarding-extraction",
            )
            return await _llm_with_chunk_timeout(
                _llm_generate(
                    stage_model, prompt, agent=agent, output_model=schema,
                    telemetry=recorder.record,
                )
            )
        except Exception as exc:
            if "throttl" in type(exc).__name__.lower() or "rate limit" in str(exc).lower():
                throttled_count += 1
            logger.warning(
                "knowledge_extraction_request_failed err=%s err_type=%s",
                exc, type(exc).__name__,
            )
            return None

    async def _extract_group(group: list[dict]) -> list[tuple[dict, ExtractionOutput | None]]:
        if len(group) == 1:
            chunk = group[0]
            if not (chunk.get("content") or "").strip():
                return [(chunk, None)]
            prompt = EXTRACTION_PROMPT.format(content=(chunk.get("content") or "")[:2000])
            output = await _call(prompt, ExtractionOutput)
            return [(chunk, output if isinstance(output, ExtractionOutput) else None)]

        prompt = (
            "Extract facts, relationships, and procedures separately for every "
            "documentation chunk below. Return exactly one item per Chunk ID; "
            "copy each ID exactly.\n\n"
            + "\n\n".join(
                f"Chunk ID: {chunk.get('id', 'unknown')}\nContent:\n"
                f"{(chunk.get('content') or '')[:EXTRACTION_GROUP_MAX_CHARS]}"
                for chunk in group
            )
        )
        output = await _call(prompt, GroupedExtractionOutput)
        expected = [str(chunk.get("id", "unknown")) for chunk in group]
        if isinstance(output, GroupedExtractionOutput):
            found = [item.chunk_id for item in output.items]
            if len(found) == len(expected) and set(found) == set(expected):
                by_id = {item.chunk_id: item for item in output.items}
                return [(chunk, by_id[str(chunk.get("id", "unknown"))]) for chunk in group]
        logger.warning("knowledge_extraction_group_invalid count=%d", len(group))
        # No grouped output is persisted until all IDs validate. Retry each
        # chunk independently so one malformed group cannot lose coverage.
        results: list[tuple[dict, ExtractionOutput | None]] = []
        for chunk in group:
            prompt = EXTRACTION_PROMPT.format(content=(chunk.get("content") or "")[:2000])
            output = await _call(prompt, ExtractionOutput)
            results.append((chunk, output if isinstance(output, ExtractionOutput) else None))
        return results

    work_queue: asyncio.Queue[list[dict] | None] = asyncio.Queue(maxsize=LLM_MAX_CONCURRENCY * 2)
    completed_queue: asyncio.Queue[list[tuple[dict, ExtractionOutput | None]]] = asyncio.Queue(
        maxsize=LLM_MAX_CONCURRENCY * 2
    )

    async def _produce() -> None:
        for group in groups:
            await work_queue.put(group)
        for _ in range(LLM_MAX_CONCURRENCY):
            await work_queue.put(None)

    async def _worker() -> None:
        while True:
            group = await work_queue.get()
            if group is None:
                return
            try:
                extracted = await _bounded(_extract_group(group))
            except TimeoutError:
                logger.warning("knowledge_group_extract_timeout count=%d", len(group))
                extracted = [(chunk, None) for chunk in group]
            except Exception as exc:
                logger.warning("knowledge_group_extract_failed count=%d err=%s", len(group), exc)
                extracted = [(chunk, None) for chunk in group]
            await completed_queue.put(extracted)

    async def _persist(batch: list[tuple[dict, ExtractionOutput | None]]) -> None:
        nonlocal persistence_ms
        started_persist = time.monotonic()
        facts: list[Knowledge] = []
        relations: list[dict] = []
        candidates: list[MemoryCandidate] = []
        batch_ids = [chunk.get("id", "unknown") for chunk, _ in batch]
        for chunk, extracted in batch:
            chunk_id = chunk.get("id", "unknown")
            if extracted is None:
                result.failed_chunks.append(chunk_id)
                continue
            for fact in extracted.facts:
                facts.append(Knowledge(
                    namespace="knowledge", content=fact, org_id=org_id,
                    topic=(chunk.get("metadata") or {}).get("title"), source_quality=0.7,
                ))
            for rel in extracted.relationships:
                relations.append({
                    "_chunk_id": chunk_id, "source": rel.source,
                    "target": rel.target, "type": rel.type, "org_id": org_id,
                    "source_type": "code", "target_type": "doc",
                })
            for proc in extracted.procedures:
                candidates.append(MemoryCandidate(
                    org_id=org_id, candidate_type="procedure_pattern",
                    payload=proc.model_dump(), source_type="document_chunk",
                    source_id=chunk_id, evidence=[(chunk.get("content") or "")[:200]],
                    confidence=0.6,
                ))

        if relations:
            if isinstance(context.docgraph, DocGraphService):
                try:
                    result.relationship_count += await context.docgraph.link_batch(relations)
                except Exception as exc:
                    logger.warning(
                        "knowledge_link_batch_failed count=%d err=%s", len(relations), exc
                    )
                    result.failed_chunks.extend(batch_ids)
            else:
                for rel in relations:
                    try:
                        await context.docgraph.link(
                            source_key=rel["source"], target_key=rel["target"],
                            relation_type=rel["type"], org_id=org_id,
                        )
                        result.relationship_count += 1
                    except Exception as exc:
                        logger.warning("knowledge_link_failed err=%s", exc)
                        result.failed_chunks.append(rel["_chunk_id"])
        if candidates:
            if isinstance(context.candidates, CandidateService):
                try:
                    result.candidate_count += await context.candidates.enqueue_batch(candidates)
                except Exception as exc:
                    logger.warning(
                        "knowledge_enqueue_batch_failed count=%d err=%s", len(candidates), exc
                    )
                    result.failed_chunks.extend(batch_ids)
            else:
                for candidate in candidates:
                    try:
                        await context.candidates.enqueue(candidate)
                        result.candidate_count += 1
                    except Exception as exc:
                        logger.warning("knowledge_enqueue_failed err=%s", exc)
                        result.failed_chunks.append(candidate.source_id)
        if facts:
            try:
                await _bounded(context.memory.store_batch(facts))
                result.knowledge_count += len(facts)
            except TimeoutError:
                logger.warning("knowledge_batch_store_timeout count=%d", len(facts))
                result.failed_chunks.extend(batch_ids)
            except Exception as exc:
                logger.warning("knowledge_fact_store_failed count=%d err=%s", len(facts), exc)
                result.failed_chunks.extend(batch_ids)
        persistence_ms += (time.monotonic() - started_persist) * 1000.0

    producer = asyncio.create_task(_produce())
    workers = [asyncio.create_task(_worker()) for _ in range(LLM_MAX_CONCURRENCY)]
    pending: list[tuple[dict, ExtractionOutput | None]] = []
    pending_groups = 0
    completed_groups = 0
    processed = 0
    last_emitted = 0

    async def _flush() -> None:
        nonlocal pending_groups, last_emitted
        if not pending:
            return
        await _persist(pending)
        pending.clear()
        pending_groups = 0
        await publish("tool_progress", {
            "name": "knowledge_extraction", "processed": processed, "total": total,
            "knowledge_count": result.knowledge_count,
            "relationship_count": result.relationship_count,
        })
        if processed == total or processed - last_emitted >= emit_every:
            last_emitted = processed
            await publish("stage_progress", {
                "stage": "knowledge_construction",
                "progress": min(int(10 + processed / total * 85), 95),
            })
            logger.info("knowledge_construction_progress", stage="knowledge_construction",
                        done=processed, total=total)

    try:
        while completed_groups < len(groups):
            try:
                if pending:
                    extracted = await asyncio.wait_for(completed_queue.get(), timeout=0.05)
                else:
                    extracted = await completed_queue.get()
            except TimeoutError:
                await _flush()
                continue
            completed_groups += 1
            processed += len(extracted)
            pending.extend(extracted)
            pending_groups += 1
            if pending_groups >= min(EXTRACTION_PERSIST_GROUPS, CHUNK_BATCH_SIZE):
                await _flush()
        await _flush()
        await producer
        await asyncio.gather(*workers)
    finally:
        producer.cancel()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(producer, *workers, return_exceptions=True)

    logger.info(
        "knowledge_construction_done", stage="knowledge_construction", org_id=org_id,
        facts=result.knowledge_count, rels=result.relationship_count,
        procs=result.candidate_count, failed=len(result.failed_chunks),
        chunks=total, groups=len(groups), requests=request_count,
        throttled=throttled_count, persistence_ms=round(persistence_ms),
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    await recorder.flush()
    return result


_RESEARCH_SHARD_QUERY = (
    "Extract facts, relationships, and procedures "
    "from the attached documentation files."
)


def _research_synthesis_enabled(context: Any) -> bool:
    """True only when the research flag is explicitly on.

    ``is True`` (not truthiness) keeps MagicMock-based contexts on the
    legacy LLM path unless a test opts in with a real True.
    """
    return (
        getattr(getattr(context, "config", None), "tavily_research_enabled", False)
        is True
    )


def _tavily_client_from_config(config: Any) -> Any:
    from draftly.integrations.tavily.client import TavilyClient

    return TavilyClient(
        getattr(config, "tavily_api_key", None) or "",
        base_url=getattr(config, "tavily_base_url", "https://api.tavily.com"),
        timeout_seconds=getattr(config, "tavily_request_timeout_seconds", 60),
        max_concurrency=getattr(config, "tavily_max_concurrency", 4),
    )


async def _run_research_synthesis(
    context: Any,
    *,
    org_id: str,
    publish: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> KnowledgeExtractionResult:
    """Stage 2 (research path, public sources only).

    The corpus is sliced through the index into deterministic RAG shards
    (page-type strata, file/word capped); each shard goes through one
    ``Research(model=mini, files=encode(shard), output_schema=ExtractionOutput)``
    call. Output validation, persistence stores, and event names match the
    legacy per-chunk path; per-shard failures record their source IDs.
    """
    from pydantic import ValidationError

    from draftly.documentation.tavily_source import (
        build_research_shards,
        files_for_shard,
        group_chunks_into_pages,
    )
    from draftly.integrations.tavily.errors import (
        TavilyError,
        TavilyErrorCode,
        is_retryable,
    )

    result = KnowledgeExtractionResult()
    config = getattr(context, "config", None)

    chunks = await context.memory.recall(
        namespace="documents",
        query="*",
        limit=500,
        org_id=org_id,
    )
    if not chunks:
        logger.info("knowledge_construction_no_chunks org=%s", org_id)
        return result

    pages, orphans = group_chunks_into_pages(chunks)
    result.failed_chunks.extend(orphans)
    if not pages:
        return result

    by_type: dict[str, list] = {}
    for page in pages:
        page_type = (page.metadata or {}).get("page_type", "index")
        by_type.setdefault(page_type, []).append(page)
    shards: list = []
    for page_type in sorted(by_type):
        shards.extend(build_research_shards(by_type[page_type]))

    client = _tavily_client_from_config(config)
    budget = getattr(config, "tavily_credit_budget", None)
    poll_timeout = getattr(config, "tavily_research_poll_timeout_seconds", 300)
    credits = 0.0
    total = len(shards)
    emit_every = tick_interval(total)
    try:
        for index, shard in enumerate(shards):
            shard_ids = [p.source_id for p in shard]
            if budget is not None and credits >= budget:
                raise RuntimeError(
                    f"tavily credit budget exceeded ({credits} >= {budget})"
                )
            try:
                created = await client.research(
                    query=_RESEARCH_SHARD_QUERY,
                    model="mini",
                    files=files_for_shard(shard),
                    output_schema=ExtractionOutput.model_json_schema(),
                )
                polled = await client.research_poll(
                    created.request_id, poll_timeout_seconds=poll_timeout
                )
            except TavilyError as exc:
                if exc.code == TavilyErrorCode.CREDIT_LIMIT or is_retryable(exc.code):
                    raise
                logger.warning(
                    "knowledge_research_shard_failed shard=%d err=%s",
                    index, exc,
                )
                result.failed_chunks.extend(shard_ids)
                continue
            usage = getattr(polled, "usage", None)
            credits += float(getattr(usage, "credits_used", 0) or 0)
            try:
                extracted = ExtractionOutput.model_validate(polled.content)
            except ValidationError as exc:
                logger.warning(
                    "knowledge_research_invalid_output shard=%d err=%s",
                    index, exc,
                )
                result.failed_chunks.extend(shard_ids)
                continue
            await _store_shard_extraction(
                context,
                result,
                org_id=org_id,
                extracted=extracted,
                shard_ids=shard_ids,
                shard=shard,
            )
            processed = index + 1
            await publish("tool_progress", {
                "name": "knowledge_extraction",
                "processed": processed,
                "total": total,
                "knowledge_count": result.knowledge_count,
                "relationship_count": result.relationship_count,
            })
            if processed == total or processed % emit_every == 0:
                pct = processed / total
                await publish("stage_progress", {
                    "stage": "knowledge_construction",
                    "progress": min(int(10 + pct * 85), 95),
                })
    finally:
        await client.aclose()

    logger.info(
        "knowledge_construction_done",
        stage="knowledge_construction",
        org_id=org_id,
        facts=result.knowledge_count,
        rels=result.relationship_count,
        procs=result.candidate_count,
        failed=len(result.failed_chunks),
    )
    return result


async def _store_shard_extraction(
    context: Any,
    result: KnowledgeExtractionResult,
    *,
    org_id: str,
    extracted: ExtractionOutput,
    shard_ids: list[str],
    shard: list,
) -> None:
    """Persist one validated shard ExtractionOutput (deduped).

    Same stores and batch/per-item fallbacks as the legacy path; batch-level
    failures attribute the whole shard, per-item failures attribute it too
    (the shard is the smallest attributable unit for blended outputs).
    """
    from draftly.memory.candidates.models import MemoryCandidate
    from draftly.memory.candidates.service import CandidateService
    from draftly.memory.docgraph.service import DocGraphService
    from draftly.memory.models.knowledge import Knowledge

    evidence = " ".join(p.content for p in shard)[:200]
    facts: list[Knowledge] = []
    seen_facts: set[str] = set()
    for fact in extracted.facts:
        if fact in seen_facts:
            continue
        seen_facts.add(fact)
        facts.append(
            Knowledge(
                namespace="knowledge",
                content=fact,
                org_id=org_id,
                topic=None,
                source_quality=0.7,
            )
        )

    batch_relations: list[dict] = []
    seen_relations: set[tuple] = set()
    for rel in extracted.relationships:
        key = (rel.source, rel.target, rel.type)
        if key in seen_relations:
            continue
        seen_relations.add(key)
        batch_relations.append({
            "_chunk_id": None,
            "source": rel.source,
            "target": rel.target,
            "type": rel.type,
            "org_id": org_id,
            "source_type": "code",
            "target_type": "doc",
        })

    batch_candidates: list[MemoryCandidate] = []
    seen_procedures: set[str] = set()
    for proc in extracted.procedures:
        key = proc.title + "\n" + "\n".join(proc.steps)
        if key in seen_procedures:
            continue
        seen_procedures.add(key)
        batch_candidates.append(
            MemoryCandidate(
                org_id=org_id,
                candidate_type="procedure_pattern",
                payload=proc.model_dump(),
                source_type="document_chunk",
                source_id=None,
                evidence=[evidence],
                confidence=0.6,
            )
        )

    if batch_relations:
        if isinstance(context.docgraph, DocGraphService):
            try:
                result.relationship_count += await context.docgraph.link_batch(
                    batch_relations
                )
            except Exception as exc:
                logger.warning(
                    "knowledge_link_batch_failed count=%d err=%s",
                    len(batch_relations), exc,
                )
                result.failed_chunks.extend(shard_ids)
        else:
            for rel_def in batch_relations:
                try:
                    await context.docgraph.link(
                        source_key=rel_def["source"],
                        target_key=rel_def["target"],
                        relation_type=rel_def["type"],
                        org_id=org_id,
                    )
                    result.relationship_count += 1
                except Exception as exc:
                    logger.warning("knowledge_link_failed err=%s", exc)
                    result.failed_chunks.extend(shard_ids)

    if batch_candidates:
        if isinstance(context.candidates, CandidateService):
            try:
                result.candidate_count += await context.candidates.enqueue_batch(
                    batch_candidates
                )
            except Exception as exc:
                logger.warning(
                    "knowledge_enqueue_batch_failed count=%d err=%s",
                    len(batch_candidates), exc,
                )
                result.failed_chunks.extend(shard_ids)
        else:
            for cand in batch_candidates:
                try:
                    await context.candidates.enqueue(cand)
                    result.candidate_count += 1
                except Exception as exc:
                    logger.warning("knowledge_enqueue_failed err=%s", exc)
                    result.failed_chunks.extend(shard_ids)

    if facts:
        try:
            if KNOWLEDGE_BATCH_TIMEOUT_SECONDS > 0:
                await asyncio.wait_for(
                    context.memory.store_batch(facts),
                    timeout=KNOWLEDGE_BATCH_TIMEOUT_SECONDS,
                )
            else:
                await context.memory.store_batch(facts)
            result.knowledge_count += len(facts)
        except Exception as exc:
            logger.warning(
                "knowledge_fact_store_failed count=%d err=%s", len(facts), exc
            )
            result.failed_chunks.extend(shard_ids)


def _stratified_sample(
    docs: list[dict], total: int = EVAL_LLM_SAMPLE_SIZE
) -> list[dict]:
    """Round-robin across page-type strata (deterministic, recall order kept)."""
    from draftly.documentation.page_type import PAGE_TYPES, derive_page_type

    groups: dict[str, list] = {}
    for doc in docs:
        meta = doc.get("metadata") or {}
        page_type = meta.get("page_type") or derive_page_type(
            meta.get("source_url") or meta.get("path") or ""
        )
        groups.setdefault(page_type, []).append(doc)
    strata = [groups[page_type] for page_type in PAGE_TYPES if groups.get(page_type)]
    strata.extend(
        group for key, group in groups.items() if key not in PAGE_TYPES
    )
    out: list[dict] = []
    while len(out) < total and any(strata):
        for group in strata:
            if group and len(out) < total:
                out.append(group.pop(0))
    return out


_EVAL_RESEARCH_QUERY = (
    "Score this documentation sample from 0.0 (poor) to 1.0 (excellent) "
    "on coverage, completeness, structure, and length."
)


async def _research_eval_scores(
    context: Any,
    *,
    org_id: str,
    docs: list[dict],
    publish: Callable[[str, dict[str, Any]], Awaitable[None]] | None,
) -> dict[str, float] | None:
    """Score a stratified sample with one Research call; None on fallback.

    Research failure (non-credit) or invalid output degrades to pure
    heuristics. Credit-limit errors halt via the stage-fail path.
    """
    from pydantic import ValidationError

    from draftly.documentation.tavily_source import pack_sample_files
    from draftly.integrations.tavily.errors import TavilyError, TavilyErrorCode

    config = getattr(context, "config", None)
    budget = getattr(config, "tavily_credit_budget", None)
    if budget is not None and budget <= 0:
        raise RuntimeError(f"tavily credit budget exceeded (0 >= {budget})")
    sample = _stratified_sample(docs)
    items = [
        (
            (doc.get("metadata") or {}).get("title") or "Untitled",
            doc.get("content", ""),
        )
        for doc in sample
        if doc.get("content", "").strip()
    ]
    if not items:
        return None
    client = _tavily_client_from_config(config)
    try:
        created = await client.research(
            query=_EVAL_RESEARCH_QUERY,
            model="mini",
            files=pack_sample_files(items),
            output_schema=EvaluationScores.model_json_schema(),
        )
        polled = await client.research_poll(
            created.request_id,
            poll_timeout_seconds=getattr(
                config, "tavily_research_poll_timeout_seconds", 300
            ),
        )
    except TavilyError as exc:
        if exc.code == TavilyErrorCode.CREDIT_LIMIT:
            raise
        logger.warning("evaluation_research_failed err=%s", exc)
        return None
    finally:
        await client.aclose()
    try:
        scores = EvaluationScores.model_validate(polled.content)
    except ValidationError as exc:
        logger.warning("evaluation_research_invalid_output err=%s", exc)
        return None
    if publish:
        await publish("tool_progress", {
            "name": "initial_evaluation",
            "processed": 1,
            "total": 1,
        })
        await publish("stage_progress", {
            "stage": "initial_evaluation",
            "progress": 95,
        })
    logger.info(
        "initial_evaluation_researched org=%s sample=%d total=%d",
        org_id, len(sample), len(docs),
    )
    return {
        "coverage": float(scores.coverage),
        "completeness": float(scores.completeness),
        "structure": float(scores.structure),
        "length": float(scores.length),
    }


async def run_initial_evaluation(
    context: Any,
    *,
    org_id: str,
    publish: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    source_type: str = "github_repository",
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

    if _research_synthesis_enabled(context) and source_type == "public_documentation":
        researched = await _research_eval_scores(
            context, org_id=org_id, docs=docs, publish=publish
        )
        if researched is not None:
            llm_scores = researched
            llm_count = 1
    elif stage_model is not None:
        # Task 7: bounded-concurrency, timeout-guarded LLM evaluation. Same fix
        # as extraction: a shared Strands Agent can't run concurrently, so use a
        # pool of LLM_MAX_CONCURRENCY agents (one per concurrent slot).
        try:
            agent_pool = _agent_pool(
                stage_model,
                EvaluationScores,
                LLM_MAX_CONCURRENCY,
                role=AgentRole.REVIEWER,
                system_prompt=EVALUATION_SYSTEM_PROMPT,
                agent_id_prefix="onboarding-evaluation",
                node_id="onboarding-evaluation",
            )
        except Exception:
            agent_pool = None
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
                    agent = None
                    if agent_pool is not None:
                        agent = await agent_pool.get()
                    try:
                        scores = await _llm_with_chunk_timeout(
                            _llm_generate(
                                stage_model, prompt, agent=agent,
                                output_model=EvaluationScores,
                                telemetry=recorder.record,
                            ),
                        )
                        return scores
                    finally:
                        if agent is not None:
                            agent_pool.put_nowait(agent)
            except Exception as exc:
                logger.warning("evaluation_llm_chunk_failed doc=%s err=%s", title, exc)
                return None

        # Task 8: sample the LLM pass — heuristics above already scanned the
        # full corpus, so only EVAL_LLM_SAMPLE_SIZE docs get an LLM call.
        eval_docs = _sample_docs(docs)
        eval_total = len(eval_docs)
        # Corpus-scaled emit cadence: replace the fixed "every 10 docs" tick
        # with a budget-bounded interval so the stream stays small for large
        # samples yet stays animated for tiny ones (interval == 1).
        emit_every = tick_interval(eval_total)
        eval_results = await asyncio.gather(
            *(_evaluate(doc) for doc in eval_docs)
        )

        for i, scores in enumerate(eval_results):
            if scores:
                for dim in llm_scores:
                    llm_scores[dim] += getattr(scores, dim)
                llm_count += 1

            processed = i + 1
            if publish and (processed == eval_total or processed % emit_every == 0):
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
    runtime: SteeringRuntime | None = None,
    agent_id: str | None = None,
    node_id: str | None = None,
    source_type: str = "github_repository",
    org_id: str = "",
) -> list[Recommendation]:
    """Stage 5: Generate prioritized recommendations via LLM."""
    if _research_synthesis_enabled(context) and source_type == "public_documentation":
        return await _run_research_recommendations(
            context,
            eval_result=eval_result,
            health_result=health_result,
            document_count=document_count,
            chunk_count=chunk_count,
            org_id=org_id,
        )
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
            agent = build_draftly_agent(
                role=AgentRole.RECOMMENDER,
                system_prompt=RECOMMENDER_SYSTEM_PROMPT,
                model=stage_model,
                structured_output_model=RecommendationList,
                runtime=runtime or SteeringRuntime.disabled(),
                agent_id=agent_id or "onboarding-recommendation",
                node_id=node_id or "onboarding-recommendation",
            )
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


_RECOMMENDATION_RESEARCH_QUERY = (
    "Recommend 3-5 prioritized documentation improvements based on the "
    "attached quality metrics and documentation gaps."
)


async def _collect_gap_evidence(context: Any, org_id: str) -> list[str]:
    """Probe coverage topics through the index; return low-confidence ones.

    Never raises: probe failures degrade to no gap evidence rather than
    invalidating the recommendations.
    """
    try:
        from draftly.documentation.rag_retrieval import RagRetrieval
        from draftly.integrations.database.client import DatabaseClient
        from draftly.memory.embeddings import EmbeddingService

        retrieval = RagRetrieval(
            db=DatabaseClient(), embeddings=EmbeddingService()
        )
        gaps: list[str] = []
        for topic in sorted(EXPECTED_TOPICS):
            try:
                probed = await retrieval.retrieve(
                    org_id=org_id, query=topic, limit=3
                )
            except Exception as exc:
                logger.warning(
                    "recommendations_gap_probe_failed topic=%s err=%s",
                    topic, exc,
                )
                continue
            if probed.confidence < 0.60:
                gaps.append(topic)
        return gaps
    except Exception as exc:
        logger.warning("recommendations_gap_probe_failed err=%s", exc)
        return []


async def _run_research_recommendations(
    context: Any,
    *,
    eval_result: EvaluationResult,
    health_result: HealthResult,
    document_count: int,
    chunk_count: int,
    org_id: str = "",
) -> list[Recommendation]:
    """Stage 5 (research path, public sources only).

    Inputs plus gap evidence go through one Research call with
    ``output_schema=RecommendationList``. Failure or invalid output
    degrades to ``[]`` and never invalidates a completed analysis.
    """
    from pydantic import ValidationError

    from draftly.documentation.tavily_source import pack_sample_files
    from draftly.integrations.tavily.errors import TavilyError, TavilyErrorCode

    config = getattr(context, "config", None)
    budget = getattr(config, "tavily_credit_budget", None)
    if budget is not None and budget <= 0:
        raise RuntimeError(f"tavily credit budget exceeded (0 >= {budget})")
    gaps = await _collect_gap_evidence(context, org_id)
    summary = (
        f"# Documentation quality inputs\n\n"
        f"Health score: {health_result.score:.2f}\n"
        f"Documents: {document_count}\n"
        f"Chunks: {chunk_count}\n"
        f"Coverage: {eval_result.dimensions.get('coverage', 0.0):.2f}\n"
        f"Completeness: {eval_result.dimensions.get('completeness', 0.0):.2f}\n"
        f"Structure: {eval_result.dimensions.get('structure', 0.0):.2f}\n"
        f"Length: {eval_result.dimensions.get('length', 0.0):.2f}\n\n"
        f"# Documentation gaps (low index-retrieval confidence)\n\n"
        + ("\n".join(f"- {topic}" for topic in gaps) if gaps else "- none")
        + "\n"
    )
    client = _tavily_client_from_config(config)
    try:
        created = await client.research(
            query=_RECOMMENDATION_RESEARCH_QUERY,
            model="mini",
            files=pack_sample_files([("stage5-inputs", summary)]),
            output_schema=RecommendationList.model_json_schema(),
        )
        polled = await client.research_poll(
            created.request_id,
            poll_timeout_seconds=getattr(
                config, "tavily_research_poll_timeout_seconds", 300
            ),
        )
    except TavilyError as exc:
        if exc.code == TavilyErrorCode.CREDIT_LIMIT:
            raise
        logger.warning("recommendations_research_failed err=%s", exc)
        return []
    finally:
        await client.aclose()
    try:
        parsed = RecommendationList.model_validate(polled.content)
    except ValidationError as exc:
        logger.warning("recommendations_research_invalid_output err=%s", exc)
        return []
    if not parsed.items:
        return []
    logger.info(
        "recommendations_researched count=%d gaps=%d",
        len(parsed.items), len(gaps),
    )
    return list(parsed.items)[:5]
