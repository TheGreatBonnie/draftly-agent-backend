"""Vocabulary single-sourcing: skills and schemas cannot drift (F6)."""

from __future__ import annotations

from pathlib import Path

from draftly.agents.taxonomy import CHANGE_TYPES, DOCA_ACTIONS, SURFACES

SKILLS_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "draftly"
    / "skills"
)


def test_pr_analysis_rules_table_covers_every_change_type() -> None:
    rules = (
        SKILLS_DIR
        / "github-pr-analysis"
        / "references"
        / "pr-analysis-rules.md"
    ).read_text()

    for change_type in CHANGE_TYPES:
        assert f"`{change_type}`" in rules, f"missing change type row: {change_type}"


def test_documentation_impact_rules_covers_every_action() -> None:
    rules = (
        SKILLS_DIR
        / "github-pr-analysis"
        / "references"
        / "documentation-impact.md"
    ).read_text()

    for action in DOCA_ACTIONS:
        assert f"`{action}`" in rules, f"missing action row: {action}"


def test_schema_surfaces_are_not_enumerated_twice() -> None:
    from draftly.agents.schemas import EventClassification

    description = EventClassification.model_fields["surface"].description or ""
    for surface in SURFACES:
        assert f'"{surface}"' in description
