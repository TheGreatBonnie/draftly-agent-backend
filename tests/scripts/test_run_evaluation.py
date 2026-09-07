from __future__ import annotations

import pytest

from scripts import run_evaluation


@pytest.mark.parametrize(
    ("status", "errors", "summary", "expected"),
    [
        ("delivered", [], {"total": 2, "passed_all": True}, 0),
        ("failed", [], {"total": 2, "passed_all": True}, 1),
        ("delivered", ["dataset failed"], {"total": 2, "passed_all": True}, 1),
        ("delivered", [], {"total": 0, "passed_all": False}, 1),
        ("delivered", [], {"total": 2, "passed_all": False}, 1),
    ],
)
def test_evaluation_exit_code_reflects_evaluation_outcome(
    status: str,
    errors: list[str],
    summary: dict[str, object],
    expected: int,
) -> None:
    assert (
        run_evaluation.evaluation_exit_code(status=status, errors=errors, summary=summary)
        == expected
    )
