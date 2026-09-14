"""Delivery receipt guards: hollow/empty receipts are NOT delivered.

A GitHub receipt with ``status="completed"`` and an empty ``reference`` means
the delivery agent emitted its structured output without any successful
``create_*`` call. The runner must not report such a run as ``delivered``
(live run 82ddf040 ended "delivered" with no commit and no comment).
"""

from __future__ import annotations

from draftly.workflows.runner import is_blocked_delivery, is_empty_delivery


def test_blocked_receipt_is_not_delivered() -> None:
    assert is_blocked_delivery({"status": "blocked"}) is True
    assert is_blocked_delivery({"status": "completed"}) is False


def test_empty_github_receipt_is_not_delivered() -> None:
    assert is_empty_delivery({"status": "completed", "reference": ""}) is True
    assert is_empty_delivery({"surface": "github", "status": "completed"}) is True
    # An empty dict is falsy, so the guard returns False (matches None behavior).
    assert is_empty_delivery({}) is False


def test_receipt_with_reference_is_delivered() -> None:
    assert is_empty_delivery({"status": "completed", "reference": "abc123"}) is False
    assert (
        is_empty_delivery(
            {"status": "completed", "reference": "https://github.com/a/b/pull/7"}
        )
        is False
    )


def test_slack_receipt_ignores_reference() -> None:
    assert is_empty_delivery({"surface": "slack", "status": "completed"}) is False


def test_null_receipt_is_neither() -> None:
    assert is_empty_delivery(None) is False
    assert is_blocked_delivery(None) is False
