"""
Locks in the async-DB fix.

Before this, `run_db_async` existed and was documented as "ensures blocking
DB operations never stall the FastAPI async event loop" - but was called
from exactly one place (the event bus). Every agent tool and dashboard route
called blocking psycopg2 directly from inside an `async def`, which means
the agent runtime's `asyncio.gather` over read-only tools was providing no
actual concurrency at all, and any slow query froze every other in-flight
request (every other live voice call) for its full duration.

These tests assert the two properties that fix depends on:
  1. run_db_async actually runs work in parallel off the event loop.
  2. Its executor is bounded BELOW the psycopg2 connection-pool ceiling, so
     a load spike becomes backpressure (waiting for a worker) rather than
     PoolError("connection pool exhausted") - psycopg2's ThreadedConnectionPool
     raises rather than blocking when it runs dry.
"""
import asyncio
import time

import pytest

from app.db import database as db_module
from app.db.database import run_db_async


def _sleep_and_return(marker: str, seconds: float = 0.3) -> str:
    time.sleep(seconds)  # stands in for a blocking DB round trip
    return marker


async def test_run_db_async_actually_runs_in_parallel():
    """Four 0.3s blocking calls gathered through run_db_async must finish in
    well under the 1.2s they'd take if they were serialized on the event
    loop (which is exactly what a plain sync call inside `async def` does)."""
    started = time.perf_counter()
    results = await asyncio.gather(*(run_db_async(_sleep_and_return, f"r{i}") for i in range(4)))
    elapsed = time.perf_counter() - started

    assert results == ["r0", "r1", "r2", "r3"]
    assert elapsed < 0.75, (
        f"4x0.3s blocking calls took {elapsed:.2f}s - they are being serialized, "
        "meaning run_db_async is no longer offloading to worker threads."
    )


async def test_run_db_async_does_not_block_the_event_loop():
    """A blocking DB call must not stop other coroutines from making
    progress while it runs - the property that keeps one slow query from
    freezing every concurrent voice call."""
    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(10):
            await asyncio.sleep(0.02)
            ticks += 1

    await asyncio.gather(run_db_async(_sleep_and_return, "slow", 0.25), ticker())
    assert ticks == 10, f"event loop was starved during a blocking DB call (only {ticks}/10 ticks ran)"


def test_db_executor_is_bounded_below_the_connection_pool():
    """The worker pool must stay under the Postgres pool's max connections.
    psycopg2's ThreadedConnectionPool.getconn() raises when exhausted rather
    than waiting, so more DB threads than connections turns a load spike
    into hard errors instead of a queue."""
    assert db_module._DB_MAX_WORKERS < 20, (
        "DB worker pool must be smaller than the ThreadedConnectionPool ceiling (20) "
        "so excess concurrent work queues for a thread instead of exhausting the pool."
    )
    assert db_module._db_executor._max_workers == db_module._DB_MAX_WORKERS
