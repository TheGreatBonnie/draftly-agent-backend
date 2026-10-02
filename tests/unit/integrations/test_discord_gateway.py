"""Discord Gateway heartbeat robustness (run e1e96f90 log analysis)."""

from __future__ import annotations

import asyncio
import json

from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close
from websockets.protocol import CloseCode

from draftly.integrations.discord import gateway as gateway_module
from draftly.integrations.discord.gateway import DiscordGateway


class _ClosedSocket:
    """Socket whose peer already went away (half-closed / zombie connection)."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)
        raise ConnectionClosedError(
            None, Close(CloseCode.INTERNAL_ERROR, "keepalive ping timeout"), None
        )


class _RecordingSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


async def test_identify_requests_message_content_needed_by_support_handler(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module.settings, "discord_bot_token", "bot-token")
    socket = _RecordingSocket()

    await DiscordGateway()._send_identify(socket)

    payload = json.loads(socket.sent[0])
    assert payload["d"]["intents"] == (1 | 512 | 32768)


async def test_heartbeat_loop_survives_a_closed_socket() -> None:
    """A heartbeat that hits a closed socket must end quietly.

    Run e1e96f90 logged a full ``ConnectionClosedError`` traceback under "Task
    exception was never retrieved" because ``_heartbeat_loop`` handled only
    ``CancelledError`` and nothing ever awaited the task.
    """
    gateway = DiscordGateway()
    gateway._running = True

    await gateway._heartbeat_loop(_ClosedSocket(), interval=0)

    assert gateway._running is True, "the connection loop still drives reconnects"


async def test_heartbeat_loop_tolerates_unexpected_send_errors() -> None:
    """Any send failure (DNS blip, transport error) must not kill the task."""
    gateway = DiscordGateway()
    gateway._running = True

    class _BrokenSocket:
        async def send(self, payload: str) -> None:
            raise OSError("transport is closed")

    await gateway._heartbeat_loop(_BrokenSocket(), interval=0)


async def test_heartbeat_done_callback_retrieves_task_exceptions() -> None:
    """A dead heartbeat task must never leave an unretrieved exception."""

    async def _boom() -> None:
        raise RuntimeError("heartbeat died")

    gateway = DiscordGateway()
    task: asyncio.Task[None] = asyncio.create_task(_boom())
    await asyncio.sleep(0)

    gateway._heartbeat_done(task)

    assert task.exception() is not None, "the callback must consume the exception"


async def test_heartbeat_done_callback_ignores_cancelled_tasks() -> None:
    gateway = DiscordGateway()
    task: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(30))
    task.cancel()
    await asyncio.sleep(0)  # allow cancellation to propagate

    gateway._heartbeat_done(task)

    assert task.cancelled() is True
