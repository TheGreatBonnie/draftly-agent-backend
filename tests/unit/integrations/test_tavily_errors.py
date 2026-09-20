"""Tavily error taxonomy, retry classification, and backoff bounds.

Spec: docs/superpowers/specs/2026-09-20-tavily-rag-design.md (Error model).
Plan: docs/superpowers/plans/2026-09-20-tavily-rag.md (Task 2).
"""

from __future__ import annotations

import pytest

from draftly.integrations.tavily.errors import (
    RETRYABLE,
    TavilyError,
    TavilyErrorCode,
    is_retryable,
    retry_backoff,
)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (TavilyErrorCode.RATE_LIMIT, True),
        (TavilyErrorCode.TIMEOUT, True),
        (TavilyErrorCode.UPSTREAM, True),
        (TavilyErrorCode.AUTHENTICATION, False),
        (TavilyErrorCode.INVALID_REQUEST, False),
        (TavilyErrorCode.UNSUPPORTED_URL, False),
        (TavilyErrorCode.CREDIT_LIMIT, False),
        (TavilyErrorCode.INVALID_RESPONSE, False),
    ],
)
def test_is_retryable_table(code: TavilyErrorCode, expected: bool) -> None:
    assert is_retryable(code) is expected


def test_retryable_set_matches_spec() -> None:
    assert RETRYABLE == frozenset(
        {
            TavilyErrorCode.RATE_LIMIT,
            TavilyErrorCode.TIMEOUT,
            TavilyErrorCode.UPSTREAM,
        }
    )


def test_error_code_values_match_spec_taxonomy() -> None:
    assert {code.value for code in TavilyErrorCode} == {
        "authentication",
        "invalid_request",
        "unsupported_url",
        "rate_limit",
        "credit_limit",
        "timeout",
        "upstream",
        "invalid_response",
    }


def test_error_carries_code_request_id_retry_after() -> None:
    err = TavilyError(
        TavilyErrorCode.RATE_LIMIT,
        "slow down",
        request_id="req-123",
        retry_after_seconds=5.0,
    )
    assert err.code is TavilyErrorCode.RATE_LIMIT
    assert err.request_id == "req-123"
    assert err.retry_after_seconds == 5.0
    assert str(err) == "slow down"


def test_error_defaults_are_none() -> None:
    err = TavilyError(TavilyErrorCode.UPSTREAM, "bad gateway")
    assert err.request_id is None
    assert err.retry_after_seconds is None


def test_backoff_grows_and_is_capped() -> None:
    first = retry_backoff(0)
    later = retry_backoff(10)
    assert first > 0
    assert later <= 20.0 * 1.2  # cap * (1 + jitter)


def test_backoff_respects_custom_cap() -> None:
    assert retry_backoff(10, cap=3.0) <= 3.0 * 1.2
