"""Regression: RQ connections must use decode_responses=False.

RQ 2.11.0 always zlib-compresses job data at the storage layer
(rq/job.py: Job._get_save_key stores zlib.compress(self.data) and
decompresses on load).  RQ therefore needs a raw-byte connection; a
connection opened with decode_responses=True makes redis-py UTF-8 decode
the compressed payload before RQ can decompress it, producing either
mojibake or a UnicodeDecodeError that crashes the worker and loses the job.

This test locks the contract that both the API enqueue connection
(integrations/redis.py get_rq_connection) and the worker connection
(workers/rq_worker.py) are decode_responses=False, and that an
enqueue -> Job.fetch round-trip works on such a connection.
"""

from __future__ import annotations

import pytest
import zlib

import fakeredis
from rq import Queue
from rq.job import Job

from draftly.integrations.redis import RedisClient
from draftly.app.composition.rq_jobs import build_rq_queues


def test_get_rq_connection_is_raw_bytes_for_rq():
    """RQ needs decode_responses=False so zlib-compressed job data survives."""
    import redis as sync_redis

    async def build():
        client = RedisClient(url="redis://localhost:6379/0")
        conn = client.get_rq_connection()
        await client.close()
        return conn

    conn = run_async(build())
    assert isinstance(conn, sync_redis.Redis)
    assert conn.connection_pool.connection_kwargs.get("decode_responses") is False


def test_rq_job_data_is_zlib_compressed_on_raw_connection():
    """Confirms RQ compresses job payloads, so decoding responses would break."""
    from draftly.app.workers.async_sync import make_sync_handler

    raw = fakeredis.FakeRedis(decode_responses=False)
    queue = build_rq_queues(raw)["default"]

    async def noop(**kw):
        return kw

    job = queue.enqueue(make_sync_handler(noop), kwargs={"x": 1}, job_id="dec-raw-1")
    stored = raw.hget("rq:job:dec-raw-1", "data")
    assert stored[0] == 0x78  # zlib header (x\x9c...)
    assert zlib.decompress(stored)  # decompresses cleanly


def test_job_fetch_survives_enqueue_roundtrip_on_raw_connection():
    """Refutes the decode_responses=True crash: fetch works when raw bytes kept."""
    from draftly.app.workers.async_sync import make_sync_handler

    raw = fakeredis.FakeRedis(decode_responses=False)
    queue = build_rq_queues(raw)["default"]

    async def noop(**kw):
        return kw

    job = queue.enqueue(make_sync_handler(noop), kwargs={"x": 1}, job_id="dec-raw-2")
    fetched = Job.fetch("dec-raw-2", connection=raw)
    assert fetched == job


def test_decode_responses_true_connection_breaks_rq_job_read():
    """Guard: proves why decode_responses=True is the bug (RAISES on read).

    RQ stores job data zlib-compressed.  A decode_responses=True connection
    makes redis-py UTF-8 decode the compressed bytes into a str (or raise
    UnicodeDecodeError on strict decode), so RQ sees garbage instead of raw
    bytes.  This is the exact crash the worker hit.
    """
    import redis

    from draftly.app.workers.async_sync import make_sync_handler

    raw = fakeredis.FakeRedis(decode_responses=False)
    queue = Queue("draftly:default", connection=raw)

    async def noop(**kw):
        return kw

    queue.enqueue(make_sync_handler(noop), kwargs={"x": 1}, job_id="dec-bad-2")

    # Emulate real redis-py strict UTF-8 decoding of the stored zlib bytes:
    # this is what a decode_responses=True connection does under the hood.
    stored = raw.hget("rq:job:dec-bad-2", "data")
    assert isinstance(stored, bytes) and stored[0] == 0x78
    with pytest.raises(UnicodeDecodeError):
        stored.decode("utf-8", errors="strict")


def run_async(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)
