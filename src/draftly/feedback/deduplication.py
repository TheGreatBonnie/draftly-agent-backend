"""Deduplication service (plan §8.5) — merge similar feedback."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from draftly.feedback.models import FeedbackItem


class DeduplicationService:
    """Collapse near-identical feedback items before clustering."""

    def __init__(self, similarity_threshold: float = 0.85) -> None:
        self.similarity_threshold = similarity_threshold

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text.lower().strip())

    def similarity(self, a: str, b: str) -> float:
        return SequenceMatcher(None, self.normalize(a), self.normalize(b)).ratio()

    def deduplicate(self, items: list[FeedbackItem]) -> list[FeedbackItem]:
        """Keep the first of every near-duplicate group."""
        kept: list[FeedbackItem] = []
        for item in items:
            if not any(
                self.similarity(item.content, other.content) >= self.similarity_threshold
                for other in kept
            ):
                kept.append(item)
        return kept
