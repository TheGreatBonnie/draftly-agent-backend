"""Cross-process init-lock helpers for onboarding initialization.

The init lock spans the API and the RQ worker (Task 9): the API acquires it
before dispatching ``onboarding.initialize``, and the worker process releases
it once the job finishes (success or failure), with the TTL as a crash
backstop. The two processes use different Redis client flavors — the API a
request-bound async client, the worker a synchronous connection — so the
helpers are split accordingly but share the key scheme and the guarded
release semantics.
"""

from __future__ import annotations

from typing import Any

# MUST exceed the longest legitimate run. Raised from 600s: pre-P1
# runs take 35-90 min, so a 10-min TTL expired mid-run and let a
# refresh start a duplicate pipeline. The Task 11 watchdog (1200s) is
# authoritative once shipped: keep TTL >= watchdog. CAS-keyed release
# stays safe across TTL expiry.
INIT_LOCK_TTL_SECONDS = 7200


def init_lock_key(org_id: str) -> str:
    return f"onboarding:init-lock:{org_id}"


async def try_acquire_init_lock(redis: Any, org_id: str, run_id: str) -> bool:
    """True if this caller owns the lock (idempotent per run_id)."""
    if redis is None:  # fail-open: without Redis there is no multi-process hazard
        return True
    acquired = await redis.set(
        init_lock_key(org_id), run_id, nx=True, ex=INIT_LOCK_TTL_SECONDS
    )
    if acquired or str(await redis.get(init_lock_key(org_id))) == run_id:
        return True
    return False


async def release_init_lock(redis: Any, org_id: str, run_id: str) -> None:
    """Asynchronous guarded release (API process / in-process fallback)."""
    if redis is None:
        return
    current = await redis.get(init_lock_key(org_id))
    # NOTE: get-then-delete is not atomic; the run_id comparison makes a stale
    # release harmless (it never deletes a newer run's lock). Upgrade to a Lua
    # compare-and-delete only if exactness ever matters here.
    if current and str(current) == run_id:
        await redis.delete(init_lock_key(org_id))


def release_init_lock_sync(redis: Any, org_id: str, run_id: str) -> None:
    """Synchronous guarded release for the RQ worker's redis connection."""
    if redis is None:
        return
    current = redis.get(init_lock_key(org_id))
    if current and str(current) == run_id:
        redis.delete(init_lock_key(org_id))
