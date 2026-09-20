"""Deterministic page-type classification for doc retrieval.

Shared by ingestion (chunk metadata) and retrieval (page-type priority).
Pure functions — no I/O, no network.
"""

from __future__ import annotations

PAGE_TYPES = ("tutorial", "how-to", "reference", "explanation", "index")

_TOKENS: dict[str, tuple[str, ...]] = {
    "tutorial": (
        "tutorial",
        "tutorials",
        "getting-started",
        "getting started",
        "quickstart",
        "quick-start",
    ),
    "how-to": ("how-to", "howto", "how to", "guide", "guides", "deploy", "deploying"),
    "reference": ("reference", "ref", "api", "spec", "sdk", "syntax", "cli"),
    "explanation": (
        "concept",
        "concepts",
        "explanation",
        "background",
        "overview",
        "learn",
        "why",
    ),
}


def derive_page_type(path: str) -> str:
    """Classify a doc path/URL into one of PAGE_TYPES (default "index")."""
    lowered = path.lower()
    for page_type in ("tutorial", "how-to", "reference", "explanation"):
        if any(token in lowered for token in _TOKENS[page_type]):
            return page_type
    return "index"


QUESTION_TYPE_PAGE_TYPES: dict[str, tuple[str, ...]] = {
    "signatures": ("reference",),
    "parameters": ("reference",),
    "procedures": ("how-to", "tutorial"),
    "concepts": ("explanation",),
}


def map_question_type(question_type: str) -> tuple[str, ...]:
    """Map a retrieval question_type to prioritized page types.

    Unknown (or "general") question types impose no priority: every page
    type is eligible.
    """
    return QUESTION_TYPE_PAGE_TYPES.get(question_type, PAGE_TYPES)
