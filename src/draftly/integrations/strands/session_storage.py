"""DB-backed Strands session storage (layer 1B of the resume-session fix).

Interrupted graph state must survive process restarts and multi-instance
deployments for a review to resume. Strands' session-manager interface is
synchronous, so this module runs an asyncpg pool on a dedicated worker-thread
event loop and blocks each call on its result (``run_coroutine_threadsafe``
never deadlocks here because the caller is always a different thread than the
pool's loop).

Rows live in a single ``draftly_sessions`` JSONB table keyed by a composite
``key``:

- ``session:{session_id}``
- ``agent:{session_id}:{agent_id}``
- ``message:{session_id}:{agent_id}:{message_id}``
- ``multi_agent:{session_id}:{multi_agent_id}``
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
from typing import Any, cast

import asyncpg
import structlog
from strands.session.session_repository import SessionRepository
from strands.types.session import Session, SessionAgent, SessionMessage

from draftly.integrations.database.client import _register_json_codecs

logger = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 60.0
_DEFAULT_LIST_LIMIT = 1000


def _resolve_database_url(database_url: str | None) -> str:
    return (
        database_url
        or os.environ.get("NEON_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    )


class DatabaseSessionStore:
    """Synchronous facade over an asyncpg pool owned by a worker event loop."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._dsn = _resolve_database_url(database_url)
        self._timeout = timeout
        self._pool: asyncpg.Pool | None = None

        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._serve,
            args=(self._loop, self._ready),
            name="draftly-session-store",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()

    def _serve(self, loop: asyncio.AbstractEventLoop, ready: threading.Event) -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    def _run(self, coro: Any) -> Any:
        """Run a coroutine on the worker loop and block for its result."""
        future = asyncio.run_coroutine_threadsafe(
            self._ensure_pool_and(coro),
            self._loop,
        )
        try:
            return future.result(timeout=self._timeout)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError("draftly session store timed out") from exc

    async def _ensure_pool_and(self, coro: Any) -> Any:
        if self._pool is None:
            if not self._dsn:
                raise RuntimeError(
                    "draftly session store requires NEON_DATABASE_URL or DATABASE_URL"
                )
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=1,
                max_size=4,
                command_timeout=30,
                init=_register_json_codecs,
            )
        return await coro

    def upsert(self, key: str, session_id: str, payload: dict[str, Any]) -> None:
        self._run(self._upsert(key, session_id, payload))

    def get(self, key: str) -> dict[str, Any] | None:
        result = self._run(self._get(key))
        return cast("dict[str, Any] | None", result)

    def list_messages(
        self,
        session_id: str,
        agent_id: str,
        *,
        limit: int | None,
        offset: int,
    ) -> list[dict[str, Any]]:
        result = self._run(self._list_messages(session_id, agent_id, limit, offset))
        return cast("list[dict[str, Any]]", result)

    async def _upsert(self, key: str, session_id: str, payload: dict[str, Any]) -> None:
        assert self._pool is not None
        await self._pool.execute(
            """
            INSERT INTO draftly_sessions (key, session_id, payload)
            VALUES ($1, $2, $3)
            ON CONFLICT (key) DO UPDATE
                SET payload = excluded.payload, updated_at = now()
            """,
            key,
            session_id,
            payload,
        )

    async def _get(self, key: str) -> dict[str, Any] | None:
        assert self._pool is not None
        result = await self._pool.fetchrow(
            "SELECT payload FROM draftly_sessions WHERE key = $1",
            key,
        )
        return result["payload"] if result is not None else None

    async def _list_messages(
        self,
        session_id: str,
        agent_id: str,
        limit: int | None,
        offset: int,
    ) -> list[dict[str, Any]]:
        assert self._pool is not None
        rows = await self._pool.fetch(
            """
            SELECT payload FROM draftly_sessions
            WHERE key LIKE $1
            ORDER BY CAST(payload->>'message_id' AS INTEGER)
            LIMIT $2 OFFSET $3
            """,
            f"message:{session_id}:{agent_id}:%",
            limit if limit is not None else _DEFAULT_LIST_LIMIT,
            offset,
        )
        return [row["payload"] for row in rows]

    def close(self) -> None:
        """Close the pool and stop the worker loop (best-effort, thread-safe)."""
        if self._thread is not None and self._thread.is_alive():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._shutdown(),
                    self._loop,
                )
                future.result(timeout=self._timeout)
            except Exception:
                logger.warning("session_store_shutdown_incomplete", exc_info=True)

    async def _shutdown(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        self._loop.stop()


class DatabaseSessionRepository(SessionRepository):
    """Strands ``SessionRepository`` over the shared database.

    ``store`` is injectable for offline tests; when omitted a real
    ``DatabaseSessionStore`` is constructed.
    """

    def __init__(
        self,
        *,
        store: DatabaseSessionStore | None = None,
        database_url: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._store = store or DatabaseSessionStore(
            database_url=database_url,
            timeout=timeout,
        )

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"session:{session_id}"

    @staticmethod
    def _agent_key(session_id: str, agent_id: str) -> str:
        return f"agent:{session_id}:{agent_id}"

    @staticmethod
    def _message_key(session_id: str, agent_id: str, message_id: int) -> str:
        return f"message:{session_id}:{agent_id}:{message_id}"

    @staticmethod
    def _multi_agent_key(session_id: str, multi_agent_id: str) -> str:
        return f"multi_agent:{session_id}:{multi_agent_id}"

    def close(self) -> None:
        self._store.close()

    # -- Session ----------------------------------------------------------

    def create_session(self, session: Session, **kwargs: Any) -> Session:
        self._store.upsert(
            self._session_key(session.session_id),
            session.session_id,
            session.to_dict(),
        )
        return session

    def read_session(self, session_id: str, **kwargs: Any) -> Session | None:
        payload = self._store.get(self._session_key(session_id))
        return Session.from_dict(payload) if payload is not None else None

    # -- Agent ------------------------------------------------------------

    def create_agent(self, session_id: str, session_agent: SessionAgent, **kwargs: Any) -> None:
        self._store.upsert(
            self._agent_key(session_id, session_agent.agent_id),
            session_id,
            session_agent.to_dict(),
        )

    def read_agent(
        self, session_id: str, agent_id: str, **kwargs: Any
    ) -> SessionAgent | None:
        payload = self._store.get(self._agent_key(session_id, agent_id))
        return SessionAgent.from_dict(payload) if payload is not None else None

    def update_agent(self, session_id: str, session_agent: SessionAgent, **kwargs: Any) -> None:
        self.create_agent(session_id, session_agent)

    # -- Message ----------------------------------------------------------

    def create_message(
        self, session_id: str, agent_id: str, session_message: SessionMessage, **kwargs: Any
    ) -> None:
        self._store.upsert(
            self._message_key(session_id, agent_id, session_message.message_id),
            session_id,
            session_message.to_dict(),
        )

    def read_message(
        self, session_id: str, agent_id: str, message_id: int, **kwargs: Any
    ) -> SessionMessage | None:
        payload = self._store.get(self._message_key(session_id, agent_id, message_id))
        return SessionMessage.from_dict(payload) if payload is not None else None

    def update_message(
        self, session_id: str, agent_id: str, session_message: SessionMessage, **kwargs: Any
    ) -> None:
        self.create_message(session_id, agent_id, session_message)

    def list_messages(
        self,
        session_id: str,
        agent_id: str,
        limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> list[SessionMessage]:
        payloads = self._store.list_messages(
            session_id, agent_id, limit=limit, offset=offset
        )
        return [SessionMessage.from_dict(payload) for payload in payloads]

    # -- MultiAgent -------------------------------------------------------

    def create_multi_agent(self, session_id: str, multi_agent: Any, **kwargs: Any) -> None:
        self._store.upsert(
            self._multi_agent_key(session_id, multi_agent.id),
            session_id,
            multi_agent.serialize_state(),
        )

    def read_multi_agent(
        self, session_id: str, multi_agent_id: str, **kwargs: Any
    ) -> dict[str, Any] | None:
        return self._store.get(self._multi_agent_key(session_id, multi_agent_id))

    def update_multi_agent(self, session_id: str, multi_agent: Any, **kwargs: Any) -> None:
        self.create_multi_agent(session_id, multi_agent)
