"""Cache GitHub App installation tokens.

Run d76e2490 minted 28 installation tokens for a single
``installation_id=164603551``. Every tool call re-mints one: an RS256 JWT
signed with the App's key, then a POST to
``/app/installations/{id}/access_tokens``. That is 28 network round trips of
pure waste, and it is per-call, so the cost scales with the tool-call count
that change C and D exist to reduce.

GitHub expires installation tokens after 1 hour. The cache holds them for 45
minutes, so a token handed out is always valid for at least 15 more minutes —
long enough that GitHub never rejects a token mid-request.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from draftly.integrations.github import app_auth


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    app_auth.clear_installation_token_cache()
    yield
    app_auth.clear_installation_token_cache()


@pytest.fixture
def mint_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    async def _mint(installation_id: int) -> str:
        calls.append(installation_id)
        return f"token-{len(calls)}"

    monkeypatch.setattr(app_auth, "_mint_installation_token", _mint)
    return calls


@pytest.mark.asyncio
async def test_repeat_calls_mint_once(mint_calls: list[int]) -> None:
    calls = mint_calls

    first = await app_auth.get_installation_token(164603551)
    second = await app_auth.get_installation_token(164603551)

    assert first == second == "token-1"
    assert calls == [164603551]


@pytest.mark.asyncio
async def test_different_installations_do_not_share_a_token(
    mint_calls: list[int],
) -> None:
    calls = mint_calls

    one = await app_auth.get_installation_token(1)
    two = await app_auth.get_installation_token(2)

    assert one != two
    assert calls == [1, 2]


@pytest.mark.asyncio
async def test_expired_token_is_reminted(
    mint_calls: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = mint_calls
    monkeypatch.setattr(app_auth, "_TOKEN_TTL_SECONDS", 0)  # type: ignore[attr-defined]

    await app_auth.get_installation_token(1)
    await app_auth.get_installation_token(1)

    assert calls == [1, 1]


@pytest.mark.asyncio
async def test_concurrent_callers_mint_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """28 concurrent tool calls must not become 28 mints.

    A check-then-mint cache with no lock collapses under the concurrency the
    workflow actually has.
    """
    calls: list[int] = []

    async def _slow_mint(installation_id: int) -> str:
        calls.append(installation_id)
        await asyncio.sleep(0.01)
        return f"token-{len(calls)}"

    monkeypatch.setattr(app_auth, "_mint_installation_token", _slow_mint)

    tokens = await asyncio.gather(
        *(app_auth.get_installation_token(1) for _ in range(8))
    )

    assert len(set(tokens)) == 1
    assert calls == [1]


@pytest.mark.asyncio
async def test_a_failed_mint_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient 502 must not be remembered as a token."""
    attempts = 0

    async def _flaky(installation_id: int) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("502 from GitHub")
        return f"token-{attempts}"

    monkeypatch.setattr(app_auth, "_mint_installation_token", _flaky)

    with pytest.raises(RuntimeError):
        await app_auth.get_installation_token(1)

    assert await app_auth.get_installation_token(1) == "token-2"


@pytest.mark.asyncio
async def test_concurrent_failure_does_not_wedge_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raise inside the lock must release it, or every later call hangs."""
    attempts = 0

    async def _flaky(installation_id: int) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        return f"token-{attempts}"

    monkeypatch.setattr(app_auth, "_mint_installation_token", _flaky)

    results = await asyncio.gather(
        app_auth.get_installation_token(1),
        app_auth.get_installation_token(1),
        return_exceptions=True,
    )

    assert any(isinstance(r, RuntimeError) for r in results)
    assert await app_auth.get_installation_token(1) == "token-2"
