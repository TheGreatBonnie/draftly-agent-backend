"""Onboarding initialization stage functions.

Stages 2-5 of the onboarding workflow: knowledge extraction, evaluation,
health scoring, and recommendation generation.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from strands import Agent

logger = structlog.get_logger(__name__)

EXTRACTION_PROMPT = """\
Analyze this documentation chunk and extract structured knowledge.

Return a JSON object with these keys:
- "facts": list of key facts (strings)
- "relationships": list of {{"source": str, "target": str, "type": str}} objects
  where "type" MUST be exactly one of: IMPLEMENTS, DOCUMENTED_BY, AFFECTS, DERIVED_FROM
- "procedures": list of {{"steps": [str], "title": str}} objects

Chunk content:
{content}

Return ONLY valid JSON, no markdown fences."""


VALID_RELATION_TYPES = {"IMPLEMENTS", "DOCUMENTED_BY", "AFFECTS", "DERIVED_FROM"}


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


@dataclass
class Recommendation:
    priority: str = "medium"
    title: str = ""
    detail: str = ""
    category: str = ""


CHUNK_BATCH_SIZE = 50
CHUNK_TIMEOUT_SECONDS = 10


async def _llm_generate(model: Any, prompt: str) -> str:
    """Generate a response from the LLM via a Strands Agent."""
    agent = Agent(model=model)
    result = await agent.invoke_async(prompt)
    return str(result)


def _parse_llm_scores(raw: str) -> dict[str, float] | None:
    """Extract dimension scores from LLM output. Returns None on failure."""
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    text = fenced.group(1) if fenced else raw
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    required = {"coverage", "completeness", "structure", "length"}
    if not required.issubset(parsed.keys()):
        return None
    scores = {}
    for dim in required:
        val = parsed[dim]
        if isinstance(val, (int, float)) and 0.0 <= val <= 1.0:
            scores[dim] = float(val)
        else:
            return None
    return scores

EXPECTED_TOPICS = {
    "readme", "getting started", "installation", "api",
    "usage", "examples", "changelog", "contributing",
}
CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```")
HEADING_PATTERN = re.compile(r"^#{1,3}\s+", re.MULTILINE)
WORD_RANGE = (200, 5000)

EVALUATION_LLM_PROMPT = """\
Evaluate this documentation's quality. Rate each dimension 0.0-1.0.

Documentation title: {title}
Content:
{content}

Dimensions:
- coverage: Does it cover key topics users need?
- completeness: Are explanations thorough with examples?
- structure: Is it well-organized with clear navigation?
- length: Is length appropriate for the content?

Return JSON: {{"coverage": float, "completeness": float, "structure": float, "length": float}}
Return ONLY valid JSON, no markdown fences."""

HEURISTIC_WEIGHT = 0.4
LLM_WEIGHT = 0.6

RECOMMENDATION_PROMPT = """\
You are a documentation quality advisor. Based on the following metrics, generate 3-5 \
prioritized recommendations.

Health Score: {health_score:.2f}/1.0
Document Count: {document_count}
Chunk Count: {chunk_count}

Dimension Scores (0-1):
- Coverage: {coverage:.2f}
- Completeness: {completeness:.2f}
- Structure: {structure:.2f}
- Length: {length:.2f}

Low-scoring dimensions need the most attention.

Return a JSON array of recommendations, each with:
- "priority": "high", "medium", or "low"
- "title": short action title
- "detail": 1-2 sentence explanation
- "category": which dimension this addresses

