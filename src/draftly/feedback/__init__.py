"""Draftly feedback subsystem (plan §8.5)."""

from .classifier import FeedbackClassifier
from .deduplication import DeduplicationService
from .gap_detector import GapDetector
from .models import DocumentationGapCandidate, FeedbackCluster, FeedbackItem
from .prioritization import GapPrioritizer
from .service import FeedbackService

__all__ = [
    "DeduplicationService",
    "DocumentationGapCandidate",
    "FeedbackClassifier",
    "FeedbackCluster",
    "FeedbackItem",
    "FeedbackService",
    "GapDetector",
    "GapPrioritizer",
]
