"""Tests for the async-to-sync wrapper utility."""

from __future__ import annotations

import pytest

from draftly.app.workers.async_sync import make_sync_handler


async def _async_add(a: int, b: int) -> int:
    return a + b


async def _async_raises() -> None:
    raise ValueError("boom")


class TestMakeSyncHandler:
    def test_wraps_async_function(self):
        handler = make_sync_handler(_async_add)
        result = handler(2, 3)
        assert result == 5

    def test_preserves_exception(self):
        handler = make_sync_handler(_async_raises)
        with pytest.raises(ValueError, match="boom"):
            handler()

    def test_returns_new_event_loop_each_call(self):
        handler = make_sync_handler(_async_add)
        r1 = handler(1, 2)
        r2 = handler(3, 4)
        assert r1 == 3
        assert r2 == 7
