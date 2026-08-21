"""Documentation event processors (plan §7.1)."""

from draftly.events.documentation.document_changed import DocumentChangedProcessor
from draftly.events.documentation.publish_completed import PublishCompletedProcessor
from draftly.events.documentation.review_completed import ReviewCompletedProcessor

__all__ = [
    "DocumentChangedProcessor",
    "PublishCompletedProcessor",
    "ReviewCompletedProcessor",
]
