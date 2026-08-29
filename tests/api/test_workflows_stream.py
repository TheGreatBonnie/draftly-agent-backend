"""SSE endpoint: ticket auth, frame shape, terminal-on-result."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.auth import get_verified_token
from draftly.app.api.routes.workflows import router
from draftly.events.redis_bus import RedisEventBus
from draftly.events.stream_envelope import StreamEnvelope
from draftly.integrations.ticket_store import RedisTicketStore


class FakeJobsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {
            "evt-1": {"job_id": "evt-1", "org_id": "org-1", "status": "running"}
        }

    async def get(self, *, job_id: str) -> dict[str, Any] | None:
        return self.rows.get(job_id)


def make_app(bus: RedisEventBus | None = None, token: dict[str, Any] | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_verified_token] = lambda: token or {"org_id": "org-1"}
    fake_redis = FakeRedis(decode_responses=True)
    app.state.redis_client = type("Native", (), {"native": fake_redis})()
    app.state.redis_tickets = RedisTicketStore(fake_redis, ttl_seconds=60)

    state = SimpleNamespaceState()
    state.dependencies.repositories.jobs = FakeJobsRepo()
    state.workflows.event_bus = bus
    app.state.draftly = state
    return app


class SimpleNamespaceState:
    """Mimics request.app.state.draftly shape used by all routes."""

    def __init__(self) -> None:
        from types import SimpleNamespace

        self.dependencies = SimpleNamespace(
            repositories=SimpleNamespace(jobs=None)
        )
        self.workflows = SimpleNamespace(event_bus=None)


async def issue(app: FastAPI, run_id: str = "evt-1") -> str:
    return await app.state.redis_tickets.issue(run_id, org_id="org-1")


def http_scope(
    route: str, *, query: dict[str, str] | None = None, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    raw_headers = [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
    ]
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": route,
        "raw_path": route.encode(),
        "query_string": urlencode(query or {}).encode(),
        "root_path": "",
        "headers": raw_headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "state": {},
    }


@dataclass
class ASGISession:
    """Drives an SSE response in-process on the caller's event loop.

    Unlike TestClient, this delivers body chunks incrementally as the server
    produces them (TestClient buffers the full body until the response
    completes, which deadlocks always-open SSE streams).
    """

    app: FastAPI
    scope: dict[str, Any]
    status_code: int | None = None
    headers: list[tuple[bytes, bytes]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        from asyncio import Queue

        self.sent = Queue()
        self.received = Queue()

    async def send(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.status_code = message["status"]
            self.headers = message["headers"]
        await self.sent.put(message)

    async def receive(self) -> dict[str, Any]:
        return await self.received.get()

    async def run(self) -> None:
        await self.app(self.scope, self.receive, self.send)

    def body_chunks(self) -> AsyncIterator[bytes]:
        async def _gen():
            while True:
                message = await self.sent.get()
                if message.get("type") != "http.response.body":
                    continue
                yield message.get("body", b"")
                if not message.get("more_body", False):
                    return

        return _gen()

    def frames(self) -> AsyncIterator[str]:
        async def _gen():
            leftover = b""
            async for chunk in self.body_chunks():
                leftover += chunk
                while b"\r\n\r\n" in leftover:
                    head, _, leftover = leftover.partition(b"\r\n\r\n")
                    yield head.decode()

        return _gen()


class TestTickets:
    @pytest.mark.asyncio
    async def test_tickets_are_single_use(self) -> None:
        fake_redis = FakeRedis(decode_responses=True)
        store = RedisTicketStore(fake_redis, ttl_seconds=60)
        ticket = await store.issue("evt-1", org_id="org-1")
        assert await store.consume(ticket) == ("evt-1", "org-1")
        assert await store.consume(ticket) is None

    @pytest.mark.asyncio
    async def test_expired_ticket_rejected(self) -> None:
        fake_redis = FakeRedis(decode_responses=True)
        store = RedisTicketStore(fake_redis, ttl_seconds=1)
        ticket = await store.issue("evt-1", org_id="org-1")
        import asyncio
        await asyncio.sleep(1.1)
        assert await store.consume(ticket) is None


class TestTicketRoute:
    def test_issue_ticket_requires_known_run(self) -> None:
        app = make_app()
        client = TestClient(app)
        resp = client.post("/workflows/missing/stream-ticket")
        assert resp.status_code == 404

    def test_issue_ticket_unknown_run_logs_structured_error(self) -> None:
        """Task 3: an unknown run must emit a structured error line so the
        incident is visible in production logging."""
        from structlog.testing import capture_logs

        with capture_logs() as cap:
            app = make_app()
            client = TestClient(app)
            resp = client.post("/workflows/missing/stream-ticket")

        assert resp.status_code == 404
        assert any(
            e.get("event") == "stream_ticket_unknown_run"
            and e.get("run_id") == "missing"
            and e.get("org_id") == "org-1"
            for e in cap
        ), f"expected structured log, got: {cap}"

    def test_issue_ticket_returns_ticket(self) -> None:
        app = make_app()
        client = TestClient(app)
        resp = client.post("/workflows/evt-1/stream-ticket")
        assert resp.status_code == 200
        assert resp.json()["ticket"]

    def test_issue_ticket_requires_org(self) -> None:
        app = make_app(token={"sub": "u"})
        client = TestClient(app)
        assert client.post("/workflows/evt-1/stream-ticket").status_code == 400


class TestEventStream:
    async def test_rejects_invalid_ticket(self) -> None:
        app = make_app(bus=RedisEventBus(redis_client=FakeRedis()))
        session = ASGISession(
            app,
            http_scope("/workflows/evt-1/events", query={"ticket": "bogus"}),
        )
        await asyncio.wait_for(session.run(), timeout=5)
        assert session.status_code == 403

    async def test_stream_frames_and_terminal(self) -> None:
        bus = RedisEventBus(redis_client=FakeRedis())
        app = make_app(bus=bus)
        ticket = await issue(app)

        start = StreamEnvelope(type="node_start", run_id="evt-1", surface="documentation", seq=1)
        done = StreamEnvelope(
            type="workflow_result", run_id="evt-1", surface="documentation", seq=2
        )
        done.payload = {"status": "COMPLETED", "interrupts": []}

        async def publish() -> None:
            await asyncio.sleep(0.3)
            await bus.publish(start)
            await bus.publish(done)

        pub_task = asyncio.create_task(publish())
        session = ASGISession(
            app, http_scope("/workflows/evt-1/events", query={"ticket": ticket})
        )
        run_task = asyncio.create_task(session.run())

        frames = [f async for f in session.frames()]
        await asyncio.wait_for(run_task, timeout=5)
        await pub_task

        assert session.status_code == 200
        assert "event: node_start" in frames[0]
        assert '"workflow_result"' in frames[-1].replace("'", '"')
        # ticket consumed exactly once
        assert await app.state.redis_tickets.consume(ticket) is None

    async def test_heartbeat_when_idle(self) -> None:
        """Idle stream still emits keepalive ``: ping`` frames in real time."""
        bus = RedisEventBus(redis_client=FakeRedis())
        app = make_app(bus=bus)
        app.state.draftly.heartbeat = 0.05
        ticket = await issue(app)

        session = ASGISession(
            app,
            http_scope(
                "/workflows/evt-1/events",
                query={"ticket": ticket, "heartbeat": "0.05"},
            ),
        )
        run_task = asyncio.create_task(session.run())
        frames = session.frames()

        # No events are ever published; keepalive pings must still arrive.
        first = await asyncio.wait_for(anext(frames), timeout=5)
        second = await asyncio.wait_for(anext(frames), timeout=5)
        assert first.startswith(": ping")
        assert second.startswith(": ping")

        done = StreamEnvelope(type="workflow_result", run_id="evt-1", surface="", seq=9)
        done.payload = {"status": "COMPLETED", "interrupts": []}
        await bus.publish(done)
        await asyncio.wait_for(run_task, timeout=5)

    async def test_replays_after_last_event_id(self) -> None:
        bus = RedisEventBus(redis_client=FakeRedis())

        class FakeEventsRepo:
            async def list_after(self, run_id: str, *, seq: int, limit: int = 500):
                assert run_id == "evt-1"
                return [
                    {
                        "run_id": run_id,
                        "seq": 2,
                        "ts": "2026-08-23T00:00:00Z",
                        "type": "node_stop",
                        "node_id": "classify",
                        "payload": {"status": "COMPLETED", "duration_ms": 5},
                    }
                ]

        app = make_app(bus=bus)
        # attach events repo alongside jobs on repositories namespace
        repos = app.state.draftly.dependencies.repositories
        repos.workflow_events = FakeEventsRepo()
        ticket = await issue(app)

        session = ASGISession(
            app,
            http_scope(
                "/workflows/evt-1/events",
                query={"ticket": ticket},
                headers={"last-event-id": "1"},
            ),
        )
        run_task = asyncio.create_task(session.run())
        frames = session.frames()

        # The replayed backlog is served first, before any live event.
        first = await asyncio.wait_for(anext(frames), timeout=5)
        assert first.startswith("id: 2")
        assert "event: node_stop" in first

        done = StreamEnvelope(type="workflow_result", run_id="evt-1", surface="", seq=3)
        done.payload = {"status": "COMPLETED", "interrupts": []}
        await bus.publish(done)
        await asyncio.wait_for(run_task, timeout=5)

    async def test_bus_unavailable_503(self) -> None:
        app = make_app(bus=None)
        session = ASGISession(
            app,
            http_scope("/workflows/evt-1/events", query={"ticket": await issue(app)}),
        )
        run_task = asyncio.create_task(session.run())
        await asyncio.wait_for(run_task, timeout=5)
        assert session.status_code == 503

    async def test_dedupes_events_on_replay(self) -> None:
        """Ensure identical seqs from replay + live are deduplicated."""
        bus = RedisEventBus(redis_client=FakeRedis())

        class FakeEventsRepo:
            async def list_after(self, run_id: str, *, seq: int, limit: int = 500):
                return [
                    {
                        "run_id": run_id,
                        "seq": 1,
                        "type": "node_start",
                        "ts": "2026-08-23T00:00:00Z",
                        "payload": {},
                    },
                    {
                        "run_id": run_id,
                        "seq": 2,
                        "type": "node_stop",
                        "ts": "2026-08-23T00:00:01Z",
                        "payload": {},
                    },
                    {
                        "run_id": run_id,
                        "seq": 3,
                        "type": "text_delta",
                        "ts": "2026-08-23T00:00:02Z",
                        "payload": {},
                    },
                ]

        app = make_app(bus=bus)
        app.state.draftly.dependencies.repositories.workflow_events = FakeEventsRepo()
        ticket = await issue(app)

        start1 = StreamEnvelope(type="node_start", run_id="evt-1", surface="", seq=1)
        stop2 = StreamEnvelope(type="node_stop", run_id="evt-1", surface="", seq=2)
        text3 = StreamEnvelope(type="text_delta", run_id="evt-1", surface="", seq=3)
        text4 = StreamEnvelope(type="text_delta", run_id="evt-1", surface="", seq=4)
        done = StreamEnvelope(type="workflow_result", run_id="evt-1", surface="", seq=5)

        async def publish() -> None:
            await asyncio.sleep(0.3)
            await bus.publish(start1)
            await bus.publish(stop2)
            await bus.publish(text3)
            await bus.publish(text4)
            await bus.publish(done)

        pub_task = asyncio.create_task(publish())
        session = ASGISession(
            app,
            http_scope("/workflows/evt-1/events", query={"ticket": ticket}),
        )
        run_task = asyncio.create_task(session.run())

        frames = [f async for f in session.frames()]
        await asyncio.wait_for(run_task, timeout=5)
        await pub_task

        assert session.status_code == 200
        for i in range(1, 6):
            # each replay+live seq appears exactly once after dedupe
            assert sum(f"id: {i}" in f for f in frames) == 1


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    yield
    with contextlib.suppress(Exception):
        pass
