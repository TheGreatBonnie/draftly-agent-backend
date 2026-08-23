"""Candidate outbox: workflows propose, the curator disposes."""

from draftly.memory.candidates.models import MemoryCandidate
from draftly.memory.candidates.service import CandidateService

__all__ = ["CandidateService", "MemoryCandidate"]
