"""The one bounded boundary for synchronous work called from async handlers.

FastAPI runs an ``async def`` handler on the event loop, so a synchronous parse,
SDK call or polling loop inside it stops every other request for as long as it
runs. :func:`run_blocking` hands one such call to a fixed pool of threads and
answers saturation instead of queueing without limit: when every slot is busy
the caller gets :class:`BlockingWorkSaturated` and no work is started.

The pool is process-wide and bounded by ``API_BLOCKING_WORK_CAPACITY`` (read
once, at import): an integer from 1 to 64, defaulting to 4. An invalid value
fails loudly at startup instead of silently resizing the boundary.

Cancelling the awaiting handler — server shutdown or an explicit task
cancellation, since a non-streaming JSON handler is not cancelled by a client
disconnect — cannot undo work already dispatched: a provider request in flight
runs to completion and its result is discarded. Nothing here retries; repeating
paid work stays an explicit caller decision made outside this module.

PDFium keeps its own process-wide serialization in ``app/pdfium_native.py``;
this module adds capacity, not a second native lock.

The nesting rule is one level: offloaded work is leaf work and never calls back
into this module, and a handler holds at most one slot at a time — the process
path runs its parse hop and then its extraction hop one after the other, each
released before the next starts. A single request therefore cannot occupy more
than one slot, so no pool size can deadlock on nesting.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, TypeVar

__all__ = (
    "BLOCKING_WORK_CAPACITY",
    "MAX_BLOCKING_WORK_CAPACITY",
    "MIN_BLOCKING_WORK_CAPACITY",
    "BlockingWorkSaturated",
    "run_blocking",
)

T = TypeVar("T")

MIN_BLOCKING_WORK_CAPACITY = 1
MAX_BLOCKING_WORK_CAPACITY = 64


def _capacity_from(raw: str | None) -> int:
    """Read the configured capacity, naming the setting and range when invalid."""
    value = "4" if raw is None or not raw.strip() else raw.strip()
    try:
        capacity = int(value)
    except ValueError:
        capacity = None
    if capacity is None or not MIN_BLOCKING_WORK_CAPACITY <= capacity <= MAX_BLOCKING_WORK_CAPACITY:
        raise ValueError(
            "API_BLOCKING_WORK_CAPACITY must be an integer from "
            f"{MIN_BLOCKING_WORK_CAPACITY} to {MAX_BLOCKING_WORK_CAPACITY}, got {raw!r}"
        )
    return capacity


BLOCKING_WORK_CAPACITY = _capacity_from(os.getenv("API_BLOCKING_WORK_CAPACITY"))

_BUSY_SLOTS = threading.BoundedSemaphore(BLOCKING_WORK_CAPACITY)
_WORKERS = ThreadPoolExecutor(
    max_workers=BLOCKING_WORK_CAPACITY,
    thread_name_prefix="blocking-work",
)


class BlockingWorkSaturated(RuntimeError):
    """Every blocking slot is busy, so the call was not started."""


def _release_slot(_future: Future[Any]) -> None:
    # Tied to the worker finishing, not to the awaiter, so a cancelled request
    # cannot free a slot while its thread is still running.
    _BUSY_SLOTS.release()


async def run_blocking(call: Callable[..., T], /, *args: Any) -> T:
    """Run one synchronous call off the event loop, or raise when saturated."""
    if not _BUSY_SLOTS.acquire(blocking=False):
        raise BlockingWorkSaturated(
            f"all {BLOCKING_WORK_CAPACITY} blocking-work slots are in use"
        )
    try:
        future = _WORKERS.submit(call, *args)
    except BaseException:
        _BUSY_SLOTS.release()
        raise
    future.add_done_callback(_release_slot)
    return await asyncio.wrap_future(future)
