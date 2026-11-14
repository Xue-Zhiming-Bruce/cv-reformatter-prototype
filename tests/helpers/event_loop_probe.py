"""Probe the blocking-work boundary from a test.

``assert_health_answers`` proves a pending handler leaves the FastAPI event loop
free: a blocking parse/provider substitute waits on a ``threading.Event`` while a
concurrent ``GET /health`` must answer first. Comparing durations would not prove
the boundary, so the probe fails when the response does not arrive within a
bounded wait.

``occupied_blocking_work`` holds every slot for the body of a ``with`` block, so
a request inside it meets the saturated case.

``cancel_while_held`` and ``wait_until`` cover what off-loop work exposed:
cancelling a handler while its worker still runs, and the state that worker
reaches afterwards.

``paused_publication`` holds the first rename that publishes into one artifact
directory, so a test can look at what a reader sees inside the publication
window instead of racing it.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager, suppress
from pathlib import Path
from unittest.mock import patch

import httpx
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


def wait_until(predicate: Callable[[], bool], message: str) -> None:
    """Bounded wait for a state a worker thread reaches after the handler is gone."""
    deadline = time.monotonic() + HOLD_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(message)


@contextmanager
def paused_publication(
    artifact_dir: Path, entered: threading.Event, release: threading.Event
) -> Iterator[None]:
    """Hold the publication right after its first rename into ``artifact_dir``."""
    real_replace = os.replace

    def replace(source, destination):
        result = real_replace(source, destination)
        if not entered.is_set() and str(artifact_dir) in str(destination):
            entered.set()
            assert release.wait(HOLD_TIMEOUT_SECONDS), "the publication was never released"
        return result

    with patch.object(os, "replace", replace):
        yield


async def _request_until_cancelled(
    async_client: httpx.AsyncClient,
    request: Callable[[httpx.AsyncClient], Awaitable[None]],
    entered: threading.Event,
    release: threading.Event,
) -> None:
    task = asyncio.create_task(request(async_client))
    try:
        assert await asyncio.to_thread(
            entered.wait, HOLD_TIMEOUT_SECONDS
        ), "the blocking substitute never started"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def cancel_while_held(
    request: Callable[[httpx.AsyncClient], Awaitable[None]],
    *,
    entered: threading.Event,
    release: threading.Event,
) -> None:
    """Cancel one ASGI request while the worker behind it is still running.

    ``entered`` is set by the test's blocking substitute inside the worker, so
    the cancellation lands after the request dispatched its blocking work;
    ``release`` then frees that worker while nobody is waiting for it.
    """
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as async_client:
        await _request_until_cancelled(async_client, request, entered, release)
