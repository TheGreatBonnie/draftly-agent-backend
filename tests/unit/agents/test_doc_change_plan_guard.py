"""DocChangePlan hardening — truncated JSON rejection + metadata-only plans.

Reproduces the `failed to parse tool input json, defaulting to empty dict`
failure: the writer used to emit full markdown files inline, and a payload over
the streaming budget was cut mid-string. Content now lives in the draft store;
the plan is metadata-only and the schema validator rejects inline content.
"""

from __future__ import annotations

import json

import pytest

from draftly.agents.schemas import DocChangePlan


def test_truncated_plan_json_is_rejected_not_silenced() -> None:
    from draftly.agents.documentation.plan_guard import parse_plan_json_strict

    truncated = (
        '{"repository": "TheGreatBonnie/authly", '
        '"branch": "docs/oauth-login-update", '
        '"files": [{"path": "docs/explanation/faq.md", '
        '"content": "# FAQ\\n\\nUnclosed string...'
    )
    with pytest.raises(ValueError, match="truncated|invalid"):
        parse_plan_json_strict(truncated)


def test_empty_files_plan_is_rejected() -> None:
    from draftly.agents.documentation.plan_guard import validate_plan_dict

    with pytest.raises(ValueError, match="at least one file"):
        validate_plan_dict({"repository": "r", "branch": "b", "files": []})


def test_file_entry_without_path_is_rejected() -> None:
    from draftly.agents.documentation.plan_guard import validate_plan_dict

    with pytest.raises(ValueError, match="path"):
        validate_plan_dict({"files": [{"action": "update"}]})


def test_metadata_only_plan_round_trips() -> None:
    from draftly.agents.documentation.plan_guard import parse_plan_json_strict

    payload = {
        "repository": "r",
        "branch": "b",
        "files": [{"path": "docs/a.md", "action": "update"}],
        "commit_message": "docs: update",
        "summary": "s",
    }
    parsed = parse_plan_json_strict(json.dumps(payload))
    assert parsed["files"][0] == {"path": "docs/a.md", "action": "update"}


def test_multiple_files_are_allowed() -> None:
    from draftly.agents.documentation.plan_guard import validate_plan_dict

    files = [
        {"path": f"docs/doc-{i}.md", "action": "update"}
        for i in range(10)
    ]
    validate_plan_dict({"files": files})


def test_schema_rejects_inline_content() -> None:
    with pytest.raises(ValueError, match="content"):
        DocChangePlan(
            repository="r",
            branch="b",
            files=[{"path": "docs/a.md", "action": "update", "content": "full markdown"}],
        )


def test_schema_accepts_metadata_only() -> None:
    plan = DocChangePlan(
        repository="r",
        branch="b",
        files=[{"path": "docs/a.md", "action": "update"}],
    )
    assert plan.files == [{"path": "docs/a.md", "action": "update"}]


def test_schema_rejects_entry_without_path() -> None:
    with pytest.raises(ValueError, match="path"):
        DocChangePlan(repository="r", files=[{"action": "update"}])
