"""Feedback classifier (plan §8.5) — categorize feedback signals."""

from __future__ import annotations

import re

from draftly.feedback.models import FeedbackItem

CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("bug_report", ("error", "crash", "fails", "broken", "exception", "traceback")),
    ("how_to", ("how do", "how to", "how can", "is there a way", "where do")),
    ("docs_gap", ("not documented", "no docs", "missing docs", "can't find", "cannot find")),
    ("feature_request", ("would be nice", "feature request", "please add", "wish")),
    ("complaint", ("frustrating", "annoying", "confusing", "terrible", "hate")),
]

SENTIMENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("negative", ("error", "broken", "fail", "confusing", "frustrating", "hate", "terrible")),
    ("positive", ("thanks", "great", "love", "awesome", "works")),
]


class FeedbackClassifier:
    """Rule-based categorization; LLM refinement happens in the graph."""

    def categorize(self, content: str) -> str:
        lowered = content.lower()
        for category, keywords in CATEGORY_RULES:
            if any(keyword in lowered for keyword in keywords):
                return category
        return "question"

    def sentiment(self, content: str) -> str:
        lowered = content.lower()
        if any(keyword in lowered for keyword in SENTIMENT_RULES[0][1]):
            return "negative"
        if any(keyword in lowered for keyword in SENTIMENT_RULES[1][1]):
            return "positive"
        return "neutral"

    def topic_key(self, content: str, max_words: int = 3) -> str:
        """Cheap clustering key: first significant words."""
        words = re.findall(r"[a-z][a-z0-9_-]{2,}", content.lower())
        return "-".join(words[:max_words]) or "misc"

    def classify(self, item: FeedbackItem) -> FeedbackItem:
        item.category = self.categorize(item.content)
        item.sentiment = self.sentiment(item.content)
        item.topic = item.topic or self.topic_key(item.content)
        return item
