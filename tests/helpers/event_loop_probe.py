"""Probe the blocking-work boundary from a test.

``assert_health_answers`` proves a pending handler leaves the FastAPI event loop
free: a blocking parse/provider substitute waits on a ``threading.Event`` while a
concurrent ``GET /health`` must answer first. Comparing durations would not prove
the boundary, so the probe fails when the response does not arrive within a
bounded wait.

``occupied_blocking_work`` holds every slot for the body of a ``with`` block, so
a request inside it meets the saturated case.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

HOLD_TIMEOUT_SECONDS = 15
PROBE_TIMEOUT_SECONDS = 15


def assert_health_answers(client: TestClient, pool: ThreadPoolExecutor) -> None:
    """Fail unless a concurrent health request answers while work is pending.

    ``client`` must be entered as a context manager so both requests share the
    one event loop the blocking work is supposed to leave free.
    """
    probe = pool.submit(client.get, "/health")
    try:
        response = probe.result(timeout=PROBE_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        pytest.fail(
            "a concurrent GET /health waited for the pending substitute: the "
            "handler is still running synchronous work on the event loop"
        )
    assert response.json() == {"status": "ok"}


@contextmanager
def occupied_blocking_work() -> Iterator[None]:
    """Hold every blocking-work slot until this block exits."""
    from app import blocking_work

    capacity = blocking_work.BLOCKING_WORK_CAPACITY
    release, occupied = threading.Event(), threading.Event()
    lock = threading.Lock()
    running = 0

    def hold() -> None:
        nonlocal running
        with lock:
            running += 1
            if running == capacity:
                occupied.set()
        assert release.wait(HOLD_TIMEOUT_SECONDS), "the held slots were never released"

    async def occupy() -> None:
        await asyncio.gather(*(blocking_work.run_blocking(hold) for _ in range(capacity)))

    holder = threading.Thread(target=lambda: asyncio.run(occupy()), daemon=True)
    holder.start()
    try:
        if not occupied.wait(HOLD_TIMEOUT_SECONDS):
            pytest.fail("the blocking-work slots were never filled")
        yield
    finally:
        release.set()
        holder.join(HOLD_TIMEOUT_SECONDS)
