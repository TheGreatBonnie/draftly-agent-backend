"""Support answer evaluation (plan §7.2).

Scores a delivered support answer against the question with the
deterministic Contains evaluator; LLM-judge scoring lands in §8.2.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def evaluate_support_answer(
    *,
    question: str,
    answer: str,
    expected_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministic keyword-coverage score for a support answer."""
    keywords = expected_keywords or _keywords_from_question(question)
    if not keywords:
        return {"score": 1.0, "passed": True, "missing": []}

    lowered = answer.lower()
    missing = [k for k in keywords if k.lower() not in lowered]
    covered = len(keywords) - len(missing)
    score = covered / len(keywords)

    return {
        "score": round(score, 3),
        "passed": score >= 0.5,
        "missing": missing,
    }


def _keywords_from_question(question: str) -> list[str]:
    stop = {
        "how",
        "do",
        "i",
        "the",
        "a",
        "an",
        "is",
        "are",
        "what",
        "why",
        "when",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "my",
        "can",
        "does",
        "did",
        "it",
        "this",
        "that",
    }
    words = [w.strip("?!.,;:'\"()") for w in question.split()]
    return [w for w in words if w.lower() not in stop and len(w) > 2][:8]
