"""Local font discovery used by the Pipeline render-plan compiler."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from collections import Counter
from statistics import median
from typing import Any
import re


FONT_SEARCH_DIRS = (
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
)
FAMILY_ALIASES = {
    "charterbt": "Charter",
    "avenirnextcyr": "Avenir Next",
    "timesnewromanpsmt": "Times New Roman",
    "timesnewromanps": "Times New Roman",
}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


@lru_cache(maxsize=1)
def installed_font_families() -> frozenset[str]:
    families: set[str] = set()
    for directory in FONT_SEARCH_DIRS:
        if directory.is_dir():
            families.update(
                _key(path.stem)
                for path in directory.rglob("*")
                if path.suffix.casefold() in {".ttf", ".otf", ".ttc"}
            )
    return frozenset(families)


def font_is_installed(family: str) -> bool:
    return font_file_for_family(family) is not None


@lru_cache(maxsize=32)
def font_file_for_family(family: str) -> Path | None:
    """Resolve an actual local face; never treat fontconfig fallback as installed."""
    key = _key(FAMILY_ALIASES.get(_key(family), family))
    for directory in FONT_SEARCH_DIRS:
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.suffix.casefold() in {".ttf", ".otf", ".ttc"} and _key(path.stem) == key:
                    return path
    return None


def _family(raw: str) -> tuple[str, bool]:
    key = _key(raw)
    if key.startswith("timesnewroman"):
        return "Times New Roman", False
    if key.startswith("arial"):
        return "Arial", False
    if key.startswith("charterbt"):
        return "Charter", True
    if key.startswith("avenirnextcyr"):
        return "Avenir Next", True
    return raw, False


def _style(blocks: list[Any]) -> dict[str, Any]:
    styles = Counter(
        (block.font_family, round(block.font_size_pt, 2), bool(block.bold))
        for block in blocks for _ in range(max(1, len(block.text or "")))
    )
    raw, size, bold = styles.most_common(1)[0][0]
    family, alias_substitution = _family(raw)
    font_file = font_file_for_family(family)
    if font_file is None:
        family = "Arial"
        font_file = font_file_for_family(family)
        alias_substitution = True
    bold_file = (font_file_for_family(f"{family} Bold") or (font_file if font_file and font_file.suffix.casefold() == ".ttc" else None)) if bold else font_file
    return {
        "target_family": raw, "required_family": family,
        "font_size_pt": size, "bold": bold,
        "font_file": str(font_file) if font_file else None,
        "bold_face_file": str(bold_file) if bold_file else None,
        "substitution": alias_substitution,
        "visual_review_required": alias_substitution or font_file is None or (bold and bold_file is None),
    }


def target_font_roles(evidence: Any, labels: list[Any]) -> dict[str, dict[str, Any]]:
    """Character-weighted target typography for the roles Lane B renders."""
    blocks = list(evidence.text_blocks)
    label_texts = {_key(label.text) for label in labels if label.kind == "section_heading"}
    name = max((b for b in blocks if b.page_number == 1), key=lambda b: b.font_size_pt)
    section = [b for b in blocks if _key(b.text) in label_texts]
    body = [b for b in blocks if not b.bold and b is not name]
    body_style = _style(body)
    entry = [
        b for b in blocks if b.bold and b is not name and b not in section
        and b.font_size_pt <= body_style["font_size_pt"] + 1
    ]
    roles = {"body": body_style, "name": _style([name])}
    if section:
        roles["section_heading"] = _style(section)
    if entry:
        roles["entry_heading"] = _style(entry)
    second = blocks[1] if len(blocks) > 1 else None
    if second and second.page_number == 1 and second.text.isupper() and second.font_size_pt > body_style["font_size_pt"] + 2:
        roles["name_secondary"] = _style([second])
    return roles


def _pdf_family(raw: str) -> str:
    key = _key(raw.split("+", 1)[-1])
    for prefix, family in (
        ("timesnewroman", "Times New Roman"), ("arial", "Arial"),
        ("xcharter", "Charter"), ("charter", "Charter"),
        ("avenirnext", "Avenir Next"), ("helvetica", "Helvetica"),
    ):
        if key.startswith(prefix):
            return family
    return raw.split("+", 1)[-1].split("-", 1)[0]


def pdf_font_role_gate(pdf: Path, roles: dict[str, dict[str, Any]], candidate: Any, labels: list[Any]) -> dict[str, Any]:
    """Read actual PDF glyph faces, weights, and sizes for each rendered role."""
    import pdfplumber

    with pdfplumber.open(pdf) as document:
        def search(text: str) -> list[dict[str, Any]]:
            for page in document.pages:
                found = page.search(re.escape(text), regex=True, case=False)
                if found:
                    return found[0]["chars"]
            return []

        all_chars = [char for page in document.pages for char in page.chars if char.get("text", "").strip()]
        top = [char for char in document.pages[0].chars if char.get("text", "").strip() and char["top"] < document.pages[0].height * .25]
        largest = max((char["size"] for char in top), default=0)
        samples: dict[str, list[dict[str, Any]]] = {
            "name": [char for char in top if abs(char["size"] - largest) < .2],
        }
        bullet = next((leaf.text for leaf in candidate.leaves if leaf.kind == "work_bullet" and leaf.text), "")
        samples["body"] = search(bullet.lstrip("• ")[:24]) if bullet else all_chars
        entry = next((leaf.text for leaf in candidate.leaves if leaf.kind == "work_entry" and leaf.text), "")
        samples["entry_heading"] = search(entry) if entry else []
        samples["section_heading"] = next((chars for label in labels if (chars := search(label.text))), [])
        profile = candidate.profile
        samples["name_secondary"] = search(profile.family_name) if profile and profile.family_name else []
        results: dict[str, Any] = {}
        for role, expected in roles.items():
            chars = samples.get(role) or []
            if not chars and ((role == "section_heading" and not labels) or (role == "name_secondary" and (not profile or not profile.family_name))):
                continue
            faces = Counter((_pdf_family(char["fontname"]), bool(re.search(r"bold|demi|black", char["fontname"], re.I))) for char in chars)
            actual_family, actual_bold = faces.most_common(1)[0][0] if faces else (None, None)
            actual_size = round(median(char["size"] for char in chars), 2) if chars else None
            passed = actual_family == expected["required_family"] and actual_bold == expected["bold"] and actual_size is not None and abs(actual_size - expected["font_size_pt"]) <= .6
            results[role] = {
                "passed": passed,
                "expected": expected,
                "actual_family": actual_family,
                "actual_bold": actual_bold,
                "actual_size_pt": actual_size,
                "glyphs_checked": len(chars),
            }
    return {
        "passed": bool(results) and all(item["passed"] for item in results.values()),
        "roles": results,
        "mismatches": [role for role, item in results.items() if not item["passed"]],
        "substitutions": [role for role, item in results.items() if item["expected"]["visual_review_required"]],
    }
