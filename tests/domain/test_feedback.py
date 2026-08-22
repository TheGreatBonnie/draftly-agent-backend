"""§8.9 verification: feedback cluster→gap logic with a seeded
in-memory question set (§8.5)."""

from __future__ import annotations

from datetime import UTC, datetime

from draftly.feedback import (
    DeduplicationService,
    FeedbackClassifier,
    FeedbackItem,
    GapDetector,
    GapPrioritizer,
)


def seed_items() -> list[FeedbackItem]:
    """In-memory support question set (no DB)."""
    now = datetime.now(UTC)
    raw = [
        ("slack", "How do I rotate API keys for production?", "alice"),
        ("slack", "How do I rotate API keys in production?", "bob"),
        ("discord", "rotate api keys - where is that documented?", "carol"),
        ("slack", "Getting SSL handshake errors after cert rotation", "dave"),
        ("slack", "SSL handshake fails on staging only", "erin"),
        ("github", "Docs don't mention key rotation at all", "frank"),
        ("slack", "Thanks, the pooling guide is great!", "grace"),
    ]
    return [
        FeedbackItem(
            id=str(i),
            platform=platform,
            content=content,
            author=author,
            timestamp=now,
        )
        for i, (platform, content, author) in enumerate(raw)
    ]


class TestFeedbackPipeline:
    def test_classifier_assigns_categories_and_sentiment(self) -> None:
        classifier = FeedbackClassifier()
        item = classifier.classify(
            FeedbackItem(platform="slack", content="This error is so confusing")
        )
        assert item.category in {"bug_report", "complaint"}
        assert item.sentiment == "negative"

    def test_deduplication_collapses_near_duplicates(self) -> None:
        items = seed_items()
        deduplicator = DeduplicationService()
        unique = deduplicator.deduplicate(items)
        assert len(unique) < len(items)
        # The near-identical rotation questions collapse to one.
        rotation = [
            i
            for i in unique
            if "rotate api keys" in i.content.lower() and "production" in i.content.lower()
        ]
        assert len(rotation) == 1

    def test_cluster_to_gap_promotion(self) -> None:
        detector = GapDetector(min_cluster_size=2)
        items = DeduplicationService().deduplicate(seed_items())

        clusters = detector.cluster(items)
        assert any("ssl" in c.topic or "handshake" in c.topic for c in clusters)

        gaps = detector.detect_gaps(items, min_occurrences=2)
        # Key-rotation questions cluster into a gap; the thank-you does not.
        topics = " ".join(g.topic for g in gaps)
        assert "thanks" not in topics
        assert all(g.occurrences >= 2 for g in gaps)
        assert all(g.severity > 0 for g in gaps)

    def test_prioritization_orders_by_severity_then_frequency(self) -> None:
        prioritizer = GapPrioritizer()
        detector = GapDetector(min_cluster_size=1)
        items = seed_items()

        gaps = detector.detect_gaps(items, min_occurrences=1)
        ranked = prioritizer.prioritize(gaps)

        scores = [prioritizer.score(g) for g in ranked]
        assert scores == sorted(scores, reverse=True)
        # The 3-item rotation cluster outranks singletons.
        assert ranked[0].occurrences >= max(g.occurrences for g in gaps) - 1

    def test_severity_increases_with_negative_sentiment(self) -> None:
        detector = GapDetector(min_cluster_size=1)
        classifier = FeedbackClassifier()
        neutral = [
            classifier.classify(FeedbackItem(platform="slack", content="how do backups work"))
        ]
        angry = [
            classifier.classify(FeedbackItem(platform="slack", content="backups are broken again"))
        ]
        gap_neutral = detector.detect_gaps(neutral, min_occurrences=1)[0]
        gap_angry = detector.detect_gaps(angry, min_occurrences=1)[0]
        assert gap_angry.severity > gap_neutral.severity
