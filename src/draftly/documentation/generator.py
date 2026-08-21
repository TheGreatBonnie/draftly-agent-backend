"""Documentation generator (plan §8.4) — create new docs."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any


class DocumentationGenerator:
    """Assemble new documentation pages from structured inputs.

    Deterministic scaffolding only: LLM drafting happens in the graph
    (§6); this service turns an approved plan into a valid page.
    """

    SLUG_PATTERN = re.compile(r"[^a-z0-9]+")

    @staticmethod
    def slugify(title: str) -> str:
        slug = DocumentationGenerator.SLUG_PATTERN.sub("-", title.lower().strip())
        return slug.strip("-") or "untitled"

    def generate(
        self,
        *,
        title: str,
        sections: list[dict[str, str]],
        front_matter: dict[str, Any] | None = None,
    ) -> str:
        """Render a markdown page with front matter and sections."""
        meta = {
            "title": title,
            "generated_at": datetime.now(UTC).isoformat(),
            **(front_matter or {}),
        }
        lines = ["---"]
        for key, value in meta.items():
            lines.append(f"{key}: {value}")
        lines.extend(["---", "", f"# {title}", ""])

        for section in sections:
            heading = section.get("heading", "")
            body = section.get("body", "")
            if heading:
                lines.extend([f"## {heading}", ""])
            if body:
                lines.extend([body, ""])

        return "\n".join(lines).rstrip() + "\n"

    def build_path(self, *, repository: str, title: str) -> str:
        return f"docs/{self.slugify(title)}.md"
