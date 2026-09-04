"""Tests for the slack socket-mode shutdown/session cleanup."""

from __future__ import annotations

from types import SimpleNamespace

import aiohttp
import pytest

from draftly.integrations.slack.socket import _close_slack_session


class _SessionHolder:
    def __init__(self, session):
        self.session = session


@pytest.mark.asyncio
async def test_close_slack_session_closes_live_aiohttp_session() -> None:
    session = aiohttp.ClientSession()
    assert not session.closed

    await _close_slack_session(SimpleNamespace(client=_SessionHolder(session), webhook=None))

    assert session.closed


@pytest.mark.asyncio
async def test_close_slack_session_tolerates_none_and_non_sessions() -> None:
    # client.session is None until the SDK lazily creates it on first request.
    ok = SimpleNamespace(client=SimpleNamespace(session=None), webhook=None)
    await _close_slack_session(ok)  # must not raise

    # A closed session must not be re-closed.
    session = aiohttp.ClientSession()
    await session.close()
    await _close_slack_session(SimpleNamespace(client=_SessionHolder(session), webhook=None))
    assert session.closed
