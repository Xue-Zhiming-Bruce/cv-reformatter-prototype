"""Shared pipeline fonts implementation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_FONT_SEARCH_DIRS = (
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
)

FAMILY_ALIASES = {"charter bt": "charter"}

@lru_cache(maxsize=1)
def installed_font_families() -> frozenset[str]:
    """Lowercase stems of locally installed font files (no network access)."""
    families: set[str] = set()
    for directory in _FONT_SEARCH_DIRS:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.suffix.casefold() in {".ttf", ".otf", ".ttc"}:
                families.add(path.stem.casefold())
    return frozenset(families)

def _font_is_installed(family: str) -> bool:
    key = family.casefold().replace(" ", "")
    installed = installed_font_families()
    if key in installed:
        return True
    alias = FAMILY_ALIASES.get(key)
    if alias and alias in installed:
        return True
    first = key.split("_")[0] if "_" in key else None
    return bool(first) and first in installed
