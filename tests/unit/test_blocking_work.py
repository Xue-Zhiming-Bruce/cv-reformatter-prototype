"""Bounded capacity, saturation and slot release for app.blocking_work."""

from __future__ import annotations

import asyncio
import threading

import pytest

from app import blocking_work

HOLD_TIMEOUT_SECONDS = 15


async def _answer() -> str:
    return await blocking_work.run_blocking(lambda: "done")


def test_saturation_rejects_without_starting_the_call() -> None:
    """Every slot busy: the next caller is rejected instead of queued unbounded."""
    release, occupied = threading.Event(), threading.Event()
    started = threading.Event()
    lock = threading.Lock()
    running = 0

    def hold() -> None:
        nonlocal running
        with lock:
            running += 1
            if running == blocking_work.BLOCKING_WORK_CAPACITY:
                occupied.set()
        assert release.wait(HOLD_TIMEOUT_SECONDS), "the held slots were never released"

    async def occupy() -> None:
        capacity = blocking_work.BLOCKING_WORK_CAPACITY
        await asyncio.gather(*(blocking_work.run_blocking(hold) for _ in range(capacity)))

    holder = threading.Thread(target=lambda: asyncio.run(occupy()), daemon=True)
    holder.start()
    try:
        assert occupied.wait(HOLD_TIMEOUT_SECONDS), "the slots were never filled"

        def rejected() -> None:
            started.set()

        with pytest.raises(blocking_work.BlockingWorkSaturated):
            asyncio.run(blocking_work.run_blocking(rejected))
    finally:
        release.set()
        holder.join(HOLD_TIMEOUT_SECONDS)

    assert not started.is_set()
    assert asyncio.run(_answer()) == "done"


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
