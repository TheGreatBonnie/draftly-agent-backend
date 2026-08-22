"""Support answer validation (plan §8.7) — generate/validate answers."""

from __future__ import annotations

import re
from typing import Any

from draftly.support.models import SupportAnswer

CITATION_PATTERN = re.compile(r"\[([^\]]+)\]|\bhttps?://\S+")


class SupportAnswerValidator:
    """Deterministic answer checks shared with the graph gate."""

    MIN_LENGTH = 40
    MAX_LENGTH = 4000

    def citations(self, content: str) -> list[str]:
        return [m.group(0) for m in CITATION_PATTERN.finditer(content)]

    def validate(
        self,
        *,
        question_id: str,
        content: str,
        evidence: list[dict[str, Any]] | None = None,
    ) -> SupportAnswer:
        """Score an answer; grounded requires ≥1 evidence citation."""
        evidence = evidence or []
        found = self.citations(content)
        cited_ids = [e.get("id") for e in evidence if e.get("id") and e.get("id") in content]
        grounded = bool(cited_ids) or bool(found)

        length_ok = self.MIN_LENGTH <= len(content) <= self.MAX_LENGTH
        confidence = 0.5 * min(len(cited_ids) or len(found), 2) / 2.0
        if length_ok:
            confidence += 0.3
        if not content.strip():
            confidence = 0.0

        return SupportAnswer(
            answer_id=f"answer-{question_id}",
            question_id=question_id,
            content=content,
            confidence=round(min(confidence, 1.0), 2),
            citations=cited_ids or found,
            grounded=grounded,
            metadata={"length_ok": length_ok},
        )
