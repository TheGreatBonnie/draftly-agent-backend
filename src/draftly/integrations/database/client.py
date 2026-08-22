from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import asyncpg


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
        return cast(str, await conn.execute(query, *args))

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
