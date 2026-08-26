"""SSE endpoint: ticket auth, frame shape, terminal-on-result."""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any

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
    def test_rejects_invalid_ticket(self) -> None:
        app = make_app(bus=RedisEventBus(redis_client=FakeRedis()))
        client = TestClient(app)
        resp = client.get("/workflows/evt-1/events", params={"ticket": "bogus"})
        assert resp.status_code == 403

    async def test_stream_frames_and_terminal(self) -> None:
        bus = RedisEventBus(redis_client=FakeRedis())
        app = make_app(bus=bus)
        client = TestClient(app)

        start = StreamEnvelope(type="node_start", run_id="evt-1", surface="documentation", seq=1)
        done = StreamEnvelope(
            type="workflow_result", run_id="evt-1", surface="documentation", seq=2
        )
        done.payload = {"status": "COMPLETED", "interrupts": []}

        def publish() -> None:
            time.sleep(0.3)
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(bus.publish(start))
                loop.run_until_complete(bus.publish(done))
            finally:
                loop.close()

        thread = threading.Thread(target=publish, daemon=True)
        thread.start()

        ticket = await issue(app)
        with client.stream(
            "GET", "/workflows/evt-1/events", params={"ticket": ticket}
        ) as resp:
            assert resp.status_code == 200
            body = b"".join(resp.iter_bytes()).decode()

        thread.join(timeout=5)
        assert "event: node_start" in body
        assert '"workflow_result"' in body.replace("'", '"')
        # ticket consumed exactly once
        assert await app.state.redis_tickets.consume(ticket) is None

    async def test_heartbeat_when_idle(self) -> None:
        bus = RedisEventBus(redis_client=FakeRedis())
        app = make_app(bus=bus)
        app.state.draftly.heartbeat = 0.05
        client = TestClient(app)
        ticket = await issue(app)
        with client.stream(
            "GET",
            "/workflows/evt-1/events",
            params={"ticket": ticket, "heartbeat": "0.05"},
        ) as resp:
            assert resp.status_code == 200
            first_chunk = next(resp.iter_raw())
        assert first_chunk.startswith(b": ping")

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
        client = TestClient(app)
        ticket = await issue(app)

        with client.stream(
            "GET",
            "/workflows/evt-1/events",
            params={"ticket": ticket},
            headers={"last-event-id": "1"},
        ) as resp:
            assert resp.status_code == 200
            first_frame = next(resp.iter_raw()).decode()

        assert first_frame.startswith("id: 2")
        assert "event: node_stop" in first_frame

    async def test_bus_unavailable_503(self) -> None:
        app = make_app(bus=None)
        client = TestClient(app)
        ticket = await issue(app)
        resp = client.get("/workflows/evt-1/events", params={"ticket": ticket})
        assert resp.status_code == 503


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    yield
    with contextlib.suppress(Exception):
        pass