Return ONLY valid JSON, no markdown fences."""


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
    )

    if not chunks:
        logger.info("knowledge_construction_no_chunks org=%s", org_id)
        return result

    total = len(chunks)
    for i in range(0, total, CHUNK_BATCH_SIZE):
        batch = chunks[i : i + CHUNK_BATCH_SIZE]
        for chunk in batch:
            chunk_id = chunk.get("id", "unknown")
            content = chunk.get("content", "")
            if not content.strip():
                result.failed_chunks.append(chunk_id)
                continue

            try:
                prompt = EXTRACTION_PROMPT.format(content=content[:2000])
                raw = await _llm_generate(context.model, prompt)
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                extracted = json.loads(raw)

                # Store extracted knowledge
                for fact in extracted.get("facts", []):
                    item = Knowledge(
                        namespace="knowledge",
                        content=fact,
                        org_id=org_id,
                        topic=chunk.get("metadata", {}).get("title"),
                        source_quality=0.7,
                    )
                    await context.memory.remember(item)
                    result.knowledge_count += 1

                for rel in extracted.get("relationships", []):
                    relation_type = rel.get("type", "related_to").upper()
                    if relation_type not in VALID_RELATION_TYPES:
                        logger.warning(
                            "invalid_relation_type type=%s chunk=%s",
                            relation_type, chunk_id,
                        )
                        relation_type = "DERIVED_FROM"
                    await context.docgraph.link(
                        source_key=rel.get("source", ""),
                        target_key=rel.get("target", ""),
                        relation_type=relation_type,
                        org_id=org_id,
                    )
                    result.relationship_count += 1

                # Store procedure patterns
                for proc in extracted.get("procedures", []):
                    candidate = MemoryCandidate(
                        org_id=org_id,
                        candidate_type="procedure_pattern",
                        payload=proc,
                        source_type="document_chunk",
                        source_id=chunk_id,
                        evidence=[content[:200]],
                        confidence=0.6,
                    )
                    await context.candidates.enqueue(candidate)
                    result.candidate_count += 1

            except Exception as exc:
                logger.warning("knowledge_extraction_chunk_failed chunk=%s err=%s", chunk_id, exc)
                result.failed_chunks.append(chunk_id)

        processed = min(i + CHUNK_BATCH_SIZE, total)
        await publish("tool_progress", {
            "name": "knowledge_extraction",
            "processed": processed,
            "total": total,
        })

    logger.info(
        "knowledge_construction_done org=%s facts=%d rels=%d procs=%d failed=%d",
        org_id, result.knowledge_count, result.relationship_count,
        result.candidate_count, len(result.failed_chunks),
    )
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
    )

    if not docs:
        return EvaluationResult(score=0.0, dimensions={
            "coverage": 0.0, "completeness": 0.0,
            "structure": 0.0, "length": 0.0,
        })

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

    if context.model is not None:
        for i, doc in enumerate(docs):
            content = doc.get("content", "")
            title = doc.get("metadata", {}).get("title", "Untitled")
            if not content.strip():
                continue
            try:
                prompt = EVALUATION_LLM_PROMPT.format(
                    title=title, content=content[:2000],
                )
                raw = await _llm_generate(context.model, prompt)
                scores = _parse_llm_scores(raw)
                if scores:
                    for dim in llm_scores:
                        llm_scores[dim] += scores[dim]
                    llm_count += 1
            except Exception as exc:
                logger.warning("evaluation_llm_chunk_failed doc=%s err=%s", title, exc)

            if publish and (i + 1) % 10 == 0:
                await publish("tool_progress", {
                    "name": "initial_evaluation",
                    "processed": i + 1,
                    "total": total,
                })

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
        "initial_evaluation_done org=%s score=%.2f llm_docs=%d/%d",
        org_id, score, llm_count, total,
    )
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

    logger.info("health_report_done score=%.2f", health_score)
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

    try:
        raw = await _llm_generate(context.model, prompt)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        items = json.loads(raw)
        return [
            Recommendation(
                priority=item.get("priority", "medium"),
                title=item.get("title", ""),
                detail=item.get("detail", ""),
                category=item.get("category", ""),
            )
            for item in items
            if isinstance(item, dict)
        ]
    except Exception as exc:
        logger.warning("recommendations_generation_failed err=%s", exc)
        return []
