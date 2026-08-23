"""Knowledge base updates (plan §7.2).

Turns prioritized gaps into memory items so retrieval improves before
the documentation graph even runs. Phase 5 records the update plan;
the memory write path lands with §8 domain services.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def plan_knowledge_updates(
    gaps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build memory-item upsert plans for each gap."""
    return [
        {
            "kind": "documentation_gap",
            "key": f"gap:{gap.get('topic', 'unknown')}",
            "content": f"Users repeatedly ask about: {gap.get('topic', '')}",
            "count": gap.get("count", 0),
            "action": gap.get("action", "create"),
        }
        for gap in gaps
    ]
