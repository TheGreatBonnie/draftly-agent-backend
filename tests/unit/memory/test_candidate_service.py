"""CandidateService tests."""

import pytest

from draftly.memory.candidates.models import MemoryCandidate
from draftly.memory.candidates.service import CandidateService
from tests.fakes.memory_stores import FakeCandidatesStore


def make_candidate(**kw) -> MemoryCandidate:
    defaults = dict(
        candidate_type="fact",
        payload={"content": "tokens expire in 1h"},
        org_id="org1",
        source_type="github_pr",
        source_id="482",
        evidence=["auth/token_service.py"],
    )
    defaults.update(kw)
    return MemoryCandidate(**defaults)


@pytest.mark.asyncio
async def test_enqueue_then_claim_marks_processing():
    svc = CandidateService(store=FakeCandidatesStore())
    await svc.enqueue(make_candidate())
    claimed = await svc.claim_batch(limit=10)
    assert len(claimed) == 1 and claimed[0]["status"] == "processing"


@pytest.mark.asyncio
async def test_claim_is_atomic_no_double_claim():
    svc = CandidateService(store=FakeCandidatesStore())
    await svc.enqueue(make_candidate())
    first = await svc.claim_batch(limit=10)
    second = await svc.claim_batch(limit=10)
    assert len(first) == 1 and second == []


@pytest.mark.asyncio
async def test_mark_applied_and_rejected_record_reasons():
    store = FakeCandidatesStore()
    svc = CandidateService(store=store)
    a = await svc.enqueue(make_candidate())
    r = await svc.enqueue(make_candidate(candidate_type="decision"))
    await svc.claim_batch(limit=10)
    await svc.mark_applied(a["id"], reason="new fact")
    await svc.mark_rejected(r["id"], reason="duplicate")
    assert {c["status"]: c["decision_reason"] for c in store.rows} == {
        "applied": "new fact",
        "rejected": "duplicate",
    }
