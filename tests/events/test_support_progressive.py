"""Progressive renderer: accumulate + throttled edit + terminal stop."""

from __future__ import annotations

import asyncio

from draftly.events.consumers.support_progressive import (
    SupportProgressiveRenderer,
)
from draftly.events.stream_envelope import StreamEnvelope


def delta(text: str, seq: int) -> StreamEnvelope:
    env = StreamEnvelope(type="text_delta", run_id="r", surface="support", seq=seq)
    env.payload = {"text": text}
    return env


class FakeBus:
    def __init__(self, envelopes: list[StreamEnvelope]) -> None:
        self.envelopes = envelopes

    async def subscribe(self, run_id: str):
        for env in self.envelopes:
            yield env


class FakeClient:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit_message(self, channel_id: str, message_id: str, content: str) -> None:
        self.edits.append(content)


async def test_edits_throttled_and_final_flush() -> None:
    client = FakeClient()
    renderer = SupportProgressiveRenderer(
        client,
        channel_ref=("ch-1", "msg-1"),
        throttle_seconds=0,
    )
    envelopes = [delta("a", 1), delta("b", 2), delta("c", 3)]
    terminal = StreamEnvelope(
        type="workflow_result", run_id="r", surface="support", seq=4
    )
    terminal.payload = {"status": "COMPLETED"}
    envelopes.append(terminal)

    await asyncio.wait_for(renderer.run(FakeBus(envelopes), "r"), timeout=2)

    # zero throttle => one edit per delta; terminal flush adds nothing new
    assert client.edits == ["a", "ab", "abc"]


async def test_throttle_coalesces_after_first_edit() -> None:
    client = FakeClient()
    renderer = SupportProgressiveRenderer(
        client,
        channel_ref=("ch-1", "msg-1"),
        throttle_seconds=60,
    )
    envelopes = [delta("a", 1), delta("b", 2)]
    terminal = StreamEnvelope(
        type="workflow_result", run_id="r", surface="support", seq=3
    )
    terminal.payload = {"status": "COMPLETED"}
    envelopes.append(terminal)

    await asyncio.wait_for(renderer.run(FakeBus(envelopes), "r"), timeout=2)

    # first delta edits immediately; the rest coalesces into the final flush
    assert client.edits == ["a", "ab"]


async def test_edit_failures_swallowed() -> None:
    class ExplodingClient:
        def __init__(self) -> None:
            self.calls = 0

        async def edit_message(self, channel_id: str, message_id: str, content: str) -> None:
            self.calls += 1
            raise RuntimeError("discord down")

    client = ExplodingClient()
    renderer = SupportProgressiveRenderer(
        client,
        channel_ref=("ch-1", "msg-1"),
        throttle_seconds=0,
    )
    envelopes = [delta("a", 1), delta("b", 2)]

    await asyncio.wait_for(renderer.run(FakeBus(envelopes), "r"), timeout=2)
    assert client.calls == 2  # kept trying despite failures
