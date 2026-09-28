"""Text-normalisation primitives shared by every layer of the library.

``_norm`` was split out of ``c2_renderer`` (the delivery-gate module) because it
is a leaf operation, and importing it from the gate module closed an import
cycle:

    docx_lane.build -> c2_renderer -> resume_layout.html
                    -> resume_layout.plan -> docx_lane.build

This module imports nothing from ``tests.experiments`` and must stay that way so
that anything may depend on it.
"""
from __future__ import annotations

import re
import hashlib
import html as html_lib
import statistics
from html.parser import HTMLParser
from pathlib import Path


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace(":", " ")).strip().casefold()


def _median(values: list[float]) -> float | None:
    return _round(statistics.median(values)) if values else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _html_text(document: str) -> tuple[str, list[str]]:
    parser = _TextParser()
    parser.feed(document)
    text = html_lib.unescape(" ".join(parser.parts))
    empty = [
        str(section["name"])
        for section in parser.sections
        if not re.sub(r"\s+", "", " ".join(section["parts"]))
    ]
    return re.sub(r"\s+", " ", text).strip(), empty


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts = []
        self.sections = []
        self._section = None
        self._depth = 0
        self._ignored = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"style", "script", "title"}:
            self._ignored = tag
            return
        if self._ignored:
            return
        values = dict(attrs)
        if self._section is not None:
            self._depth += 1
            return
        if values.get("data-section") is not None:
            self._section = {"name": values["data-section"], "parts": []}
            self._depth = 1
            return

    def handle_endtag(self, tag: str) -> None:
        if tag == self._ignored:
            self._ignored = None
            return
        if self._ignored:
            return
        if self._section is None:
            return
        self._depth -= 1
        if self._depth == 0:
            self.sections.append(self._section)
            self._section = None
            return

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        self.parts.append(data)
        if self._section is not None:
            self._section["parts"].append(data)
            return


def _hyphen_free(value: str) -> str:
    """Owner decision 2026-09-09 (F->E matrix postmortem): Chrome breaks words at
    hyphens across lines ("Scikit-" / "learn") and read_pdf_text merges the
    fragments WITHOUT the hyphen ("Scikitlearn"), so hyphen-bearing source lines
    are permanently reported missing from the PDF even though every glyph
    rendered. PDF-side coverage therefore compares hyphen-free text on both
    sides; the HTML-side coverage stays strict (verbatim, hyphens included)."""
    return value.replace("-", "")
