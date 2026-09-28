"""``ImpactAnalysis`` must fit in one response.

Run ``9ab7a0a0``: the ``impact`` node died on ``max_tokens`` with the
``impact_analysis`` tool call cut off mid-JSON --
``tool_name=<impact_analysis> | replacing with error message due to max_tokens
truncation`` -- and strands' recovery *discards* the partial message, so the
node produced nothing at all.

The cause is that ``ImpactAnalysis`` is the only model-authored payload with no
ceilings at all (``EvidenceItem`` got them in the ``ce8ea540`` round):

======================  ===================  =========================
field                   cap today            multiplier
======================  ===================  =========================
``tasks``                unbounded count      x per-task evidence
``affected_documents``   unbounded count      x unbounded string
``evidence``             unbounded count      x unbounded string
``rationale``            unbounded string     -
======================  ===================  =========================

A model that enumerates the repository instead of planning a change produces a
tool call no ``max_tokens`` can hold, and the provider's default cap is the only
thing standing between that and a dead node.

The ceilings live in the schema, not in a prompt, so they reach the model as
``maxLength``/``maxItems`` in the generated tool definition and it can budget
against them -- the same reasoning as ``EVIDENCE_*_MAX_CHARS``.
"""

from __future__ import annotations

import pytest

from draftly.agents.schemas import IMPACT_DOCUMENT_MAX_CHARS, ImpactAnalysis

#: A real single-PR documentation plan touches a few pages, not a repository.
#: Generous on purpose: a rejected plan costs a whole node, so the cap has to
#: clear any legitimate plan by a wide margin and only catch the runaway case.
REALISTIC_TASKS = 12
REALISTIC_AFFECTED_DOCUMENTS = 25


def _plan(**overrides: object) -> ImpactAnalysis:
    payload: dict[str, object] = {
        "action": "update",
        "affected_documents": ["docs/how-to/oauth-login.md"],
        "rationale": "The PR adds an authorization-code flow.",
        "evidence": ["docs/how-to/oauth-login.md#oauth-callback"],
        "tasks": [
            {
                "id": "t1",
                "path": "docs/how-to/oauth-login.md",
                "action": "update",
                "reason": "New callback section.",
                "requirements": ["Document the code exchange."],
            }
        ],
    }
    payload.update(overrides)
    return ImpactAnalysis.model_validate(payload)


# --- a realistic plan is never rejected -----------------------------------


def test_a_realistic_plan_validates() -> None:
    plan = _plan(
        affected_documents=[f"docs/page-{i}.md" for i in range(REALISTIC_AFFECTED_DOCUMENTS)],
        tasks=[
            {"id": f"t{i}", "path": f"docs/page-{i}.md", "action": "update"}
            for i in range(REALISTIC_TASKS)
        ],
    )

    assert len(plan.tasks) == REALISTIC_TASKS
    assert len(plan.affected_documents) == REALISTIC_AFFECTED_DOCUMENTS


def test_every_field_is_optional_as_before() -> None:
    """The caps must not turn omitted fields into required ones."""
    plan = ImpactAnalysis.model_validate({"action": "none"})

    assert plan.tasks == []
    assert plan.evidence == []
    assert plan.rationale == ""


# --- the ceilings reach the model as schema constraints -------------------


def test_the_caps_appear_in_the_generated_tool_schema() -> None:
    """A ceiling the model cannot see is a ceiling it cannot budget against."""
    schema = ImpactAnalysis.model_json_schema()
    tasks = schema["properties"]["tasks"]
    rationale = schema["properties"]["rationale"]

    assert "maxItems" in tasks
    assert "maxLength" in rationale


# --- the runaway cases are rejected ---------------------------------------


def test_a_repository_sized_task_list_is_rejected() -> None:
    """The multiplier that actually truncated the node."""
    with pytest.raises(ValueError):
        _plan(tasks=[{"id": f"t{i}", "path": f"docs/p{i}.md"} for i in range(500)])


def test_an_unbounded_rationale_is_rejected() -> None:
    with pytest.raises(ValueError):
        _plan(rationale="x" * 50_000)


def test_an_unbounded_document_list_is_rejected() -> None:
    with pytest.raises(ValueError):
        _plan(affected_documents=[f"docs/p{i}.md" for i in range(2_000)])


def test_pasted_file_contents_in_rationale_are_rejected() -> None:
    """The realistic failure: the model pastes a file instead of summarising."""
    with pytest.raises(ValueError):
        _plan(rationale="def handler():\n    return oauth_exchange(code)\n" * 2_000)


# --- a value exactly at the cap is accepted -------------------------------


def test_a_rationale_exactly_at_the_cap_is_accepted() -> None:
    """Off-by-one guard: a cap that rejects its own boundary is a typo, not a limit."""
    cap = ImpactAnalysis.model_fields["rationale"].metadata[0].max_length

    assert len(_plan(rationale="x" * cap).rationale) == cap


def test_a_task_list_exactly_at_the_cap_is_accepted() -> None:
    cap = ImpactAnalysis.model_fields["tasks"].metadata[0].max_length

    assert len(_plan(tasks=[{"id": f"t{i}", "path": f"d{i}.md"} for i in range(cap)]).tasks) == cap


def test_a_pasted_blob_as_a_document_reference_is_rejected() -> None:
    """``max_length`` on a list bounds the count, not the items -- both need caps."""
    with pytest.raises(ValueError):
        _plan(affected_documents=["docs/" + "a" * 5_000])


def test_the_item_cap_appears_in_the_generated_tool_schema() -> None:
    schema = ImpactAnalysis.model_json_schema()
    items = schema["properties"]["affected_documents"]["items"]

    assert items["maxLength"] == IMPACT_DOCUMENT_MAX_CHARS
