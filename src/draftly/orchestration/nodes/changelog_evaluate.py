"""Deterministic quality gate for changelog entries."""

from __future__ import annotations

import re
from typing import Any

from strands.multiagent.base import (
    MultiAgentBase,
    MultiAgentResult,
    NodeResult,
    Status,
)

from draftly.orchestration.nodes.base import agent_result, parse_node_input

_VALID_CATEGORIES = {"added", "changed", "deprecated", "removed", "fixed", "security"}
_VERSION_RE = re.compile(r"^## \[[^\]]+\] - \d{4}-\d{2}-\d{2}", re.MULTILINE)
_CATEGORY_RE = re.compile(r"^### (\w+)", re.MULTILINE)
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_BREAKING_RE = re.compile(r"\*\*breaking\*\*", re.IGNORECASE)


def compute_changelog_quality(raw_markdown: str) -> tuple[float, list[str]]:
    """Score a changelog entry on format validity and content quality."""
    if not raw_markdown or not raw_markdown.strip():
        return 0.0, ["Empty changelog entry"]

    reasons: list[str] = []
    score = 0.0

    # Format checks (60%):
    has_version_header = bool(_VERSION_RE.search(raw_markdown))
    if has_version_header:
        score += 0.2
        reasons.append("Valid version header")
    else:
        reasons.append("Missing or malformed version header (expected: ## [X.Y.Z] - YYYY-MM-DD)")

    category_matches = _CATEGORY_RE.findall(raw_markdown)
    valid_categories = [c for c in category_matches if c.lower() in _VALID_CATEGORIES]
    if valid_categories:
        score += 0.2
        reasons.append(f"Valid categories: {', '.join(valid_categories)}")
    else:
        cats = ", ".join(_VALID_CATEGORIES)
        reasons.append(f"No valid categories found (expected one of: {cats})")

    has_iso_date = bool(_ISO_DATE_RE.search(raw_markdown))
    if has_iso_date:
        score += 0.1
        reasons.append("ISO 8601 date present")
    else:
        reasons.append("Missing ISO 8601 date")

    invalid_categories = [c for c in category_matches if c.lower() not in _VALID_CATEGORIES]
    if not invalid_categories:
        score += 0.1
        reasons.append("No invalid categories")
    else:
        reasons.append(f"Invalid categories: {', '.join(invalid_categories)}")

    # Content checks (40%):
    bullet_count = len(re.findall(r"^[-*] ", raw_markdown, re.MULTILINE))
    if bullet_count >= 1:
        score += 0.2
        reasons.append(f"{bullet_count} change item(s)")
    else:
        reasons.append("No change items found")

    has_breaking = bool(_BREAKING_RE.search(raw_markdown))
    if has_breaking:
        score += 0.1
        reasons.append("Breaking changes properly marked")
    elif "changed" in [c.lower() for c in valid_categories]:
        reasons.append("Changed category present but no **Breaking:** marker")

    length_score = min(len(raw_markdown) / 100, 1.0)
    score += length_score * 0.1
    if length_score > 0.5:
        reasons.append("Adequate detail level")

    return score, reasons


class ChangelogEvaluatorNode(MultiAgentBase):
    """Deterministic quality gate for changelog entries."""

    def __init__(
        self,
        name: str = "changelog_evaluate",
        max_iterations: int = 2,
    ) -> None:
        self.name = name
        self.iteration = 0
        self.max_iterations = max_iterations

    async def invoke_async(
        self,
        task: Any,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MultiAgentResult:
        self.iteration += 1

        deps = parse_node_input(task)
        changelog_data = deps.get("changelog", {})
        raw_markdown = changelog_data.get("raw_markdown", "")

        score, reasons = compute_changelog_quality(raw_markdown)
        passed = score >= 0.7

        if not reasons:
            reasons.append(f"Score {score:.2f} (threshold: 0.70)")
        if not passed and self.iteration >= self.max_iterations:
            reasons.append(f"Quality threshold not met after {self.iteration} evaluations")

        return MultiAgentResult(
            status=Status.COMPLETED,
            results={
                self.name: NodeResult(
                    result=agent_result(
                        {
                            "passed": passed,
                            "score": score,
                            "reasons": reasons,
                            "iteration": self.iteration,
                        }
                    )
                )
            },
        )
