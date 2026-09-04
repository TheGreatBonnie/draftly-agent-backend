"""RED: tool-input hardening for truncated JSON parse-drops.

Reproduces the eval warnings:
  tool_name=<EvidenceBundle|read_file>, raw_input=<{...cut...>
  | failed to parse tool input json, defaulting to empty dict
"""

from __future__ import annotations

import pytest


def test_oversized_string_arg_is_rejected() -> None:
    from draftly.tools._guard import OversizedToolInputError, require_max_length

    with pytest.raises(OversizedToolInputError, match="excerpt"):
        require_max_length("x" * 5000, 100, "content", "EvidenceBundle")


def test_absolute_path_arg_is_rejected() -> None:
    from draftly.tools._guard import (
        AbsolutePathError,
        require_repo_relative_path,
    )

    with pytest.raises(AbsolutePathError, match="repo-relative"):
        require_repo_relative_path(
            "/Applications/Projects/hackathon/draftly-docs-engineer/authly/x.py",
            "path",
            "read_file",
        )


def test_evidence_item_full_file_dump_is_rejected() -> None:
    from draftly.tools._guard import OversizedToolInputError, validate_evidence_item

    with pytest.raises(OversizedToolInputError, match="excerpt"):
        validate_evidence_item(
            {"path": "src/authly/oauth.py", "content": "x" * 5000}
        )


def test_evidence_item_reference_passes() -> None:
    from draftly.tools._guard import validate_evidence_item

    item = validate_evidence_item(
        {"path": "src/authly/oauth.py", "content": "short excerpt"}
    )
    assert item["path"] == "src/authly/oauth.py"
