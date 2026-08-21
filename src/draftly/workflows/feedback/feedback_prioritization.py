"""Gap prioritization helpers (plan §7.2).

The feedback graph ranks gaps by frequency; this module re-ranks them by
frequency × severity for the knowledge-update step.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SEVERITY_WEIGHTS = {
    "breaking": 3,
    "error": 3,
    "failure": 3,
    "deprecated": 2,
    "how-to": 1,
    "question": 1,
}


def prioritize_gaps(
    gaps: list[dict[str, Any]],
    *,
    min_count: int = 1,
) -> list[dict[str, Any]]:
    """Sort gaps by (severity weight × count), descending."""
    ranked = []
    for gap in gaps:
        count = int(gap.get("count", 0) or 0)
        if count < min_count:
            continue
        topic = str(gap.get("topic", ""))
        severity = next(
            (w for key, w in SEVERITY_WEIGHTS.items() if key in topic),
            1,
        )
        ranked.append(
            {
                **gap,
                "priority_score": severity * count,
            }
        )
    ranked.sort(key=lambda g: g["priority_score"], reverse=True)
    return ranked
