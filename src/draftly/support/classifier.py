"""Support classifier (plan §8.7) — triage questions."""

from __future__ import annotations

from draftly.support.models import SupportQuestion

URGENT_KEYWORDS = ("production", "outage", "down", "urgent", "asap", "blocked")
CATEGORY_KEYWORDS = {
    "bug": ("error", "crash", "fails", "broken", "exception"),
    "docs": ("docs", "documentation", "guide", "example"),
    "billing": ("invoice", "billing", "charge", "payment", "plan"),
    "how_to": ("how do", "how to", "how can", "configure", "setup"),
}


class SupportClassifier:
    """Rule-based triage; the graph's analyzer agent refines further."""

    def categorize(self, content: str) -> str:
        lowered = content.lower()
        for category, keywords in CATEGORY_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                return category
        return "general"

    def urgency(self, content: str) -> str:
        lowered = content.lower()
        return (
            "high"
            if any(keyword in lowered for keyword in URGENT_KEYWORDS)
            else "normal"
        )

    def triage(self, question: SupportQuestion) -> SupportQuestion:
        question.category = self.categorize(question.content)
        question.urgency = self.urgency(question.content)
        return question
