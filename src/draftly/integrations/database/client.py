from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import asyncpg


async def _register_json_codecs(conn: asyncpg.Connection) -> None:
    """Register ``json``/``jsonb`` decoders for a connection.

    asyncpg's default ``jsonb`` codec decodes the column to a raw JSON *string*
    unless a decoder is registered via ``set_type_codec``. JSONB columns such
    as ``evaluations.metrics``/``evaluations.failures`` would then surface to
    consumers as JSON text instead of dict/list objects, which is why the
    evaluation detail page saw empty metrics. Registering ``json.loads`` as the
    decoder (``format="text"``) turns the wire value back into containers so
    all JSONB consumers receive objects.

    Passed to ``asyncpg.create_pool(init=...)`` so every pooled connection runs
    it on acquire.
    """
    for typename in ("jsonb", "json"):
        await conn.set_type_codec(
            typename,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
            format="text",
        )


class DatabaseClient:
    """
    Async NeonDB (Postgres) connection manager backed by an asyncpg pool.

    Mirrors agent/src/database.py: one lazy pool, serializable
    transactions, and conn-scoped helpers for use inside a
    transaction.
    """

    def __init__(
        self,
        database_url: str | None = None,
        *,
        pool_min_size: int = 2,
        pool_max_size: int = 10,
        command_timeout: int = 30,
    ) -> None:
        self.database_url = (
            database_url or os.environ.get("NEON_DATABASE_URL") or os.environ["DATABASE_URL"]
        )
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self.command_timeout = command_timeout

        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._pool is not None:
            return

        async with self._lock:
            if self._pool is not None:
                return

            self._pool = await asyncpg.create_pool(
                dsn=self.database_url,
                min_size=self.pool_min_size,
                max_size=self.pool_max_size,
                command_timeout=self.command_timeout,
                init=_register_json_codecs,
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _acquire(self) -> asyncpg.Connection:
        await self.start()

        assert self._pool is not None

        return await self._pool.acquire()

    async def execute(self, query: str, *args: Any) -> str:
        conn = await self._acquire()

        try:
            return await self.execute_conn(conn, query, *args)

        finally:
            if self._pool is not None:
                await self._pool.release(conn)

    async def fetch_one(self, query: str, *args: Any) -> Any:
        conn = await self._acquire()

        try:
            return await self.fetch_one_conn(conn, query, *args)

        finally:
            if self._pool is not None:
                await self._pool.release(conn)

    async def fetch_all(self, query: str, *args: Any) -> list[Any]:
        conn = await self._acquire()

        try:
            return await self.fetch_all_conn(conn, query, *args)

        finally:
            if self._pool is not None:
                await self._pool.release(conn)

    @staticmethod
    async def execute_conn(conn: asyncpg.Connection, query: str, *args: Any) -> str:
        return await conn.execute(query, *args)

    @staticmethod
    async def fetch_one_conn(conn: asyncpg.Connection, query: str, *args: Any) -> Any:
        return await conn.fetchrow(query, *args)

    @staticmethod
    async def fetch_all_conn(conn: asyncpg.Connection, query: str, *args: Any) -> list[Any]:
        return cast(list[Any], await conn.fetch(query, *args))

    @asynccontextmanager
    async def transaction(
        self,
        *,
        isolation: str = "read_committed",
    ) -> AsyncIterator[asyncpg.Connection]:
        conn = await self._acquire()

        try:
            async with conn.transaction(isolation=isolation):
                yield conn

        finally:
            if self._pool is not None:
                await self._pool.release(conn)
