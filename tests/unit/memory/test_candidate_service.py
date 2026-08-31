"""CandidateService tests."""

import contextlib
from unittest.mock import AsyncMock

import pytest

from draftly.integrations.database.memory_candidates_store import MemoryCandidatesStore
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


class _FakeClient:
    def __init__(self):
        self.conn_ops = []

    def transaction(self):
        @contextlib.asynccontextmanager
        async def _tx():
            conn = AsyncMock()
            yield conn

        return _tx()

    async def fetch_one_conn(self, conn, query, *args):
        self.conn_ops.append(("fetch", args[0]))
        return {"id": args[0], "org_id": args[1], "candidate_type": args[2]}


@pytest.mark.asyncio
async def test_candidates_store_insert_batch():
    client = _FakeClient()
    store = MemoryCandidatesStore(client=client)
    rows = await store.insert_batch(fields_list=[
        {"org_id": "o", "candidate_type": "procedure_pattern", "payload": "{}",
         "source_type": "doc", "source_id": "c1", "evidence": "[]", "confidence": 0.6},
        {"org_id": "o", "candidate_type": "procedure_pattern", "payload": "{}",
         "source_type": "doc", "source_id": "c2", "evidence": "[]", "confidence": 0.6},
    ])
    assert len(rows) == 2
    assert client.conn_ops[0][0] == "fetch"


@pytest.mark.asyncio
async def test_candidate_service_enqueue_batch():
    class FakeCandidateStore:
        def __init__(self):
            self.inserted = []

        async def insert(self, *, fields):
            self.inserted.append(fields)
            return {"id": "x"}

        async def insert_batch(self, *, fields_list):
            self.inserted.extend(fields_list)
            return fields_list

    svc = CandidateService(store=FakeCandidateStore())
    cands = [
        MemoryCandidate(
            org_id="o", candidate_type="procedure_pattern", payload={"title": "t"},
            source_type="doc", source_id="c1", evidence=["e"], confidence=0.6,
        )
    ]
    count = await svc.enqueue_batch(cands)
    assert count == 1
