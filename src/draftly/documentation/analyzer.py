"""Documentation analyzer (plan §8.4) — impact analysis, gap detection."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from draftly.documentation.models import DocumentationGap


class DocumentationAnalyzer:
    """Static analysis over indexed documents."""

    TOPIC_PATTERN = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
    LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    STOPWORDS = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "in",
        "for",
        "on",
        "how",
        "do",
        "i",
        "is",
        "are",
        "with",
        "what",
        "when",
        "my",
    }

    def topics(self, content: str) -> list[str]:
        """Headings define the topic inventory of a document."""
        return [m.group(1).strip() for m in self.TOPIC_PATTERN.finditer(content)]

    def links(self, content: str) -> list[str]:
        return [m.group(2) for m in self.LINK_PATTERN.finditer(content)]

    def keywords(self, text: str, limit: int = 10) -> list[str]:
        words = re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower())
        counts = Counter(w for w in words if w not in self.STOPWORDS)
        return [word for word, _ in counts.most_common(limit)]

    def detect_gaps(
        self,
        questions: list[str],
        documents: list[dict[str, Any]],
        *,
        min_occurrences: int = 1,
    ) -> list[DocumentationGap]:
        """Cluster questions into topics not covered by any document.

        A question is "covered" when its salient keywords appear in some
        document's title/topics/keywords. Uncovered clusters become gaps.
        """
        corpus: list[set[str]] = []
        for doc in documents:
            text = " ".join(
                [
                    str(doc.get("title", "")),
                    str(doc.get("path", "")),
                    " ".join(self.topics(str(doc.get("content", "")))),
                    " ".join(self.keywords(str(doc.get("content", "")), 20)),
                ]
            )
            corpus.append(set(text.lower().split()) | set(self.keywords(text, 20)))

        uncovered: dict[str, list[str]] = {}
        for question in questions:
            keys = set(self.keywords(question, 6))
            if not keys:
                continue
            covered = any(keys & doc_keys for doc_keys in corpus)
            if not covered:
                primary = sorted(keys)[0]
                uncovered.setdefault(primary, []).append(question)

        gaps = [
            DocumentationGap(
                topic=topic,
                occurrences=len(samples),
                severity=min(1.0, 0.2 * len(samples)),
                sample_questions=samples[:5],
            )
            for topic, samples in uncovered.items()
            if len(samples) >= min_occurrences
        ]
        gaps.sort(key=lambda g: (-g.occurrences, g.topic))
        return gaps
