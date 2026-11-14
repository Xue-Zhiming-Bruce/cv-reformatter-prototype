"""The one owner of native PDFium access in this process.

PDFium forbids simultaneous calls from different threads, even on unrelated
documents, so one process-wide lock covers each complete native lifetime
(open, render, bitmap, page, close). pypdfium2 releases native handles only by
garbage collection unless they are closed explicitly, so every lifetime closes
while it still holds that lock. Call :func:`native_lifetime` around the whole
lifetime and reach PDFium only through the ``pdfium`` name this module owns.

A missing pypdfium2 now fails when this module is imported rather than at the
first render, because the import above happens at module import time.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pypdfium2 as pdfium

__all__ = ("native_lifetime", "pdfium")

_LIFETIME_LOCK = threading.RLock()


@contextmanager
def native_lifetime() -> Iterator[None]:
    """Hold the process-wide PDFium lock for one complete native lifetime."""
    with _LIFETIME_LOCK:
        yield
