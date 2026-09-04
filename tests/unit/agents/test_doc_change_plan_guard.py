"""RED: DocChangePlan hardening — truncated JSON and oversized payloads.

Reproduces the `failed to parse tool input json, defaulting to empty dict`
failure seen when the writer emits a full markdown file inline.
"""

from __future__ import annotations

import json

import pytest


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


def test_oversized_content_is_chunked() -> None:
    from draftly.agents.documentation.plan_guard import (
        MAX_FILE_CONTENT_CHARS,
        chunk_content,
    )

    big = "x" * (MAX_FILE_CONTENT_CHARS + 100)
    chunks = chunk_content(big)
    assert len(chunks) >= 2
    assert "".join(chunks) == big
    assert all(len(c) <= MAX_FILE_CONTENT_CHARS for c in chunks)


def test_valid_plan_round_trips_multiline_content() -> None:
    from draftly.agents.documentation.plan_guard import parse_plan_json_strict

    payload = {
        "repository": "r",
        "branch": "b",
        "files": [
            {"path": "docs/a.md", "content": 'line1\nline2 "quoted"\n', "action": "update"}
        ],
        "commit_message": "docs: update",
        "summary": "s",
    }
    parsed = parse_plan_json_strict(json.dumps(payload))
    assert parsed["files"][0]["content"] == 'line1\nline2 "quoted"\n'


def test_too_many_files_are_rejected() -> None:
    """More than MAX_FILES_PER_PLAN files fails validation (prevents the
    oversized single-plan emission that truncated mid-stream in live runs."""
    from draftly.agents.documentation.plan_guard import (
        MAX_FILES_PER_PLAN,
        validate_plan_dict,
    )

    files = [
        {"path": f"docs/doc-{i}.md", "content": "small", "action": "update"}
        for i in range(MAX_FILES_PER_PLAN + 1)
    ]
    with pytest.raises(ValueError, match="consolidate|max is"):
        validate_plan_dict({"files": files})


def test_total_content_is_capped() -> None:
    """Aggregate content across files must stay under the streaming budget so
    the plan never exceeds the writer model's output-token ceiling."""
    from draftly.agents.documentation.plan_guard import (
        MAX_FILE_CONTENT_CHARS,
        MAX_TOTAL_CONTENT_CHARS,
        validate_plan_dict,
    )

    # Two files each under the per-file cap but collectively over the total cap.
    per = MAX_TOTAL_CONTENT_CHARS // 2 + 1
    assert per <= MAX_FILE_CONTENT_CHARS, "setup should keep each file under per-file cap"
    with pytest.raises(ValueError, match="total"):
        validate_plan_dict(
            {
                "files": [
                    {"path": "docs/a.md", "content": "x" * per},
                    {"path": "docs/b.md", "content": "x" * per},
                ]
            }
        )
