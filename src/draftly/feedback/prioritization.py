"""Gap prioritization (plan §8.5) — rank gaps by severity/frequency."""

from __future__ import annotations

from draftly.feedback.models import DocumentationGapCandidate

PLATFORM_BONUS = {
    "slack": 0.05,
    "discord": 0.05,
    "github": 0.1,
}


class GapPrioritizer:
    """Order gap candidates so the highest-impact docs get written first."""

    def __init__(self, *, frequency_weight: float = 0.6) -> None:
        self.frequency_weight = frequency_weight
        self.severity_weight = 1.0 - frequency_weight

    def score(self, candidate: DocumentationGapCandidate) -> float:
        frequency = min(candidate.occurrences / 10.0, 1.0)
        platform_bonus = max(
            (PLATFORM_BONUS.get(p, 0.0) for p in candidate.platforms),
            default=0.0,
        )
        return (
            self.severity_weight * candidate.severity
            + self.frequency_weight * frequency
            + platform_bonus
        )

    def prioritize(
        self,
        candidates: list[DocumentationGapCandidate],
        *,
        limit: int | None = None,
    ) -> list[DocumentationGapCandidate]:
        ranked = sorted(
            candidates, key=self.score, reverse=True
        )
        return ranked[:limit] if limit else ranked
