import asyncio

import pytest
from fakeredis import aioredis


@pytest.fixture
def fake_redis():
    return aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_issue_returns_ticket_string(fake_redis):
    from draftly.integrations.ticket_store import RedisTicketStore

    store = RedisTicketStore(fake_redis, ttl_seconds=60)
    ticket = await store.issue("run-123", org_id="org-abc")
    assert isinstance(ticket, str)
    assert len(ticket) > 10


@pytest.mark.asyncio
async def test_consume_returns_run_and_org(fake_redis):
    from draftly.integrations.ticket_store import RedisTicketStore

    store = RedisTicketStore(fake_redis, ttl_seconds=60)
    ticket = await store.issue("run-123", org_id="org-abc")
    result = await store.consume(ticket)
    assert result == ("run-123", "org-abc")


@pytest.mark.asyncio
async def test_consume_single_use(fake_redis):
    from draftly.integrations.ticket_store import RedisTicketStore

    store = RedisTicketStore(fake_redis, ttl_seconds=60)
    ticket = await store.issue("run-123", org_id="org-abc")
    await store.consume(ticket)
    result = await store.consume(ticket)
    assert result is None


@pytest.mark.asyncio
async def test_consume_expired_returns_none(fake_redis):
    from draftly.integrations.ticket_store import RedisTicketStore

    store = RedisTicketStore(fake_redis, ttl_seconds=0)
    ticket = await store.issue("run-123", org_id="org-abc")
    await asyncio.sleep(1.1)
    result = await store.consume(ticket)
    assert result is None
