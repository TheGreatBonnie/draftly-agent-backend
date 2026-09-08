"""Single source of truth for the documentation-agent vocabularies.

The PR classification, change-type, urgency, and action vocabularies live in
four places today (schema descriptions, pr-analysis-rules.md,
change-impact-rules.md, documentation-impact.md). These constants are the
canonical lists; tests/unit/agents/test_taxonomy_drift.py pins the prose to
them, and schemas.py builds its descriptions from them.
"""

from __future__ import annotations

SURFACES = ("pull_request", "issue", "support_question")

CHANGE_TYPES = (
    "documentation_only",
    "bug_fix",
    "new_feature",
    "api_change",
    "breaking_change",
    "deprecation",
    "question",
    "other",
)

URGENCY_LEVELS = ("low", "medium", "high")

DOCA_ACTIONS = ("answer", "update", "create", "none")
