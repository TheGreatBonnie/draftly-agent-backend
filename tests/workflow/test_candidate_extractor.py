"""Candidate extractor tests."""

from draftly.workflows.post_run.candidate_extractor import extract_candidates
from draftly.workflows.state import WorkflowState


class FakeResult:
    status = "completed"
    interrupts = ()
    execution_order: list = []


def make_state(event=None):
    return WorkflowState(run_id="run-1", event=event or {})


def test_doc_relations_extracted_from_result_payload():
    state = make_state({"project_id": "org1", "source": "github"})
    state.result = FakeResult()
    object.__setattr__(state.result, "changed_files", ["auth/token_service.py"])
    object.__setattr__(state.result, "docs_touched", ["docs/auth/tokens.md"])

    candidates = extract_candidates(state, "github_pr")
    rels = [c for c in candidates if c.candidate_type == "doc_relation"]
    assert len(rels) == 1
    assert rels[0].payload["source_key"] == "auth/token_service.py"
    assert rels[0].payload["target_key"] == "docs/auth/tokens.md"


def test_facts_extracted_with_evidence():
    state = make_state({"project_id": "org1"})
    state.result = FakeResult()
    object.__setattr__(
        state.result,
        "detected_facts",
        [{"content": "tokens expire in 1h", "evidence": ["a.py"], "confidence": 0.9}],
    )
    candidates = extract_candidates(state, "github_pr")
    facts = [c for c in candidates if c.candidate_type == "fact"]
    assert len(facts) == 1
    assert facts[0].evidence == ["a.py"]
    assert facts[0].confidence == 0.9


def test_no_result_means_no_candidates():
    state = make_state({})
    assert extract_candidates(state, "slack_support") == []
