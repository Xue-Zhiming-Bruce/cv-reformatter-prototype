"""Persistence seam for the conversion execution envelope (ADR 0010).

PostgreSQL holds the envelope and its append-only event history; the offline
lane and the unit tests use one bounded in-process store per process.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Protocol


class EnvelopeUnavailable(RuntimeError):
    """Envelope storage could not be read or written, so no paid stage may start."""

    error_code = "envelope_unavailable"


class ExecutionEnvelopeStore(Protocol):
    """Persistence seam: the envelope record plus its append-only event history."""

    def create_envelope(self, body: dict[str, Any]) -> None: ...
    def get_envelope(self, conversion_id: str) -> dict[str, Any] | None: ...
    def find_open(self, artifact_id: str) -> dict[str, Any] | None: ...
    def set_state(self, conversion_id: str, state: str, closed_at: datetime | None) -> None: ...
    def append_event(self, conversion_id: str, event: dict[str, Any]) -> None: ...
    def list_events(self, conversion_id: str) -> list[dict[str, Any]]: ...


class InMemoryExecutionEnvelopeStore:
    """Process-wide store for the offline lane and unit tests: bounded but not durable."""

    def __init__(self) -> None:
        self.envelopes: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}

    def create_envelope(self, body: dict[str, Any]) -> None:
        self.envelopes[str(body["conversion_id"])] = dict(body)

    def get_envelope(self, conversion_id: str) -> dict[str, Any] | None:
        return self.envelopes.get(conversion_id)

    def find_open(self, artifact_id: str) -> dict[str, Any] | None:
        found = [body for body in self.envelopes.values()
                 if body["artifact_id"] == artifact_id and body["state"] in {"open", "exhausted"}]
        return max(found, key=lambda body: body["opened_at"]) if found else None

    def set_state(self, conversion_id: str, state: str, closed_at: datetime | None) -> None:
        self.envelopes[conversion_id].update(state=state, closed_at=closed_at)

    def append_event(self, conversion_id: str, event: dict[str, Any]) -> None:
        history = self.events.setdefault(conversion_id, [])
        history.append({**event, "sequence": len(history) + 1})

    def list_events(self, conversion_id: str) -> list[dict[str, Any]]:
        return [dict(event) for event in self.events.get(conversion_id, [])]


_MEMORY_STORE = InMemoryExecutionEnvelopeStore()


def memory_store() -> InMemoryExecutionEnvelopeStore:
    """The empty in-process store: one envelope per artifact, no history leak between tests."""
    _MEMORY_STORE.envelopes.clear()
    _MEMORY_STORE.events.clear()
    return _MEMORY_STORE


def default_store() -> ExecutionEnvelopeStore:
    """PostgreSQL when configured, else the bounded in-process store."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return _MEMORY_STORE
    from app.storage.postgres import PostgresExecutionEnvelopeStore, postgres_engine

    return PostgresExecutionEnvelopeStore(postgres_engine(url))
