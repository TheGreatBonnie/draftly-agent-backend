"""The page-scoped evidence contract must survive into the tool schema.

The resolver matches EvidenceItem.id against DocumentationTask.path, so the
model has to be told that rule. Field descriptions reach the Bedrock tool spec
through list[...] nesting (verified against
strands.tools.structured_output.structured_output_utils), so this asserts the
rendered schema, not the source text.
"""

from __future__ import annotations

from strands.tools.structured_output.structured_output_utils import (
    convert_pydantic_to_tool_spec,
)

from draftly.agents.schemas import ImpactAnalysis


def _evidence_item_node() -> dict:
    spec = convert_pydantic_to_tool_spec(ImpactAnalysis)
    return spec["inputSchema"]["json"]["properties"]["tasks"]["items"]["properties"][
        "evidence"
    ]


def test_evidence_id_description_reaches_the_tool_schema() -> None:
    node = _evidence_item_node()
    assert "path" in node["items"]["properties"]["id"]["description"].lower()


def test_task_evidence_description_reaches_the_tool_schema() -> None:
    node = _evidence_item_node()
    assert node["description"], "tasks[].evidence must carry a description"


def test_impact_prompt_states_the_id_equals_path_rule() -> None:
    from draftly.agents.prompts import IMPACT_PROMPT

    lowered = IMPACT_PROMPT.lower()
    assert "evidence" in lowered
    assert "path" in lowered
