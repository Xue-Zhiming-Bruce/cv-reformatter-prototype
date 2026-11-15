"""Instrument a real PDF parse so the shared wall-clock budget can be exercised."""

from __future__ import annotations

import time

import pytest


def slow_page_extraction(monkeypatch: pytest.MonkeyPatch, *, delay: float = 0.12) -> None:
    """Make every page-level extraction cost wall-clock time.

    The parse budget is elapsed time, so a page that costs time in an otherwise
    small file is what crosses it; nothing else about the parse changes.
    """
    import pdfplumber.page

    for name in ("extract_text", "extract_words"):
        original = getattr(pdfplumber.page.Page, name)

        def slow(self, *args, _original=original, **kwargs):
            time.sleep(delay)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(pdfplumber.page.Page, name, slow)
