"""GitHub event processors (plan §7.1)."""

from draftly.events.github.issue import IssueProcessor
from draftly.events.github.pull_request import PullRequestProcessor
from draftly.events.github.push import PushProcessor
from draftly.events.github.release import ReleaseProcessor

__all__ = [
    "IssueProcessor",
    "PullRequestProcessor",
    "PushProcessor",
    "ReleaseProcessor",
]
