"""Async memory curation (spec 2026-08-23 §Components 4).

Scheduled post-run job: claims candidate batches, asks the curator agent for
structured decisions, applies them deterministically, and records outcomes.
Failures leave candidates pending for retry — no data loss.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from draftly.agents.shared.memory_curator import build_memory_curator
from draftly.integrations.strands.models import RoleAwareModelResolver

logger = structlog.get_logger(__name__)

BATCH_SIZE = 10


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of (possibly fenced) agent output."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else text
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def run_memory_curation(context: Any) -> dict[str, int]:
    """Claim a candidate batch and apply the curator's decisions."""
    candidates = context.candidates
    claimed = await candidates.claim_batch(limit=BATCH_SIZE)
    if not claimed:
        return {"claimed": 0}

    prompt = (
        "Curate these memory candidates:\n"
        + "\n".join(json.dumps(c, default=str) for c in claimed)
    )
    from draftly.app.composition.tools import _MEMORY_CURATOR_TOOLS

    # Resolve routed curator model (offline degrades to None)
    resolver = getattr(context, "model", None)
    model = (
        resolver.for_role("memory_curator")
        if isinstance(resolver, RoleAwareModelResolver)
        else resolver
    )
    if model is None:
        # Offline: no routed model → release the claim for retry, exactly
        # like the "curator returned nothing usable" fallback below.
        for record in claimed:
            await candidates.set_status_pending(
                str(record["id"]), "offline: no routed curator model"
            )
        return {"claimed": 0}

    agent = build_memory_curator(model=model, tools=_MEMORY_CURATOR_TOOLS)
    try:
        result = await agent.invoke_async(prompt)
        payload = _extract_json(str(result))
    except Exception:
        logger.exception("curator_invoke_failed")
        payload = None

    applied = 0
    decisions = payload.get("decisions") if isinstance(payload, dict) else None
    if isinstance(decisions, list) and decisions:
        for i, decision in enumerate(decisions[: len(claimed)]):
            record = claimed[i]
            try:
                ok = await _apply_decision(decision, record, context)
            except Exception:
                logger.exception("decision_apply_failed")
                ok = False
            reason = str(decision.get("reason") or decision.get("action") or "")
            if ok:
                await candidates.mark_applied(str(record["id"]), reason)
                applied += 1
            else:
                await candidates.mark_rejected(
                    str(record["id"]), reason or "apply failed"
                )
    else:
        # Curator produced nothing usable: release the batch for retry.
        for record in claimed:
            await candidates.set_status_pending(
                str(record["id"]), "curator returned no usable decisions"
            )

    logger.info("memory_curation_run claimed=%d applied=%d", len(claimed), applied)
    return {"claimed": len(claimed), "applied": applied}


async def _apply_decision(decision: dict, record: dict, context: Any) -> bool:
    """Apply one curator decision deterministically against memory services."""
    action = str(decision.get("action", "")).upper()
    content = decision.get("content")
    target = decision.get("target_memory_id")

    if action == "CREATE" and content:
        from draftly.memory.models.base import MemoryItem
        from draftly.memory.service import MemoryService

        await MemoryService().remember(
            MemoryItem(
                namespace="knowledge",
                content=str(content),
                org_id=record.get("org_id"),
                confidence=float(record.get("confidence") or 0.5),
                metadata={"candidate_id": record.get("id")},
            )
        )
        return True

    if action == "SUPERSEDE" and target and content:
        from draftly.memory.service import MemoryService

        result = await MemoryService().supersede(
            str(target),
            str(content),
            namespace="knowledge",
            org_id=record.get("org_id"),
        )
        return result is not None

    if action == "MERGE" and target:
        from draftly.memory.service import MemoryService

        merged = await MemoryService().consolidate(
            namespace="knowledge",
            query=str(content or record.get("payload")),
            merge_target_id=str(target),
        )
        return merged is not None

    if action == "ARCHIVE" and target:
        from draftly.memory.service import MemoryService

        return bool(
            await MemoryService().repository.set_status(
                memory_id=str(target), status="archived"
            )
        )

    if action == "UPDATE" and target and content:
        from draftly.memory.service import MemoryService

        updated = await MemoryService().repository.update(str(target), content=str(content))
        return updated is not None

    if action == "REJECT":
        return True  # rejection itself is the outcome

    return False
