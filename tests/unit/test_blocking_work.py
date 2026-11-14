"""Bounded capacity, saturation, cancellation and slot release for app.blocking_work."""

from __future__ import annotations

import asyncio
import threading

import pytest

from app import blocking_work
from tests.helpers.event_loop_probe import HOLD_TIMEOUT_SECONDS, occupied_blocking_work


async def _answer() -> str:
    return await blocking_work.run_blocking(lambda: "done")


def test_saturation_rejects_without_starting_the_call() -> None:
    """Every slot busy: the next caller is rejected instead of queued unbounded."""
    started = threading.Event()

    def rejected() -> None:
        started.set()

    with occupied_blocking_work():
        with pytest.raises(blocking_work.BlockingWorkSaturated):
            asyncio.run(blocking_work.run_blocking(rejected))
        assert not started.is_set()

    assert asyncio.run(_answer()) == "done"


async def _free_slot_call() -> str:
    """Wait for a free slot without assuming an ordering between threads."""

    async def attempt() -> str:
        while True:
            try:
                return await blocking_work.run_blocking(lambda: "done")
            except blocking_work.BlockingWorkSaturated:
                await asyncio.sleep(0.001)

    return await asyncio.wait_for(attempt(), timeout=HOLD_TIMEOUT_SECONDS)


def _hold_every_slot(
    release: threading.Event, occupied: threading.Event
) -> list[asyncio.Future[str]]:
    """Start one held call per slot; ``occupied`` is set once all are running."""
    capacity = blocking_work.BLOCKING_WORK_CAPACITY
    running = 0
    lock = threading.Lock()

    def hold() -> str:
        nonlocal running
        with lock:
            running += 1
            if running == capacity:
                occupied.set()
        assert release.wait(HOLD_TIMEOUT_SECONDS), "the workers were never released"
        return "held"

    return [
        asyncio.ensure_future(blocking_work.run_blocking(hold))
        for _ in range(capacity)
    ]


async def _cancel_one_awaiter_while_its_worker_runs(
    held: list[asyncio.Future[str]], release: threading.Event
) -> None:
    """Cancel one awaiter, then prove its worker's slot is still held."""
    held[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await held[0]

    with pytest.raises(blocking_work.BlockingWorkSaturated):
        await blocking_work.run_blocking(lambda: "extra")

    release.set()
    await asyncio.gather(*held[1:])


def test_cancelling_the_awaiter_keeps_the_slot_until_the_worker_finishes() -> None:
    """A cancelled awaiter must not free a slot whose worker is still running."""
    release, occupied = threading.Event(), threading.Event()

    async def scenario() -> None:
        held = _hold_every_slot(release, occupied)
        try:
            assert await asyncio.to_thread(occupied.wait, HOLD_TIMEOUT_SECONDS)
            await _cancel_one_awaiter_while_its_worker_runs(held, release)
            assert await _free_slot_call() == "done"
        finally:
            release.set()

    asyncio.run(scenario())


def test_invalid_capacity_names_the_setting_and_range() -> None:
    """A bad API_BLOCKING_WORK_CAPACITY fails at startup instead of a bare ValueError."""
    for raw in ("abc", "0", "-2", "65", "3.5"):
        with pytest.raises(ValueError, match="API_BLOCKING_WORK_CAPACITY.*1 to 64"):
            blocking_work._capacity_from(raw)

    assert blocking_work._capacity_from("1") == 1
    assert blocking_work._capacity_from(None) == 4
    assert blocking_work._capacity_from(" 8 ") == 8


def test_a_failed_call_releases_its_slot() -> None:
    """Slots come back after worker exceptions, so capacity is not lost."""

    def boom() -> None:
        raise RuntimeError("worker failure")

    for _ in range(blocking_work.BLOCKING_WORK_CAPACITY * 2):
        with pytest.raises(RuntimeError):
            asyncio.run(blocking_work.run_blocking(boom))

    assert asyncio.run(_answer()) == "done"


def test_full_capacity_of_two_hop_requests_still_completes() -> None:
    """The deepest real shape: one request does two hops (parse, then extraction).

    With every slot busy, a hop that kept its slot would leave the second hop of
    every request waiting for a slot that never frees. The bounded wait turns
    that into a loud failure instead of a hung run.
    """
    capacity = blocking_work.BLOCKING_WORK_CAPACITY
    hops: list[str] = []
    lock = threading.Lock()

    def hop(name: str) -> None:
        with lock:
            hops.append(name)

    async def request() -> None:
        await blocking_work.run_blocking(hop, "parse")
        await blocking_work.run_blocking(hop, "extraction")

    async def exercise() -> None:
        await asyncio.wait_for(
            asyncio.gather(*(request() for _ in range(capacity))),
            timeout=HOLD_TIMEOUT_SECONDS,
        )

    asyncio.run(exercise())

    assert len(hops) == capacity * 2
