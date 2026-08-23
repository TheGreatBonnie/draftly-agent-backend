"""Memory curation workflow tests."""

import json

import pytest

from draftly.memory.candidates.models import MemoryCandidate
from draftly.memory.candidates.service import CandidateService
from tests.fakes.memory_stores import FakeCandidatesStore

DECISIONS = {
    "decisions": [
        {
            "candidate_id": "cand-1",
            "action": "CREATE",
            "target_memory_id": None,
            "content": "Access tokens expire after one hour.",
            "reason": "evidenced",
        },
        {
            "candidate_id": "cand-x",
            "action": "REJECT",
            "target_memory_id": None,
            "content": None,
            "reason": "duplicate",
        },
    ]
}


class StubResult:
    def __init__(self, text):
        self._text = text

    def __str__(self):
        return self._text


class StubAgent:
    def __init__(self, *a, **kw):
        pass

    async def invoke_async(self, prompt):
        return StubResult("```json\n" + json.dumps(DECISIONS) + "\n```")


@pytest.mark.asyncio
async def test_curation_applies_decisions(monkeypatch):
    import draftly.workflows.memory.curation_workflow as cw

    store = FakeCandidatesStore()
    svc = CandidateService(store=store)
    await svc.enqueue(
        MemoryCandidate(
            org_id="org1",
            candidate_type="fact",
            payload={"content": "tokens expire in 1h"},
        )
    )
    await svc.enqueue(
        MemoryCandidate(
            org_id="org1",
            candidate_type="decision",
            payload={"content": "use PKCE"},
        )
    )

    applied_actions = []

    async def fake_apply(decision, candidate, context):
        applied_actions.append(decision["action"])
        return True

    monkeypatch.setattr(
        cw, "build_memory_curator", lambda model, tools=None: StubAgent()
    )
    monkeypatch.setattr(cw, "_apply_decision", fake_apply)

    class FakeContext:
        candidates = svc
        model = object()

    summary = await cw.run_memory_curation(FakeContext())

    assert summary["claimed"] == 2
    assert applied_actions == ["CREATE", "REJECT"]
    assert {r["status"] for r in store.rows} == {"applied"}


@pytest.mark.asyncio
async def test_empty_batch_is_noop():
    import draftly.workflows.memory.curation_workflow as cw

    class FakeContext:
        candidates = CandidateService(store=FakeCandidatesStore())
        model = object()

    assert await cw.run_memory_curation(FakeContext()) == {"claimed": 0}


@pytest.mark.asyncio
async def test_unparseable_output_returns_candidates_to_pending(monkeypatch):
    import draftly.workflows.memory.curation_workflow as cw

    store = FakeCandidatesStore()
    svc = CandidateService(store=store)
    await svc.enqueue(
        MemoryCandidate(org_id="org1", candidate_type="fact", payload={})
    )

    class Garbled:
        def __init__(self, *a, **kw):
            pass

        async def invoke_async(self, prompt):
            return StubResult("I could not decide.")

    monkeypatch.setattr(cw, "build_memory_curator", lambda model, tools=None: Garbled())

    class FakeContext:
        candidates = svc
        model = object()

    summary = await cw.run_memory_curation(FakeContext())
    pending = await svc.store.list_by_status("pending")
    assert summary["claimed"] == 1
    assert len(pending) == 1
