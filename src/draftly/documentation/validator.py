"""Documentation validator (plan §8.4) — link checking, freshness."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from draftly.documentation.analyzer import DocumentationAnalyzer
from draftly.documentation.models import ValidationResult

LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
STALE_DAYS = 90


class DocumentationValidator:
    """Validate links and freshness of indexed documents."""

    def __init__(
        self,
        analyzer: DocumentationAnalyzer | None = None,
        *,
        stale_after_days: int = STALE_DAYS,
    ) -> None:
        self.analyzer = analyzer or DocumentationAnalyzer()
        self.stale_after_days = stale_after_days

    def check_links(self, content: str, *, known_paths: set[str] | None = None) -> list[str]:
        """Return broken relative links; anchors/URLs are skipped."""
        broken: list[str] = []
        for match in LINK_PATTERN.finditer(content):
            target = match.group(2).strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = target.split("#", 1)[0]
            if known_paths is not None and path not in known_paths:
                broken.append(target)
        return broken

    def check_freshness(self, updated_at: Any) -> int | None:
        """Days since last update; None when unknown."""
        if not updated_at:
            return None
        if isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            except ValueError:
                return None
        return (datetime.now(UTC) - updated_at).days

    def validate(
        self,
        *,
        path: str,
        content: str,
        updated_at: Any = None,
        known_paths: set[str] | None = None,
    ) -> ValidationResult:
        broken = self.check_links(content, known_paths=known_paths)
        stale_days = self.check_freshness(updated_at)
        issues: list[str] = []
        if broken:
            issues.append(f"{len(broken)} broken link(s)")
        if stale_days is not None and stale_days > self.stale_after_days:
            issues.append(f"stale ({stale_days}d since update)")
        return ValidationResult(
            path=path,
            valid=not issues,
            broken_links=broken,
            stale_days=stale_days,
            issues=issues,
        )
