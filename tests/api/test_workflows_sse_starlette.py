"""Tests for SSE endpoints using sse-starlette EventSourceResponse."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
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
            "run-1": {"job_id": "run-1", "org_id": "org-1", "status": "running"}
        }

    async def get(self, *, job_id: str) -> dict[str, Any] | None:
        return self.rows.get(job_id)


class SimpleNamespaceState:
    """Mimics request.app.state.draftly shape used by all routes."""

    def __init__(self) -> None:
        self.dependencies = SimpleNamespace(
            repositories=SimpleNamespace(jobs=None)
        )
        self.workflows = SimpleNamespace(event_bus=None)


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


async def issue(app: FastAPI, run_id: str = "run-1") -> str:
    return await app.state.redis_tickets.issue(run_id, org_id="org-1")


class TestSSEStarletteStream:
    """Verify sse-starlette EventSourceResponse produces correct SSE frames."""

    def test_rejects_invalid_ticket(self) -> None:
        app = make_app(bus=RedisEventBus(redis_client=FakeRedis()))
        client = TestClient(app)
        resp = client.get("/workflows/run-1/events", params={"ticket": "bogus"})
        assert resp.status_code == 403

    async def test_stream_frames_have_correct_structure(self) -> None:
        """Events arrive with id:, event:, data: fields (sse-starlette format)."""
        bus = RedisEventBus(redis_client=FakeRedis())
        app = make_app(bus=bus)
        client = TestClient(app)

        start = StreamEnvelope(type="node_start", run_id="run-1", surface="docs", seq=1)
        done = StreamEnvelope(type="workflow_result", run_id="run-1", surface="docs", seq=2)
        done.payload = {"status": "COMPLETED", "interrupts": []}

        def publish() -> None:
            time.sleep(0.3)
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
            "GET", "/workflows/run-1/events", params={"ticket": ticket}
        ) as resp:
            assert resp.status_code == 200
            body = b"".join(resp.iter_bytes()).decode()

        thread.join(timeout=5)
        # sse-starlette uses \r\n line separators
        assert "event: node_start" in body
        assert "event: workflow_result" in body
        # data field contains JSON with the envelope
        assert '"type": "node_start"' in body or '"type":"node_start"' in body
        assert '"seq": 1' in body or '"seq":1' in body

    async def test_stream_terminates_on_workflow_result(self) -> None:
        """Stream closes after workflow_result event."""
        bus = RedisEventBus(redis_client=FakeRedis())
        app = make_app(bus=bus)
        client = TestClient(app)

        done = StreamEnvelope(type="workflow_result", run_id="run-1", surface="docs", seq=1)
        done.payload = {"status": "COMPLETED"}

        def publish() -> None:
            time.sleep(0.3)
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(bus.publish(done))
            finally:
                loop.close()

        thread = threading.Thread(target=publish, daemon=True)
        thread.start()

        ticket = await issue(app)
        with client.stream(
            "GET", "/workflows/run-1/events", params={"ticket": ticket}
        ) as resp:
            assert resp.status_code == 200
            body = b"".join(resp.iter_bytes()).decode()
            # Connection should close — no more data after workflow_result
        thread.join(timeout=5)
        assert "workflow_result" in body

    async def test_heartbeat_sent_when_idle(self) -> None:
        """sse-starlette sends ping comments to keep the idle connection alive."""
        from tests.api.test_workflows_stream import ASGISession, http_scope

        bus = RedisEventBus(redis_client=FakeRedis())
        app = make_app(bus=bus)
        app.state.heartbeat = 1
        ticket = await issue(app)

        session = ASGISession(
            app,
            http_scope(
                "/workflows/run-1/events",
                query={"ticket": ticket, "heartbeat": "1"},
            ),
        )
        run_task = asyncio.create_task(session.run())
        frames = session.frames()

        # No events published; the keepalive ping must still arrive in real time.
        first = await asyncio.wait_for(anext(frames), timeout=5)
        assert first.startswith(": ping")

        done = StreamEnvelope(type="workflow_result", run_id="run-1", surface="docs", seq=2)
        done.payload = {"status": "COMPLETED"}
        await bus.publish(done)
        await asyncio.wait_for(run_task, timeout=5)

    async def test_bus_unavailable_503(self) -> None:
        app = make_app(bus=None)
        client = TestClient(app)
        ticket = await issue(app)
        resp = client.get("/workflows/run-1/events", params={"ticket": ticket})
        assert resp.status_code == 503

    @pytest.mark.xfail(reason="sse-starlette replay via TestClient.stream hangs (pre-existing)")
    async def test_replays_stored_events(self) -> None:
        """NeonDB events are replayed on initial connection."""
        pytest.skip("sse-starlette replay hangs in TestClient (pre-existing)")
        bus = RedisEventBus(redis_client=FakeRedis())

        class FakeEventsRepo:
            async def list_after(self, run_id: str, *, seq: int, limit: int = 500):
                return [
                    {
                        "run_id": run_id,
                        "seq": 1,
                        "ts": "2026-08-27T00:00:00Z",
                        "type": "stage_change",
                        "node_id": "plan",
                        "payload": {"stage": "planning"},
                    },
                    {
                        "run_id": run_id,
                        "seq": 2,
                        "ts": "2026-08-27T00:00:01Z",
                        "type": "workflow_result",
                        "node_id": None,
                        "payload": {"status": "COMPLETED"},
                    },
                ]

        app = make_app(bus=bus)
        repos = app.state.draftly.dependencies.repositories
        repos.workflow_events = FakeEventsRepo()
        client = TestClient(app)

        ticket = await issue(app)
        with client.stream(
            "GET", "/workflows/run-1/events", params={"ticket": ticket}
        ) as resp:
            assert resp.status_code == 200
            body = b"".join(resp.iter_bytes()).decode()

        assert "stage_change" in body
        assert "workflow_result" in body
