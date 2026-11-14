"""Prove that a pending handler leaves the FastAPI event loop free.

A blocking parse/provider substitute waits on a ``threading.Event`` while the
probe below asks for ``GET /health``. Comparing durations would not prove the
boundary, so the probe fails when the concurrent response does not arrive within
a bounded wait.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

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
