"""Content dataset ground truth.

The expected_output for the grounded release case states the *substance* the
release notes establish (not a description of the authoring task), so the
token-coverage ExpectedContains gate is not structurally capped by preamble
words like "Blog and social variants announce the release".
"""

from __future__ import annotations

import json
from pathlib import Path

from draftly.evaluation.runner import ExpectedContains

CONTENT_DATASET = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "draftly"
    / "evaluation"
    / "datasets"
    / "content.json"
)


def _expected(name: str) -> str:
    data = json.loads(CONTENT_DATASET.read_text())
    for ds in data:
        for c in ds["cases"]:
            if c["name"] == name:
                return c["expected_output"]
    raise AssertionError(f"case {name} not found")


def test_grounded_expected_output_carries_substance_not_preamble() -> None:
    expected = _expected("grounded_release_variants").lower()
    # Must state the actual claims from the release notes, not describe the task.
    for substance in ("api key authentication was removed", "authorization", "rbac", "scoping"):
        assert substance in expected, f"expected_output must mention: {substance}"
    # Must NOT be a task description whose words can never appear in the draft.
    for preamble in ("blog and social variants announce the release", "grounded in the release notes"):
        assert preamble not in expected, f"preamble should be removed: {preamble}"


def test_grounded_expected_output_is_coverage_attainable() -> None:
    """A plausible grounded blog/social output must reach >= 60% token
    coverage, proving the case is passable rather than structurally capped."""
    expected = _expected("grounded_release_variants")
    plausible_draft = (
        "API key authentication was removed. OAuth-based authorization was added; "
        "obtain scoped tokens via the new oauth flow. RBAC with organization "
        "scoping was introduced — migrate by replacing api_key with project_id "
        "and scoped_token."
    )
    expected_tokens = ExpectedContains._significant_tokens(expected)
    actual_tokens = ExpectedContains._significant_tokens(plausible_draft)
    coverage = len(expected_tokens & actual_tokens) / len(expected_tokens)
    assert coverage >= 0.6, f"coverage {coverage:.2f} < 0.6; make the expected output more substance-focused"
