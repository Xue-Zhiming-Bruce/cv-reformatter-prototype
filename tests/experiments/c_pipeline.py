"""Run the C1 candidate-blind header topology experiment beside A and B."""

from __future__ import annotations

import argparse
import collections
import os
import re
import shutil
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Callable, Literal

from lxml import html as lxml_html
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from tests.experiments.a_pipeline import ROOT, _pinned_chrome_environment, _requested_font_families
from tests.experiments.b_pipeline import DEFAULT_SOURCE, DEFAULT_TARGET, FinalReview, _page_text_lines, source_header_units
from tests.experiments.refinement import GateFailure, HardGateResult
from tests.experiments.fill_plan import (
    _section_semantic,
    analyze_candidate_provenance,
    normalize_section_headings,
    source_blocks,
    source_lines,
)

SlotKind = Literal["name", "title", "tagline", "location", "phone", "envelope", "github", "linkedin"]
# Owner ruling 2026-09-11 (E→D schema boundary, option B): the D-target header
# topology is name/contact/tagline only — the interests bar is a section row
# (bar_section), never a topology row.
TargetRole = Literal["name", "location", "contact", "tagline", "extension"]


class HeaderRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_role: TargetRole
    slots: list[SlotKind] = Field(min_length=1)
    alignment: Literal["left", "center", "right"]


class HeaderTopology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    rows: list[HeaderRow] = Field(min_length=1, max_length=8)


class TopologyCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[HeaderTopology] = Field(min_length=1, max_length=3)


class HeaderScaffold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["name", "location", "contact", "tagline", "bar_section"]
    top_pt: float = Field(ge=0)
    x0_pt: float = Field(ge=0)
    x1_pt: float = Field(gt=0)
    evidence_ids: list[str] = Field(min_length=1)
    # bar_section rows are compiler-owned section geometry (owner option B):
    # they carry no slot and are never topology rows, so slots may be empty.
    slots: list[SlotKind] = Field(default_factory=list)
    alignment: Literal["left", "center", "right"]
    # Per-row measured typography (E→D cold start, 2026-09-11): each header
    # row compiles from the style of ITS OWN measured target line, replacing
    # the positional group heuristic that only held where the corpus made the
    # first non-bold group the contact style. None = no measurable style on
    # that line (compiler falls back to the role heuristic).
    font_family: str | None = None
    font_size_pt: float | None = None
    line_height_pt: float | None = None
    bold: bool | None = None


class FitParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offset_pt: float = Field(default=0, ge=-36, le=144)
    gaps_pt: list[float] = Field(default_factory=list)


class BodyHeadingScaffold(BaseModel):
    """Measured presentation evidence for one target section heading.

    `verbatim` is the target heading text kept for matching and audit only;
    it is never compiled into an output document (headings are verbatim SOURCE
    content, filled by the Filler).
    """

    model_config = ConfigDict(extra="forbid")

    verbatim: str
    page: int
    top_pt: float
    x0_pt: float
    x1_pt: float
    font_height_pt: float
    font_size_pt: float
    line_height_pt: float
    bold: bool
    font_family: str
    rule_top_pt: float | None = None
    rule_gap_above_pt: float | None = None
    rule_gap_below_pt: float | None = None
    rule_stroke_pt: float | None = None
    rule_color_hex: str | None = None
    content_gap_below_pt: float | None = None
    rule_below_gap_pt: float | None = None
    rule_below_x0_pt: float | None = None
    rule_below_x1_pt: float | None = None
    rule_below_stroke_pt: float | None = None
    rule_below_color_hex: str | None = None
    post_rule_content_gap_pt: float | None = None
    evidence_ids: list[str] = Field(min_length=1)


class BodyEntryScaffold(BaseModel):
    """Measured two-column entry geometry: left column x0 and the right-aligned
    column's right edge, both from target measurement. `right_row_top_delta_pt`
    records the measured inline y relation (right cell shares the left cell's
    text row in the target design).
    """

    model_config = ConfigDict(extra="forbid")

    left_x0_pt: float
    right_x1_pt: float
    right_row_top_delta_pt: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class BodyScaffold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headings: list[BodyHeadingScaffold] = Field(min_length=1)
    entry: BodyEntryScaffold | None = None
    contact_icons_present: bool = True
    contact_separator: str | None = None


class BodyHeadingSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=0)
    alignment: Literal["left", "center", "right"]
    rule: bool


class BodyTopology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    headings: list[BodyHeadingSlot]


class BodyTopologyCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[BodyTopology] = Field(min_length=1, max_length=3)


@dataclass(frozen=True)
class GeometryObservation:
    tops_pt: dict[str, float]
    row_count: int
    wrapped_roles: tuple[str, ...] = ()
    overflow: bool = False
    bounds_pt: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    overlaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class FitResult:
    passed: bool
    parameters: FitParameters
    max_delta_pt: float
    renders: int
    elapsed_seconds: float
    failures: tuple[str, ...] = ()
    observations: tuple[GeometryObservation, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BodyAnchor:
    key: str
    block_id: str
    label: str
    text: str
    tier: str  # "l1" | "bullet_text" | "metadata"
    unit_id: str | None


@dataclass(frozen=True)
class BodyHeadingObservation:
    """Measured local geometry of one rendered section heading.

    Every gap is content-independent (rule → heading text, heading text → next
    content, content above → rule), so heading acceptance survives the source
    and target having different content volumes. block_id keys the heading's
    own knobs; prev_block_id (None for the first heading) keys the knob for the
    content → rule gap, which the previous block's padding owns.
    """

    label: str
    top_pt: float
    x0_pt: float
    font_height_pt: float
    rule_gap_above_pt: float | None
    rule_gap_below_pt: float | None
    content_gap_below_pt: float | None
    block_id: str = ""
    prev_block_id: str | None = None
    rule_below_gap_pt: float | None = None
    rule_below_x0_pt: float | None = None
    rule_below_x1_pt: float | None = None
    rendered_text: str = ""


@dataclass(frozen=True)
class BodyLine:
    page: int
    top: float
    x0: float
    text: str
    block_id: str
    label: str
    tier: str  # "l1" | "bullet_text" | "metadata" | "unattributed"
    x1: float = 0.0
    anchor_key: str = ""
    right_column: bool = False  # l1 row that also carries a right-column cell (entry table)


@dataclass(frozen=True)
class BodyDot:
    block_id: str
    label: str
    unit_id: str
    x0: float


@dataclass(frozen=True)
class BodyGeometry:
    anchor_x0: dict[str, float]
    lines: tuple[BodyLine, ...]
    dots: tuple[BodyDot, ...]
    failures: tuple[str, ...]
    headings: tuple[BodyHeadingObservation, ...] = ()
    contact_separator: str | None = None
    # The rendered header contact line (the line containing '@') — lives above
    # the first heading, outside the body-line inventory; the contact
    # separator verification measures it here (owner ruling 2026-09-11,
    # E→F final rerun).
    contact_line_text: str = ""


@dataclass(frozen=True)
class BodyFitResult:
    passed: bool
    html: str
    indents_pt: dict[str, float]
    unit_pads_pt: dict[str, float]
    geometry: BodyGeometry | None
    renders: int
    failures: tuple[str, ...] = ()
    heading_knobs: dict[str, float] = field(default_factory=dict)
    line_knobs: dict[str, float] = field(default_factory=dict)
    right_row_corrections: dict[str, float] = field(default_factory=dict)


def derive_header_scaffold(
    target_pdf: Path,
    summary: dict[str, Any],
    required_slots: list[str] | None = None,
) -> list[HeaderScaffold]:
    """Turn provider-neutral header evidence into roles without retaining target facts."""
    header_elements = [
        row for row in summary.get("elements", [])
        if str(row.get("section_path", "")).casefold().endswith("/header")
    ]
    if not header_elements:
        raise ValueError("target evidence has no measured header elements")
    heading_tops = [
        float(row["bbox_pt"]["top"])
        for row in summary.get("elements", [])
        if row.get("structural_role") == "heading_candidate" and row.get("bbox_pt")
        # Body section headings start at the measured body left margin (E→D
        # cold start, 2026-09-11): the header's own tagline row is also a
        # provider heading_candidate but centered — the margin-aligned test
        # bounds the header block at the first body-margin heading, so the
        # tagline stays header content instead of truncating the header rows.
        and abs(float(row["bbox_pt"]["x0"]) - float(summary.get("margins_pt", {}).get("default", {}).get("left", 0))) <= 2
    ]
    candidate_cutoff = min(heading_tops, default=math.inf)
    # Second structural cap (E→D second freeze): the first text line that is
    # itself left-margin-aligned starts the body — header rows are
    # centered/indented by design, and section labels the provider did not
    # tag heading_candidate (D's SUMMARY) are still margin-aligned lines.
    margin_left = float(summary.get("margins_pt", {}).get("default", {}).get("left", 0))
    all_header_candidates = _page_text_lines(target_pdf, max_lines=20)

    from tests.experiments.fill_plan import CONTACT_KIND_CHECKS as _CKC

    def _cap_line_text(line: dict[str, Any]) -> str:
        return " ".join(str(word["text"]) for word in line["words"])

    def _cap_line_is_contact(line: dict[str, Any]) -> bool:
        text = _cap_line_text(line)
        return any(check(text) for check in _CKC.values()) or "(cid:" in text

    # Frozen-pair regression fix (2026-09-11, D→E 200018Z derivation restore):
    # the contact line itself is header content wherever it sits — E's contact
    # line starts at the left margin (its leading icon glyph), so the cap must
    # not bind at the contact line. Only non-contact margin-aligned lines
    # start the body.
    margin_line_tops = [
        float(row["top"])
        for row in all_header_candidates
        if row["words"]
        and abs(min(float(word["x0"]) for word in row["words"]) - margin_left) <= 2
        and not _cap_line_is_contact(row)
    ]
    cutoff = min(candidate_cutoff, min(margin_line_tops, default=math.inf))
    lines = [row for row in all_header_candidates if float(row["top"]) < cutoff]
    if not lines:
        raise ValueError("target PDF has no measurable header text rows")

    # Roles by measured content (E→D cold start, 2026-09-11): the contact row
    # is the header line that carries contact-kind tokens or icon markers —
    # not a positional assumption that it is always the last line (D's tagline
    # row sits BELOW its contact line). Fallback keeps the frozen positional
    # mapping when no line matches (two-line headers unchanged).
    from tests.experiments.fill_plan import CONTACT_KIND_CHECKS, _ROLE_WORDS

    def line_text(line: dict[str, Any]) -> str:
        return " ".join(str(word["text"]) for word in line["words"])

    def line_is_contact(line: dict[str, Any]) -> bool:
        text = line_text(line)
        return any(check(text) for check in CONTACT_KIND_CHECKS.values()) or "(cid:" in text

    # Owner option B (E→D schema boundary, 2026-09-11): a header row carrying
    # ≥2 non-empty '|'-separated segments is a multi-item bar (interests-bar
    # class), categorically different from a single-phrase tagline row. Bars
    # are section-rendered (presentation follows the target; content comes
    # from the source only), never header topology rows.
    def line_is_multi_item_bar(line: dict[str, Any]) -> bool:
        segments = [segment.strip() for segment in line_text(line).split("|")]
        return len(segments) >= 2 and all(segments)

    # Same location-shape predicate as source_header_units (City,State line),
    # applied to measured target rows: a location-shaped extension row is a
    # location row; every other single-phrase extension row is a tagline row
    # (owner option B: the header topology is name/contact/tagline only).
    _LOCATION_SHAPE = re.compile(r"[A-Za-z .'-]+,\s*[A-Za-z .'-]+")

    def classify_extension_row(line: dict[str, Any]) -> str | None:
        if line_is_multi_item_bar(line):
            return "bar_section"
        text = line_text(line)
        if re.fullmatch(_LOCATION_SHAPE, text) and "|" not in text and not _ROLE_WORDS.search(text):
            return "location"
        return "tagline"

    contact_line_index = next((i for i, line in enumerate(lines[1:], 1) if line_is_contact(line)), None)
    # (role, line) pairs; bar_section rows stay paired with their line (the
    # compiler renders them as section geometry) but are never topology rows.
    classified_rows: list[tuple[str, dict[str, Any]]]
    if len(lines) == 1:
        classified_rows = [("name", lines[0])]
    elif contact_line_index is not None:
        classified_rows = [("name", lines[0])]
        for index, line in enumerate(lines[1:], 1):
            if index == contact_line_index:
                classified_rows.append(("contact", line))
            else:
                classified_rows.append((classify_extension_row(line), line))
    else:
        classified_rows = [("name", lines[0])]
        for line in lines[1:-1]:
            classified_rows.append((classify_extension_row(line), line))
        classified_rows.append(("contact", lines[-1]))
    roles = [role for role, _ in classified_rows]
    evidence_ids = [str(row["id"]) for row in header_elements if row.get("id")]

    contact_slots = [
        kind for kind, check in CONTACT_KIND_CHECKS.items()
        if any(check(str(element.get("text_sample") or "")) for element in header_elements)
    ]
    slots_by_role: dict[str, list[str]] = {
        "name": ["name"],
        "location": ["location"],
        "tagline": ["tagline"],
        "contact": contact_slots or ["phone", "envelope", "github", "linkedin"],
    }
    # Owner option B (E→D schema boundary, 2026-09-11): when the source
    # requires a location line and the measured header has no location-shaped
    # row, the location slot lands on the contact row — the D target's own
    # contact line carries the location marker (presentation follows target).
    if required_slots and "location" in required_slots and "location" not in roles:
        if "location" not in slots_by_role["contact"]:
            slots_by_role["contact"] = [*slots_by_role["contact"], "location"]
    page_width = float(summary["pages"][0]["width_pt"])
    # Per-row measured typography: the row's style is the style group of the
    # text ON that row's own measured line (median char size + family vote),
    # matched against the provider's measured style groups — never a
    # positional assumption about which group "the next row" must use.
    groups = list(summary.get("style_groups", {}).values())
    import pdfplumber

    with pdfplumber.open(target_pdf) as document:
        page_chars = document.pages[0].chars

    def line_style(line: dict[str, Any]) -> dict[str, Any] | None:
        top = float(line["top"])
        bottom = top + max(float(word.get("height") or 0) for word in line["words"])
        line_x0 = min(float(word["x0"]) for word in line["words"])
        chars = [
            char for char in page_chars
            if top - 1 <= (float(char["top"]) + float(char["bottom"])) / 2 <= bottom + 1
            and float(char["x1"]) > line_x0 - 1
        ]
        if not chars:
            return None
        sizes = sorted(float(char["size"]) for char in chars if char.get("size") is not None)
        if not sizes:
            return None
        median = sizes[len(sizes) // 2]
        same_size = [g for g in groups if abs(float(g.get("font_size_pt") or 0) - median) <= 0.2]
        if not same_size:
            return None
        flattened = [str(char.get("fontname") or "").casefold().replace(" ", "") for char in chars]

        def family_key(group: dict[str, Any]) -> str:
            return str(group.get("font_family") or "").casefold().replace(" ", "")

        best = max(
            same_size,
            key=lambda g: (sum(1 for name in flattened if family_key(g) and family_key(g) in name), family_key(g)),
        )
        bold = bool(best.get("bold")) or any("bold" in str(char.get("fontname") or "").casefold() for char in chars)
        return {
            "font_family": str(best.get("font_family") or "Arial"),
            "font_size_pt": float(best.get("font_size_pt") or median),
            "line_height_pt": float(best.get("line_height_pt") or 0) or None,
            "bold": bold,
        }

    return [
        HeaderScaffold(
            role=role,
            top_pt=float(line["top"]),
            x0_pt=min(float(word["x0"]) for word in line["words"]),
            x1_pt=max(float(word["x1"]) for word in line["words"]),
            evidence_ids=evidence_ids,
            # bar_section rows carry no slot (compiler-owned section geometry,
            # owner option B); every topology-role row gets its measured slots.
            slots=[] if role == "bar_section" else slots_by_role[role],
            alignment=(
                "center" if abs((min(float(word["x0"]) for word in line["words"]) + max(float(word["x1"]) for word in line["words"])) / 2 - page_width / 2) <= 12
                and max(float(word["x1"]) for word in line["words"]) - min(float(word["x0"]) for word in line["words"]) < page_width * 0.8
                else "left" if min(float(word["x0"]) for word in line["words"]) <= page_width * 0.15
                else "right"
            ),
            **(line_style(line) or {}),
        )
        for role, line in classified_rows
    ]


def required_slot_kinds(source_text: str) -> list[str]:
    """Expose only semantic slot kinds to the Layout Architect, never candidate facts."""
    return list(dict.fromkeys(unit["kind"] for unit in source_header_units(source_text)))


def _measured_alignment(x0: float, x1: float, margin_left: float, page_width: float) -> str:
    if abs(x0 - margin_left) <= 1:
        return "left"
    if abs((x0 + x1) / 2 - page_width / 2) <= 12:
        return "center"
    return "right"


def derive_body_scaffold(
    target_pdf: Path,
    summary: dict[str, Any],
    header_scaffold: list[HeaderScaffold] | None = None,
) -> BodyScaffold:
    """Derive body presentation evidence from target measurement only.

    Section-heading presentation (verbatim sample, y, glyph height, measured
    style, separator rule with its measured gaps) and the entry two-column
    geometry (left column x0, right-aligned column x1) are measured values
    owned by the compiler. Target heading text is recorded for matching and
    audit only; it never enters any output document.
    """
    lines, _ = _pdf_lines_and_marks(target_pdf)
    margin = summary.get("margins_pt", {}).get("default", {})
    margin_left = float(margin.get("left", 0))
    page_height = float(summary["pages"][0]["height_pt"])
    style_groups = summary.get("style_groups", {})
    rules = [
        {
            "page": int(rule.get("page_number") or 1),
            "top_pt": float((rule.get("bbox") or {}).get("top", 0)) * page_height,
            "gap_above_pt": rule.get("gap_above_pt"),
            "gap_below_pt": rule.get("gap_below_pt"),
            "stroke_pt": rule.get("stroke_width_pt"),
            "color": rule.get("color_hex"),
        }
        for rule in summary.get("rules", [])
    ]
    headings: list[BodyHeadingScaffold] = []
    # Header-region exclusion (E→D cold start, 2026-09-11): a provider
    # heading_candidate inside the header's own measured rows (D's centered
    # tagline row) is header content, not a body section heading — otherwise
    # it becomes headings[0] and the compiled heading style/tagline gate
    # inherit the tagline's style.
    header_bottom = max((row.top_pt for row in header_scaffold), default=None) if header_scaffold else None
    for element in summary.get("elements", []):
        if element.get("structural_role") != "heading_candidate" or element.get("entry_path") or not element.get("bbox_pt"):
            continue
        if header_bottom is not None and float(element["bbox_pt"]["top"]) <= header_bottom + 1:
            continue
        sample = " ".join(str(element.get("text_sample") or "").split())
        page = int(element.get("page") or 1)
        matches = [
            line for line in lines
            if line["page"] == page
            and " ".join(line["text"].split()) == sample
            and abs(line["x0"] - float(element["bbox_pt"]["x0"])) <= 2
        ]
        if not matches:
            raise ValueError(f"target section heading {sample!r} is not measurable in the target PDF text rows")
        line = matches[0]
        following = next((row for row in lines[lines.index(line) + 1:] if row["page"] == page), None)
        style = style_groups.get(str(element.get("style_id") or ""), {})
        above = [
            rule for rule in rules
            if rule["page"] == page and line["top"] - 20 <= rule["top_pt"] < line["top"]
        ]
        rule = max(above, key=lambda item: item["top_pt"]) if above else None
        headings.append(BodyHeadingScaffold(
            verbatim=sample,
            page=page,
            top_pt=line["top"],
            x0_pt=line["x0"],
            x1_pt=line["x1"],
            font_height_pt=line["bottom"] - line["top"],
            font_size_pt=float(style.get("font_size_pt") or (line["bottom"] - line["top"])),
            line_height_pt=float(style.get("line_height_pt") or (line["bottom"] - line["top"])),
            bold=bool(style.get("bold")),
            font_family=str(style.get("font_family") or "Arial"),
            rule_top_pt=rule["top_pt"] if rule else None,
            rule_gap_above_pt=float(rule["gap_above_pt"]) if rule and rule["gap_above_pt"] is not None else None,
            rule_gap_below_pt=float(rule["gap_below_pt"]) if rule and rule["gap_below_pt"] is not None else None,
            rule_stroke_pt=float(rule["stroke_pt"]) if rule else None,
            rule_color_hex=str(rule["color"]) if rule else None,
            content_gap_below_pt=(following["top"] - line["bottom"]) if following else None,
            evidence_ids=[str(element["id"])],
        ))
    if not headings:
        raise ValueError("target evidence has no measurable section headings")

    first_heading_top = min(heading.top_pt for heading in headings if heading.page == headings[0].page)
    entry_elements = [
        row for row in summary.get("elements", [])
        if row.get("entry_path") and row.get("bbox_pt") and float(row["bbox_pt"]["x0"]) > margin_left
    ]
    # Owner ruling 2026-09-10 (E→F thirteenth freeze): the right-edge gate
    # target is the measured content boundary (page width − measured right
    # margin), not the widest row of the target's own content — F's rows end
    # at 570.6 by content, while the boundary is 576.0.
    right_x1 = float(summary["pages"][0]["width_pt"]) - float(margin.get("right", 0))
    left_x0 = min((float(row["bbox_pt"]["x0"]) for row in entry_elements), default=0.0)
    entry_lines = [line for line in lines if line["top"] > first_heading_top]
    # Inline y relation, measured: wherever a target row carries both the left
    # cell and the right-aligned cell, they share one text row (top delta 0 by
    # row clustering) — the right column is top-aligned with the left column.
    # The sharing test compares against the target's measured widest row edge
    # (its own content, not the content boundary above): F's rows end at
    # ~570.6 while the boundary is 576.0, so the boundary must not gate this
    # detection.
    widest_entry_row_x1 = max((float(line["x1"]) for line in entry_lines if line.get("x1")), default=0.0)
    shared_rows = [
        line for line in entry_lines
        if widest_entry_row_x1 and abs(float(line["x1"]) - widest_entry_row_x1) <= 2 and left_x0 and abs(float(line["x0"]) - left_x0) <= 2
    ]
    entry = BodyEntryScaffold(
        left_x0_pt=left_x0,
        right_x1_pt=right_x1,
        right_row_top_delta_pt=0.0 if shared_rows else None,
        evidence_ids=[str(row["id"]) for row in entry_elements if row.get("id")],
    ) if entry_elements or right_x1 else None

    # Contact-line presentation, measured structurally (E→D cold start,
    # 2026-09-11): the contact row is the header's bottom-most text row — the
    # same derivation as the header scaffold's roles — not the "line
    # containing @" coincidence heuristic, which fails when the target's own
    # email value is redacted (D). Icon glyphs appear as pdfplumber's
    # non-unicode '(cid:' markers (target-embedded icon fonts); a repeated
    # non-word run between contact values is the design's separator.
    contact_line = max(
        (line for line in lines if line["top"] < first_heading_top),
        key=lambda line: line["top"],
        default=None,
    ) if lines else None
    contact_line_text = contact_line["text"] if contact_line else ""
    icons_present = "(cid:" in contact_line_text
    separator = None
    if contact_line_text:
        candidates = re.findall(r"\s(\W+)\s", f" {contact_line_text} ")
        candidates = [c for c in candidates if "@" not in c and not re.search(r"\d", c)]
        # Element-measured separator (owner ruling 2026-09-11, E→F final
        # rerun): the target may render the separator as its own element
        # between adjacent values with no surrounding whitespace — invisible
        # to the line-text heuristic. A standalone non-word element on the
        # contact row (no word chars, no '@', no digits) repeated between the
        # row's value elements is the design's separator; unique icon glyphs
        # appear once each and cannot reach the ≥2 repetition bar.
        contact_band = contact_line
        if contact_band is not None:
            row_elements = [
                row for row in summary.get("elements", [])
                if row.get("bbox_pt")
                and not (
                    float(row["bbox_pt"]["bottom"]) < contact_band["top"]
                    or float(row["bbox_pt"]["top"]) > contact_band["bottom"]
                )
            ]
            candidates.extend(
                row["text_sample"].strip()
                for row in row_elements
                if (row.get("text_sample") or "").strip()
                and not re.search(r"[\w@]", row["text_sample"])
            )
        if candidates:
            best, count = collections.Counter(candidates).most_common(1)[0]
            if count >= 2:
                separator = f" {best} "
    return BodyScaffold(
        headings=headings,
        entry=entry,
        contact_icons_present=icons_present,
        contact_separator=separator,
    )


def derive_body_topology_candidates(
    scaffold: BodyScaffold, margin_left: float, page_width: float,
) -> BodyTopologyCandidates:
    """Derive body heading-slot topology candidates from measured evidence.

    Every heading presentation value is target-measured and compiled directly,
    so the body topology has zero model degrees of freedom: one deterministic
    candidate. The header candidates → validate → rejection-detail machinery
    still applies verbatim (validate_body_topology + run-dir rejection record).
    """
    return BodyTopologyCandidates(candidates=[BodyTopology(
        candidate_id="measured",
        headings=[
            BodyHeadingSlot(
                order=index,
                alignment=_measured_alignment(heading.x0_pt, heading.x1_pt, margin_left, page_width),
                rule=heading.rule_top_pt is not None,
            )
            for index, heading in enumerate(scaffold.headings)
        ],
    )])


def validate_body_topology(
    topology: BodyTopology,
    scaffold: BodyScaffold,
    margin_left: float,
    page_width: float,
) -> list[str]:
    failures: list[str] = []
    measured = scaffold.headings
    if len(topology.headings) != len(measured):
        return [f"heading slot count {len(topology.headings)} must equal {len(measured)} measured target headings"]
    for position, (slot, heading) in enumerate(zip(topology.headings, measured)):
        if slot.order != position:
            failures.append(f"heading slot order {slot.order} must preserve measured order (expected {position})")
        expected_alignment = _measured_alignment(heading.x0_pt, heading.x1_pt, margin_left, page_width)
        if slot.alignment != expected_alignment:
            failures.append(
                f"heading {position} alignment must be measured {expected_alignment!r}, got {slot.alignment!r}"
            )
        if slot.rule != (heading.rule_top_pt is not None):
            failures.append(
                f"heading {position} rule presence must be measured {heading.rule_top_pt is not None}, got {slot.rule}"
            )
    return failures


def _body_css(body_scaffold: BodyScaffold, bullet_marker_target: bool = True) -> list[str]:
    """Compile heading/entry presentation CSS from measured values + fit knobs.

    Padding-top (not margin-top) carries the rule → heading gap so adjacent
    sibling margins cannot collapse it. The header's content → rule gap rides
    padding-bottom (inside the header border = the first heading's rule);
    margin-bottom would sit BELOW the border and double-count against the
    heading's padding-top.
    """
    heading = body_scaffold.headings[0]
    section_gap = next(
        (item.rule_gap_above_pt for item in body_scaffold.headings[1:] if item.rule_gap_above_pt is not None),
        heading.rule_gap_above_pt,
    )
    css = [
        f".section-heading{{font-family:{escape(heading.font_family)},Arial,sans-serif;"
        f"font-size:{heading.font_size_pt:.3f}pt;line-height:{heading.line_height_pt:.3f}pt;"
        f"font-weight:{700 if heading.bold else 400};"
        f"padding-top:calc({(heading.rule_gap_below_pt or 0):.3f}pt + var(--c1-heading-rule-gap,0pt));"
        f"margin-bottom:calc({(heading.content_gap_below_pt or 0):.3f}pt + var(--c1-heading-content-gap,0pt));}}",
        f".c1-header{{padding-bottom:calc({(heading.rule_gap_above_pt or 0):.3f}pt + var(--c1-rule-gap-above,0pt));}}",
    ]
    ruled = next((item for item in body_scaffold.headings if item.rule_stroke_pt is not None), None)
    if ruled is not None:
        # Vocabulary-free structural selector (known-debt family): body section
        # containers are marked by the data-source-block attribute prefix.
        css.append(
            f"[data-source-block^='block:']{{border-bottom:{ruled.rule_stroke_pt:.3f}pt solid {ruled.rule_color_hex or '#000000'};"
            f"padding-bottom:calc({(section_gap or 0):.3f}pt + var(--c1-section-rule-gap,0pt));}}"
            "[data-source-block^='block:']:last-of-type{border-bottom:none;padding-bottom:0}"
        )
    if body_scaffold.entry is not None:
        # Owner ruling 2026-09-10: right-column children stack as blocks (the
        # target's own design: location row above date row), general rule, no
        # per-section/field special-casing. min-width:0 is the defensive guard.
        # .entry-right keeps the template's white-space:nowrap so a date is
        # never wrapped or truncated; flex-shrink stays 0.
        css.append(
            # Owner ruling 2026-09-10 (E→F eleventh freeze): the date column
            # must never flex-shrink below its nowrap content width — F's
            # template relies on the shrinkable default, and a squeezed date
            # column overflows right by its deficit and gets clipped.
            ".entry-right{min-width:0;flex-shrink:0;margin-right:calc(0pt - var(--c1-entry-right-offset,0pt))}"
            ".entry-right>*{display:block}"
            # A heading wider than the measured content width must wrap, never
            # define the document min-content: a nowrap long heading line
            # (verbatim source headings can be long) triggers Chrome print
            # shrink-to-fit, which rescales the whole page and invalidates
            # every measured geometry target.
            ".section-heading{white-space:normal}"
        )
    if not bullet_marker_target:
        # Owner ruling 2026-09-10 (E→F seventh freeze): when the target has no
        # measured bullet design (no bullet_dot tier), presentation follows the
        # target — suppress the template's own list markers; the verbatim
        # source bullet glyphs remain the only markers (• retention ruling).
        css.append(
            "ul{list-style:none!important}"
            # !important: the compiler-owned suppression must win the cascade —
            # E→D cold start (2026-09-11) showed a seed's class-scoped marker
            # rule (.highlights-list li::before{content:"•"}) outspecifying
            # the plain-element suppression and rendering an unmeasured
            # marker (zero-bullet target ruling: presentation follows target).
            "ul li::before{content:none!important}"
            ".entry-item::before{content:none!important}"
        )
    # Heading case, target-derived (owner ruling 2026-09-10, E→F twelfth
    # freeze): all-uppercase measured headings compile to uppercase transform;
    # otherwise the frozen sentence-case presentation stands (D→E unchanged).
    all_upper = all(
        heading.verbatim == heading.verbatim.upper() and any(c.isalpha() for c in heading.verbatim)
        for heading in body_scaffold.headings
    )
    if all_upper:
        css.append(".section-heading{text-transform:uppercase!important}")
    # Heading rule below the heading text (owner ruling 2026-09-10, E→F
    # twelfth freeze): compiler-owned block-level full-width rule at the
    # measured gap — the seed's inline flex-grow rule structure is overridden.
    rule_below = next(
        (item for item in body_scaffold.headings if item.rule_below_stroke_pt is not None),
        None,
    )
    if rule_below is not None and rule_below.rule_below_gap_pt is not None:
        css.append(
            ".section-heading .heading-rule{display:block;width:100%;flex-grow:0;margin-left:0;"
            f"margin-top:calc({rule_below.rule_below_gap_pt:.3f}pt + var(--c1-heading-rule-gap,0pt));"
            f"margin-bottom:calc({(rule_below.post_rule_content_gap_pt or 0):.3f}pt + var(--c1-heading-content-gap,0pt));"
            f"border-top:{rule_below.rule_below_stroke_pt:.3f}pt solid {rule_below.rule_below_color_hex or '#000000'};}}"
        )
        # with the rule below the heading, the two gaps are owned by the rule's
        # own margins — the heading element carries neither
        css.append(".section-heading h2{margin-bottom:0}")
    # Contact-line presentation, target-derived (owner ruling 2026-09-10,
    # E→F twelfth freeze): icon spans suppressed when the target contact line
    # has no icon glyphs; the measured separator is emitted as CSS content
    # decoration between contact items (never HTML text, so the
    # invented-content scan is unaffected by design).
    if not body_scaffold.contact_icons_present:
        css.append(".c1-icon{display:none}")
    if body_scaffold.contact_separator:
        # Owner ruling 2026-09-11 (E→F final rerun): the separator is the bare
        # measured character between values — the target's own inter-value
        # gaps are owned by the separator adjacency (element bboxes touch),
        # so the item margin is released and the content carries no literal
        # spaces (a space inside a flex ::before collapses asymmetrically).
        separator = body_scaffold.contact_separator.strip().replace("'", "\\'")
        css.append(".c1-contact-item{margin-right:0}")
        css.append(f".c1-contact-item + .c1-contact-item::before{{content:'{separator}'}}")
    return css


def final_reviewer_prompt(source_text: str) -> str:
    present = set(required_slot_kinds(source_text))
    labels = {"envelope": "email"}
    absent = ", ".join(
        labels.get(slot, slot)
        for slot in ("name", "title", "tagline", "location", "phone", "envelope", "github", "linkedin")
        if slot not in present
    ) or "none"
    return f"""You are a fresh read-only visual reviewer. Image 1 is the TARGET; later images are the actual C1 OUTPUT pages.
The documents intentionally contain different people and different source sections. Content gates passed: do not penalize different names, candidate-only sections, source text, or page count caused by content volume.
The following header fields do not exist in SOURCE: {absent}. Their absence is expected source truth, not a defect, and must not appear in diagnosis.
Section headings are verbatim SOURCE content. Review only their presentation: typography, spacing, rules, and alignment; their wording, naming, and capitalization are not defects.
There are no evidence-board labels in these images. Judge presentation only: typography, spacing, alignment, available source-backed icons, entry structure, rules, and page behavior. C1 is a header pilot, so state header defects separately from inherited body defects. Return JSON only using this schema:\n""" + json.dumps(FinalReview.model_json_schema())


def validate_topology(
    topology: HeaderTopology,
    scaffold: list[HeaderScaffold],
    required_slots: list[str],
) -> list[str]:
    failures: list[str] = []
    # bar_section rows are compiler-owned section geometry (owner option B):
    # they are never topology rows and don't participate in role matching.
    target_roles = [row.target_role for row in topology.rows if row.target_role != "extension"]
    expected_roles = [row.role for row in scaffold if row.role != "bar_section"]
    if target_roles != expected_roles:
        failures.append(f"target rows must be {expected_roles!r}, got {target_roles!r}")
    actual_slots = [slot for row in topology.rows for slot in row.slots]
    missing = [slot for slot in required_slots if slot not in actual_slots]
    if missing:
        failures.append(f"missing required slots: {missing!r}")
    duplicates = sorted({slot for slot in actual_slots if actual_slots.count(slot) > 1})
    if duplicates:
        failures.append(f"duplicate slots: {duplicates!r}")
    expected_slots = {row.role: row.slots for row in scaffold}
    for row in topology.rows:
        if row.target_role != "extension" and row.slots != expected_slots.get(row.target_role):
            failures.append(
                f"target {row.target_role} row slots must be {expected_slots.get(row.target_role)!r}, got {row.slots!r}"
            )
        if row.target_role == "contact" and any(
            slot not in {"phone", "envelope", "github", "linkedin", "location"} for slot in row.slots
        ):
            # Owner option B (E→D schema boundary, 2026-09-11): a contact row
            # may also carry the location slot — the D target's own contact
            # line carries the location marker (presentation follows target).
            failures.append("contact rows may contain contact slots only")
    return failures


def apply_measured_alignment(topology: HeaderTopology, scaffold: list[HeaderScaffold]) -> HeaderTopology:
    """Exact target alignment is measured evidence, not an LLM choice.

    Rows with a target_role the scaffold does not measure (an invented row) are
    left untouched: they are neither overwritten nor a crash — validation
    rejects them with full details, and the bounded retry carries those
    details. Owner ruling 2026-09-10 after the E→F generalization freeze.
    """
    alignments = {row.role: row.alignment for row in scaffold}
    identity_alignment = alignments.get("location", alignments.get("name", "left"))
    return topology.model_copy(update={
        "rows": [
            row.model_copy(update={"alignment": alignments[row.target_role]})
            if row.target_role in alignments
            else row.model_copy(update={"alignment": identity_alignment})
            if row.target_role == "extension"
            else row
            for row in topology.rows
        ]
    })


def relocate_misplaced_slots(
    topology: HeaderTopology,
    scaffold: list[HeaderScaffold],
) -> tuple[HeaderTopology, list[dict[str, str]]]:
    """Deterministically move source-only slots off target rows before validation.

    A slot's home is the target row whose scaffold lists it, or an extension row when
    no target row claims it. Contact-class slots are never relocated: a contact slot
    on a wrong row stays a rejection. No row is ever invented; when the topology has
    no receiving row the slot stays put and validation keeps rejecting.
    """
    contact_slots = {"phone", "envelope", "github", "linkedin"}
    home = {slot: row.role for row in scaffold for slot in row.slots}
    expected = {row.role: row.slots for row in scaffold}
    extension_index = next(
        (index for index, row in enumerate(topology.rows) if row.target_role == "extension"), None
    )
    rows = [row.model_copy(update={"slots": list(row.slots)}) for row in topology.rows]
    actions: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        if row.target_role == "extension":
            continue
        for slot in [slot for slot in row.slots if slot not in expected.get(row.target_role, [])]:
            if slot in contact_slots:
                continue
            destination = home.get(slot, "extension")
            target_index = extension_index if destination == "extension" else next(
                (position for position, other in enumerate(rows) if other.target_role == destination), None
            )
            if target_index is None or target_index == index:
                continue
            rows[index].slots.remove(slot)
            if slot in rows[target_index].slots:
                actions.append({"action": "drop_duplicate_slot", "slot": slot, "from_role": row.target_role, "to_role": rows[target_index].target_role})
            else:
                rows[target_index].slots.append(slot)
                actions.append({"action": "relocate_slot", "slot": slot, "from_role": row.target_role, "to_role": rows[target_index].target_role})
    if not actions:
        return topology, []
    return topology.model_copy(update={"rows": rows}), actions


def topology_fingerprint(topology: HeaderTopology) -> str:
    shape = [(row.target_role, row.alignment, tuple(row.slots)) for row in topology.rows]
    return hashlib.sha256(json.dumps(shape, separators=(",", ":")).encode()).hexdigest()


def _style_for(role: str, summary: dict[str, Any]) -> dict[str, Any]:
    groups = list(summary.get("style_groups", {}).values())
    if not groups:
        return {"font_family": "Arial", "font_size_pt": 11, "line_height_pt": 14, "bold": role == "name"}
    if role == "name":
        return next((row for row in groups if row.get("bold") and float(row.get("font_size_pt", 0)) == max(float(x.get("font_size_pt", 0)) for x in groups)), groups[0])
    body = [row for row in groups if not row.get("bold") and "Awesome" not in str(row.get("font_family"))]
    return body[0] if body else groups[0]


def compile_header_template(
    base_template: str,
    topology: HeaderTopology,
    scaffold: list[HeaderScaffold],
    summary: dict[str, Any],
    parameters: FitParameters | None = None,
    body_scaffold: BodyScaffold | None = None,
    bullet_marker_target: bool = True,
    required_slots: list[str] | None = None,
) -> str:
    """Compile topology to one fixed DOM/CSS convention owned entirely by C1.

    With a body scaffold, the compiler also owns section-heading presentation
    (measured font size/line height/weight, rule → heading and heading →
    content gaps, separator rule) and the entry right-column offset; heading
    text itself stays Filler-owned verbatim source content.
    """
    parameters = parameters or FitParameters(gaps_pt=[0.0] * (len(topology.rows) - 1))
    if len(parameters.gaps_pt) != len(topology.rows) - 1:
        raise ValueError("one gap parameter is required between each topology row")
    tree = lxml_html.document_fromstring(base_template)
    headers = tree.xpath("//header") or tree.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' header ')]")
    if not headers:
        raise ValueError("base template has no header element")
    old = headers[0]
    header = lxml_html.Element("header", {"class": "c1-header", "data-c1-dna": topology_fingerprint(topology)})
    icons = {"phone": "fa-phone", "envelope": "fa-envelope", "github": "fa-github", "linkedin": "fa-linkedin"}
    placeholders = {slot: f"[{slot.upper()}]" for slot in ("name", "title", "tagline", "location", "phone", "envelope", "github", "linkedin")}
    # Owner option B (E→D schema boundary, 2026-09-11): a declared slot the
    # source does not require renders EMPTY — its row exists in the target
    # design, but no source content claims it, and placeholder text must never
    # reach the output (target/placeholder text is not source content).
    required_set = set(required_slots or ())
    def slot_placeholder(slot: str) -> str:
        return placeholders[slot] if slot in required_set else ""
    measured_counts: dict[str, int] = {}
    css: list[str] = [
        ".c1-header{display:flow-root;margin-top:var(--c1-offset);}",
        f".c1-header{{--c1-offset:{parameters.offset_pt:.3f}pt;}}",
        ".c1-row{display:flex;flex-wrap:nowrap;align-items:baseline;white-space:nowrap;}",
        ".c1-row[data-align='left']{justify-content:flex-start;text-align:left}",
        ".c1-row[data-align='center']{justify-content:center;text-align:center}",
        ".c1-row[data-align='right']{justify-content:flex-end;text-align:right}",
        ".c1-row>[data-slot]+[data-slot]{margin-left:6pt}",
        # Owner 裁决 2026-09-10（选项 A）：标题保留 Title Case 视觉呈现，
        # 源全大写文本不变 —— 此规则为冻结规则，Phase A 改 heading CSS 时不得移除。
        ".section-heading{text-transform:lowercase!important}",
        ".section-heading::first-letter{text-transform:uppercase}",
        ".c1-contact-item{display:inline-flex;align-items:baseline;margin-right:24pt}",
        ".c1-contact-item:last-child{margin-right:0}",
        ".c1-icon{font-family:'Font Awesome 6 Free','Font Awesome 6 Brands';font-style:normal;margin-right:5pt}",
        ".c1-icon.fa-github,.c1-icon.fa-linkedin{font-weight:400}.c1-icon.fa-phone,.c1-icon.fa-envelope{font-weight:900}",
    ]
    if summary.get("rules"):
        rule = summary["rules"][0]
        css.append(
            f".c1-header{{border-bottom:{float(rule['stroke_width_pt']):.3f}pt solid {rule.get('color_hex') or '#000000'};}}"
        )
    margin_left = float(summary.get("margins_pt", {}).get("default", {}).get("left", 0))
    entry_lefts = [
        float(row["bbox_pt"]["x0"])
        for row in summary.get("elements", [])
        if row.get("entry_path") and row.get("bbox_pt") and float(row["bbox_pt"]["x0"]) > margin_left
    ]
    if entry_lefts:
        indent = min(entry_lefts) - margin_left
        # Vocabulary-free structural selector (known-debt family): the seed's
        # section containers may be <section> tags or <div class="section">
        # — the data-source-block attribute prefix 'block:' marks body section
        # containers (the header carries data-source-block="document").
        css.append(f"[data-source-block^='block:']>:not(.section-heading){{padding-left:var(--c1-section-indent,{indent:.3f}pt)}}")
    target_by_role = {row.role: row for row in scaffold}
    for index, row in enumerate(topology.rows):
        attrs = {"class": "c1-row", "data-c1-role": row.target_role, "data-align": row.alignment}
        node = lxml_html.Element("div", attrs)
        for slot in row.slots:
            placeholder_text = slot_placeholder(slot)
            # Owner ruling 2026-09-10 (E→F twelfth freeze): when the target's
            # own contact line carries no icon glyphs, the icon elements are
            # not built at all — display:none would leave the injected PUA
            # codepoints in the HTML and fail the icon-font gate.
            is_contact_slot = row.target_role == "contact"
            if slot in icons and (body_scaffold is None or body_scaffold.contact_icons_present):
                item = lxml_html.Element("span", {"class": "contact-item c1-contact-item"})
                item.append(lxml_html.Element("i", {"class": f"c1-icon {'fa-brands' if slot in {'github', 'linkedin'} else 'fa-solid'} {icons[slot]}"}))
                value = lxml_html.Element("span", {"data-slot": slot})
                value.text = placeholder_text
                item.append(value)
                node.append(item)
            elif is_contact_slot:
                # Icon-free target (owner ruling 2026-09-10, E→F twelfth
                # freeze: no icon elements are built): the value is still
                # wrapped so the measured separator renders as CSS content
                # between adjacent compiler-owned items. The wrapper carries
                # only the compiler-owned class — the template vocabulary
                # 'contact-item' implies an icon and must not appear on an
                # icon-free item (contact_without_icon invariant).
                item = lxml_html.Element("span", {"class": "c1-contact-item"})
                value = lxml_html.Element("span", {"data-slot": slot})
                value.text = placeholder_text
                item.append(value)
                node.append(item)
            else:
                value = lxml_html.Element("span", {"data-slot": slot})
                value.text = placeholder_text
                node.append(value)
        header.append(node)
        # Row typography (E→D cold start, 2026-09-11): the row's style is the
        # style of its OWN measured target line (carried on the scaffold);
        # the positional group heuristic only held where the corpus made the
        # first non-bold group the contact style. Invented rows (no scaffold
        # row) keep the role heuristic. Occurrence-matched per role so
        # repeated location rows map in order.
        measured_rows = [srow for srow in scaffold if srow.role == row.target_role and srow.font_size_pt is not None]
        measured_index = measured_counts.get(row.target_role, 0)
        measured_counts[row.target_role] = measured_index + 1
        measured = measured_rows[measured_index] if measured_index < len(measured_rows) else None
        style = {
            "font_family": measured.font_family,
            "font_size_pt": measured.font_size_pt,
            "line_height_pt": measured.line_height_pt,
            "bold": measured.bold,
        } if measured else _style_for(row.target_role, summary)
        css.append(
            f".c1-row:nth-child({index + 1}){{font-family:{escape(str(style.get('font_family') or 'Arial'))},Arial,sans-serif;"
            f"font-size:{float(style.get('font_size_pt') or 11):.3f}pt;line-height:{float(style.get('line_height_pt') or 14):.3f}pt;"
            f"min-height:{float(style.get('line_height_pt') or 14):.3f}pt;"
            f"font-weight:{700 if style.get('bold') else 400};margin-top:{0 if index == 0 else parameters.gaps_pt[index - 1]:.3f}pt;}}"
        )
        if row.target_role in target_by_role:
            node.set("data-evidence-ids", " ".join(target_by_role[row.target_role].evidence_ids))
    # Owner option B (E→D schema boundary, 2026-09-11): bar_section rows are
    # compiler-owned section geometry rendered after the header rows — the
    # measured top/line-height become fixed offsets; content is source-only
    # (empty here; the interests-bar source fill is out of scope for these
    # pairs) and target-sample bar text must never enter the output.
    last_topology_row = topology.rows[-1] if topology.rows else None
    last_scaffold = next((row for row in scaffold if row.role == (last_topology_row.target_role if last_topology_row else None)), None)
    for bar in [row for row in scaffold if row.role == "bar_section"]:
        prev_top = last_scaffold.top_pt if last_scaffold else bar.top_pt
        prev_height = float(last_scaffold.line_height_pt or 0) if last_scaffold else 0.0
        margin = max(float(bar.top_pt) - prev_top - prev_height, 0.0)
        bar_node = lxml_html.Element("div", {"class": "c1-bar-section", "data-c1-role": "bar_section"})
        header.append(bar_node)
        css.append(
            f".c1-bar-section{{min-height:{float(bar.line_height_pt or 0):.3f}pt;"
            f"margin-top:{margin:.3f}pt;text-align:{bar.alignment};"
            f"font-family:{escape(str(bar.font_family or 'Arial'))},Arial,sans-serif;"
            f"font-size:{float(bar.font_size_pt or 0):.3f}pt;}}"
        )
    old.getparent().replace(old, header)
    heads = tree.xpath("//head")
    if not heads:
        raise ValueError("base template has no head element")
    style_node = lxml_html.Element("style", {"data-c1-compiler": "header/1"})
    style_node.text = "\n".join(css)
    heads[0].append(style_node)
    if body_scaffold is not None:
        body_node = lxml_html.Element("style", {"data-c1-compiler": "body/1"})
        body_node.text = "\n".join(_body_css(body_scaffold, bullet_marker_target=bullet_marker_target))
        heads[0].append(body_node)
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>")


def fit_geometry(
    scaffold: list[HeaderScaffold],
    row_count: int,
    evaluate: Callable[[FitParameters, str], GeometryObservation],
    *,
    render_budget: int = 12,
    time_budget_seconds: float = 60,
    tolerance_pt: float = 1.0,
) -> FitResult:
    """Fit renderer residuals by measured error only; this loop makes zero API calls."""
    started = time.monotonic()
    # bar_section rows are compiler-owned section geometry (owner option B):
    # they are not topology rows and are not fitted — their offset is a fixed
    # measured constant carried by the compiler.
    roles = [row.role for row in scaffold if row.role != "bar_section"]
    targets = {row.role: row.top_pt for row in scaffold if row.role != "bar_section"}
    params = FitParameters(gaps_pt=[0.0] * (row_count - 1))
    observations: list[GeometryObservation] = []
    failures: list[str] = []
    while len(observations) < render_budget and time.monotonic() - started <= time_budget_seconds:
        observation = evaluate(params, "fit")
        observations.append(observation)
        missing = [role for role in roles if role not in observation.tops_pt]
        if missing or observation.row_count != row_count or observation.wrapped_roles or observation.overflow:
            failures = [
                *( [f"missing measured roles: {missing!r}"] if missing else [] ),
                *( [f"row count {observation.row_count} != {row_count}"] if observation.row_count != row_count else [] ),
                *( [f"wrapped roles: {list(observation.wrapped_roles)!r}"] if observation.wrapped_roles else [] ),
                *( ["header overflow"] if observation.overflow else [] ),
            ]
            break
        errors = [targets[role] - observation.tops_pt[role] for role in roles]
        if max(abs(error) for error in errors) <= tolerance_pt:
            failures.extend(observation.overlaps)
            break
        gaps = list(params.gaps_pt)
        params = FitParameters(
            offset_pt=max(-36, min(144, params.offset_pt + errors[0])),
            gaps_pt=[
                max(-36, min(72, gap + errors[min(index + 1, len(errors) - 1)] - errors[min(index, len(errors) - 1)]))
                for index, gap in enumerate(gaps)
            ],
        )

    if not failures and observations:
        for probe in ("short", "long"):
            if len(observations) >= render_budget or time.monotonic() - started > time_budget_seconds:
                failures.append("probe budget exhausted")
                break
            check = evaluate(params, probe)
            observations.append(check)
            if check.row_count != row_count or check.wrapped_roles or check.overflow or check.overlaps:
                failures.append(f"{probe} probe failed")
                failures.extend(check.overlaps)
            probe_deltas = [abs(targets[role] - check.tops_pt[role]) for role in roles if role in check.tops_pt]
            if len(probe_deltas) != len(roles) or max(probe_deltas, default=math.inf) > tolerance_pt:
                failures.append(f"{probe} probe exceeded geometry tolerance")
    last = observations[-1] if observations else GeometryObservation({}, 0)
    deltas = [abs(targets[role] - last.tops_pt[role]) for role in roles if role in last.tops_pt]
    max_delta = max(deltas, default=math.inf)
    if max_delta > tolerance_pt:
        failures.append(f"max target-role delta {max_delta:.3f}pt exceeds {tolerance_pt:.3f}pt")
    return FitResult(
        passed=not failures,
        parameters=params,
        max_delta_pt=max_delta,
        renders=len(observations),
        elapsed_seconds=round(time.monotonic() - started, 3),
        failures=tuple(dict.fromkeys(failures)),
        observations=tuple(observations),
    )


def derive_deterministic_topology(
    scaffold: list[HeaderScaffold],
    required_slots: list[str],
) -> HeaderTopology:
    """Phase 2 ABLATION MATERIAL — NOT wired into the run path.

    Orchestrator steering 2026-09-11: the Architect ablation is a separately
    dispatched ticket; this function is the ablation's deterministic arm and
    is unused by run() (the Architect LLM call remains the main path).
    Ablation evidence so far: fingerprint-equal across D→E/E→F/E→D with zero
    validation failures (runs/a_pipeline_reconstruction_20260911/
    phase2_ablation.json).

    Deterministic header topology from scaffold + required slots (option C
    arm): one row per measured topology-relevant scaffold row (bar_section
    rows excluded), and — when the source requires header slots the target
    header does not measure — exactly one trailing extension row carrying
    those source-only slots in required order. Alignment is measured evidence
    (apply_measured_alignment), never a model choice. No candidate facts and
    no target facts participate.
    """
    rows = [
        HeaderRow(target_role=row.role, slots=list(row.slots), alignment=row.alignment)
        for row in scaffold if row.role != "bar_section"
    ]
    home_slots = {slot for row in scaffold for slot in row.slots}
    source_only = [slot for slot in dict.fromkeys(required_slots) if slot not in home_slots]
    if source_only:
        identity = next(
            (row.alignment for row in scaffold if row.role == "location"),
            next((row.alignment for row in scaffold if row.role == "name"), "left"),
        )
        rows.append(HeaderRow(target_role="extension", slots=source_only, alignment=identity))
    return HeaderTopology(candidate_id="deterministic", rows=rows)


def architect_prompt(scaffold: list[HeaderScaffold], required_slots: list[str]) -> str:
    topology_rows = [row for row in scaffold if row.role != "bar_section"]
    target_row_slots = {row.role: row.slots for row in topology_rows}
    home_slots = {slot for row in topology_rows for slot in row.slots}
    bar_rows = [row for row in scaffold if row.role == "bar_section"]
    return """You are the C1 Layout Architect. Return JSON only. Propose up to three header topologies.
You choose structure only: rows, semantic slots, and alignment. Measurements are immutable and owned by the compiler.
Use every target row exactly once and preserve its order. Include every required candidate slot exactly once; slots absent from target rows belong in extension rows.
EVIDENCE.target_row_slots is the complete measured slot list of each target row: every slot it lists must appear in that row, even when required_candidate_slots does not mention it, and no target row may omit or add slots.
EVIDENCE.slots_without_target_row have no measured target row; place each of them in an extension row.
The name target row contains only name, a location target row contains only location, a tagline target row contains only tagline, and a contact target row contains contact kinds only (a contact row may also carry the location slot when EVIDENCE lists it).
EVIDENCE.bar_section_rows are compiler-owned section rows; they are not topology rows and must not appear in the topology.
Do not include candidate or target text, HTML, CSS, coordinates, font values, or visual guesses.
SCHEMA:\n""" + json.dumps(TopologyCandidates.model_json_schema()) + "\nEVIDENCE:\n" + json.dumps({
        "target_rows": [row.model_dump(mode="json") for row in topology_rows],
        "required_candidate_slots": required_slots,
        "target_row_slots": target_row_slots,
        "slots_without_target_row": [slot for slot in required_slots if slot not in home_slots],
        "bar_section_rows": [row.model_dump(mode="json") for row in bar_rows],
    }, sort_keys=True)


def c1_filler_prompt(base_prompt: str) -> str:
    return base_prompt + """

C1 SINGLE-CALL EXIT CHECK (perform before returning HTML):
1. Every document/header source line is inside one header carrying data-source-block="document".
2. Every source section keeps one container with its exact block owner and heading; never split one source block across containers.
3. A summary, highlights, volunteer, project, or additional section may not be relabeled as another semantic section. If no compatible template section exists, append a same-semantic section after all template sections.
4. Every visible text node is carried by an element with data-source-line, or by a container that inherited one source line (its annotated children plus bare separators reproduce that line verbatim); unprovenanced visible text is invalid.
5. A source line may span several elements carrying the same data-source-line only when their text, concatenated in DOM order, equals the source line exactly once.
6. Same-line fragments must appear in the source line's reading order.
Return a gate-valid document. At most one bounded retry may follow, carrying the complete deterministic gate failures.
"""


def split_merged_source_line_nodes(candidate_html: str, source_text: str) -> tuple[str, list[dict[str, str]]]:
    """Deterministically split one annotated node whose text spans several
    consecutive source lines (owner option B contract, E→D 2026-09-11).

    When the target's column wraps differently from the source, the Filler may
    merge consecutive source lines into one node annotated with the first line
    only — provenance invariant ① then reports the second line's tokens as
    missing/duplicated. The merge has exactly one unambiguous repair: the node
    text's token stream equals the consecutive source lines' token streams
    concatenated, so the split points are the source-line boundaries. Each
    piece becomes its own node annotated with its own line; the visible text
    concatenation is unchanged (third disposition category: unique unambiguous
    normalization).
    """
    lines = source_lines(source_text)
    line_ids = sorted(lines)
    order = {line_id: index for index, line_id in enumerate(line_ids)}
    tree = lxml_html.document_fromstring(candidate_html)
    actions: list[dict[str, str]] = []

    def tokens_of(text: str) -> list[str]:
        return [re.sub(r"\W+", "", part.casefold()) for part in text.split()]

    for node in tree.xpath("//*[@data-source-line]"):
        annotation = str(node.get("data-source-line") or "").strip()
        if annotation not in order or node.find("*") is not None:
            continue  # single-line annotations only; element children handled elsewhere
        text = node.text or ""
        node_tokens = tokens_of(text)
        line_index = order[annotation]
        # consume consecutive source lines from the annotated line onward
        consumed: list[tuple[str, int]] = []  # (line_id, token_count)
        cursor = 0
        matched_lines: list[str] = []
        for offset in range(line_index, len(line_ids)):
            line_tokens = tokens_of(lines[line_ids[offset]])
            if cursor + len(line_tokens) > len(node_tokens):
                break
            if node_tokens[cursor:cursor + len(line_tokens)] != line_tokens:
                break
            consumed.append((line_ids[offset], len(line_tokens)))
            matched_lines.append(line_ids[offset])
            cursor += len(line_tokens)
            if cursor == len(node_tokens):
                break
        if cursor != len(node_tokens) or len(consumed) < 2:
            continue
        # locate the raw-text offset of each subsequent line's first token
        word_spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
        boundaries: list[int] = []
        token_cursor = 0
        for _, count in consumed[:-1]:
            token_cursor += count
            boundaries.append(word_spans[token_cursor][0])
        pieces = [text]
        for boundary in reversed(boundaries):
            head, tail = pieces[0][:boundary].rstrip(), pieces[0][boundary:]
            pieces[0] = head
            pieces.insert(1, tail)
        parent = node.getparent()
        anchor_index = parent.index(node) if parent is not None else 0
        previous = node
        for piece_id, piece in zip(consumed[1:], pieces[1:]):
            clone = lxml_html.Element(node.tag, dict(node.attrib))
            clone.set("data-source-line", piece_id[0])
            for attr in ("data-c1-anchor", "data-c1-line-fit", "data-c1-anchor-x0"):
                if clone.get(attr) is not None:
                    del clone.attrib[attr]
            clone.text = piece
            previous.addnext(clone)
            previous = clone
            actions.append({"action": "split_merged_source_line", "node_line": annotation,
                            "split_line": piece_id[0], "piece_tokens": str(len(tokens_of(piece)))})
        node.text = pieces[0]
    if not actions:
        return candidate_html, []
    return lxml_html.tostring(tree, encoding="unicode"), actions


def remove_unprovenanced_punctuation_nodes(candidate_html: str) -> tuple[str, list[dict[str, str]]]:
    """Remove content-free unprovenanced decorative leaves (invariant-① companion).

    The Filler sometimes preserves a static template separator span (e.g. the
    D seed's summary-line '—'). Such a glyph carries no source tokens and
    cannot be annotated — owner decision 2026-09-08 strips fabricated line ids
    (a_pipeline._normalize_provenance_annotations) — so within the frozen gate
    set the only deterministic action is deletion: the glyph is not source
    content, exact-token/verbatim coverage is untouched, and word-bearing
    unprovenanced text stays a hard gate failure (never silenced here).
    Same family as remove_unused_placeholders: static template leftover
    cleanup; the placeholder-text-never-enters-output principle covers it.
    """
    tree = lxml_html.document_fromstring(candidate_html)
    actions: list[dict[str, str]] = []
    for text_node in list(tree.xpath("//body//text()[normalize-space()]")):
        parent = text_node.getparent()
        if parent is None or any(
            str(element.tag).casefold() in {"script", "style", "title"}
            for element in (parent, *parent.iterancestors())
        ):
            continue
        if any(element.get("data-source-line") is not None for element in (parent, *parent.iterancestors())):
            continue
        if re.findall(r"\w+", str(text_node)):
            continue
        actions.append({"action": "remove_unprovenanced_punctuation", "node": tree.getroottree().getpath(parent),
                        "text": " ".join(str(text_node).split())})
        if not text_node.is_tail:
            parent.text = None
        else:
            previous = text_node.getprevious()
            if previous is not None:
                previous.tail = None
            else:
                parent.text = None
        if not parent.text and not len(parent) and not (parent.tail or "").strip():
            grandparent = parent.getparent()
            if grandparent is not None:
                grandparent.remove(parent)
    if not actions:
        return candidate_html, []
    return lxml_html.tostring(tree, encoding="unicode"), actions


def inherit_container_source_line(candidate_html: str, source_text: str) -> tuple[str, list[dict[str, str]]]:
    """Owner rulings 2026-09-10 (E→F fifth + sixth freezes): a container whose
    text, concatenated in DOM order, is exactly one source line inherits that
    line's data-source-line — carrying the template-induced
    `<strong>label</strong>: <span>value</span>` idiom (seed templates hold a
    bare ':' separator the Filler faithfully reproduces), including when the
    label element itself was left unannotated. Final invariant-① semantics:
    every text fragment belongs to exactly one annotated source line (direct
    node annotation, or the nearest annotated container with the verbatim
    concatenation as proof). Conditions, all required: the concatenation
    reproduces exactly one source line (whitespace-insensitive) and matches
    uniquely; all annotated descendants already carry that same line (conflict
    is a rejection, never an overwrite); unannotated nodes deeper than the
    container's direct children stay rejected; and the container has a bare
    text segment (containers whose text lives entirely inside annotated
    children carry nothing unprovenanced and stay record-scoped)."""
    lines = source_lines(source_text)
    lines_by_text: dict[str, list[str]] = {}
    for line_id, line in lines.items():
        lines_by_text.setdefault(" ".join(line.casefold().split()), []).append(line_id)
    tree = lxml_html.document_fromstring(candidate_html)
    tree_path = tree.getroottree()
    actions: list[dict[str, str]] = []
    for container in tree.xpath("//body//*[not(@data-source-line) and *[@data-source-line]]"):
        deeper_unannotated = any(
            not descendant.get("data-source-line")
            and (descendant.getparent() is not container or len(descendant))
            for descendant in container.xpath(".//*")
        )
        if deeper_unannotated:
            continue
        if not any(
            (element.text and element.text.strip()) if element is container else (element.tail and element.tail.strip())
            for element in (container, *container)
        ):
            continue
        text = " ".join("".join(container.itertext()).casefold().split())
        matches = lines_by_text.get(text, [])
        if len(matches) != 1:
            continue
        line_id = matches[0]
        if any(
            descendant.get("data-source-line") not in (None, line_id)
            for descendant in container.xpath(".//*")
        ):
            continue
        container.set("data-source-line", line_id)
        marked = []
        for child in container:
            if not child.get("data-source-line") and not len(child):
                child.set("data-source-line", line_id)
                marked.append(tree_path.getpath(child))
        actions.append({
            "action": "inherit_source_line",
            "node": tree_path.getpath(container),
            "source_line_id": line_id,
            "marked_direct_children": marked,
        })
    if not actions:
        return candidate_html, []
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>"), actions


def c1_provenance_contract_failures(
    candidate_html: str,
    source_text: str,
    analysis: dict[str, Any] | None = None,
) -> list[str]:
    """Enforce C1's explicit text annotation and same-line ordering invariants.

    Invariant ① is container-level: every visible text node is carried either
    by an element with data-source-line or by a container that inherited one
    line via inherit_container_source_line (annotated children + bare tails
    reproducing that line verbatim)."""
    analysis = analysis or analyze_candidate_provenance(candidate_html, source_text)
    tree = lxml_html.document_fromstring(candidate_html)
    failures: list[str] = []
    for text_node in tree.xpath("//body//text()[normalize-space()]"):
        parent = text_node.getparent()
        if parent is None or any(
            str(element.tag).casefold() in {"script", "style", "title"}
            for element in (parent, *parent.iterancestors())
        ):
            continue
        # ratified invariant-① ownership (owner rulings 2026-09-10): the nearest
        # ancestor-or-self carrying data-source-line — a container's annotation
        # carries the whole verified line, including bare tails and unannotated
        # direct text-leaf children; content that does not belong to that line
        # is falsified by the invented-content / verbatim gates, not here.
        owner = next(
            (element for element in (parent, *parent.iterancestors()) if element.get("data-source-line") is not None),
            None,
        )
        if owner is None:
            failures.append(
                f"invariant=unprovenanced_text; node={tree.getroottree().getpath(parent)!r}; "
                f"text={' '.join(str(text_node).split())!r}"
            )
    failures.extend(
        f"{warning.get('source_line_id')}: invariant=source_reading_order; evidence={warning!r}"
        for warning in analysis.get("warnings", [])
        if warning.get("code") == "source_reading_order"
    )
    return failures


def normalize_source_block_owners(candidate_html: str, source_text: str) -> tuple[str, list[dict[str, str]]]:
    """Give every annotated fragment its source-derived block owner."""
    tree = lxml_html.document_fromstring(candidate_html)
    blocks = source_blocks(source_text)
    actions: list[dict[str, str]] = []
    for node in tree.xpath("//*[@data-source-line]"):
        line_id = node.get("data-source-line") or ""
        expected = blocks.get(line_id)
        if not expected:
            continue
        actual = next((ancestor.get("data-source-block") for ancestor in (node, *node.iterancestors()) if ancestor.get("data-source-block")), None)
        if actual == expected:
            continue
        node.set("data-filler-source-block", actual or "missing")
        node.set("data-source-block", expected)
        actions.append({"action": "set_source_block", "source_line_id": line_id, "from": actual or "missing", "to": expected})
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>"), actions


def inject_missing_source_headings(
    candidate_html: str,
    base_template_html: str,
    source_text: str,
) -> tuple[str, list[dict[str, str]]]:
    """Inject source-verbatim headings for identifiable rendered sections."""
    lines, blocks = source_lines(source_text), source_blocks(source_text)
    tree = lxml_html.document_fromstring(candidate_html)
    for section in _section_nodes(tree):
        if not section.get("data-source-block"):
            owned = {
                blocks.get(line_id)
                for line_id in section.xpath(".//*[@data-source-line]/@data-source-line")
                if blocks.get(line_id) not in {None, "document"}
            }
            if len(owned) == 1:
                block_id = owned.pop()
                section.set("data-source-block", block_id)
                section.set("data-source-heading", block_id.removeprefix("block:"))
    missing = {
        (section.get("data-source-heading") or (section.get("data-source-block") or "").removeprefix("block:"))
        for section in _section_nodes(tree)
        if section.get("data-source-heading") or section.get("data-source-block")
    }
    missing = {line_id for line_id in missing if line_id in lines and not tree.xpath(f"//*[@data-source-line='{line_id}']")}
    normalized = normalize_section_headings(
        lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>"),
        base_template_html,
        source_text,
    )
    result = lxml_html.document_fromstring(normalized)
    actions: list[dict[str, str]] = []
    for line_id in sorted(missing):
        nodes = [
            node
            for section in _section_nodes(result)
            for node in section.xpath(f".//*[@data-source-line='{line_id}']")
        ]
        if not nodes:
            continue
        node = nodes[0]
        node.set("data-slot", "heading")
        actions.append({"action": "inject_source_heading", "source_line_id": line_id, "text": lines[line_id]})
    return lxml_html.tostring(result, encoding="unicode", doctype="<!DOCTYPE html>"), actions


def normalize_source_section_semantics(candidate_html: str, source_text: str) -> str:
    """Make source headings, not Filler labels, authoritative for section semantics."""
    tree = lxml_html.document_fromstring(candidate_html)
    lines, blocks = source_lines(source_text), source_blocks(source_text)
    changed = False
    for section in _section_nodes(tree):
        if not section.get("data-source-block"):
            continue
        block_id = section.get("data-source-block") or ""
        heading_id = section.get("data-source-heading") or block_id.removeprefix("block:")
        semantic = _section_semantic(lines.get(heading_id, "")) if blocks.get(heading_id) == block_id else None
        if not semantic:
            continue
        filler_label = section.get("data-section")
        if filler_label and filler_label != semantic:
            section.set("data-filler-section", filler_label)
        if filler_label != semantic:
            section.set("data-section", semantic)
            changed = True
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>") if changed else candidate_html


def merge_unprovenanced_sibling_headings(candidate_html: str, source_text: str) -> str:
    """Fold a split source-line label back into its adjacent provenanced leaf."""
    tree = lxml_html.document_fromstring(candidate_html)
    lines = source_lines(source_text)
    changed = False
    headings = tree.xpath("//h1[not(@data-source-line)]|//h2[not(@data-source-line)]|//h3[not(@data-source-line)]|//h4[not(@data-source-line)]|//h5[not(@data-source-line)]|//h6[not(@data-source-line)]")
    for heading in headings:
        title = " ".join(heading.itertext()).strip()
        parent = heading.getparent()
        if not title or not any(character.isalnum() for character in title) or parent is None or heading.xpath(".//*[@data-source-line]"):
            continue
        siblings = list(parent)
        index = siblings.index(heading)
        for sibling_index in (index + 1, index - 1):
            if not 0 <= sibling_index < len(siblings):
                continue
            sibling = siblings[sibling_index]
            line_id = sibling.get("data-source-line")
            source_line = lines.get(line_id or "")
            if not source_line or len(sibling) or sibling.xpath(".//*[@data-source-line]"):
                continue
            pieces = (title, " ".join(sibling.itertext()).strip()) if sibling_index > index else (" ".join(sibling.itertext()).strip(), title)
            if re.findall(r"\w+", " ".join(pieces).casefold()) != re.findall(r"\w+", source_line.casefold()):
                continue
            sibling.text = source_line
            parent.remove(heading)
            changed = True
            break
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>") if changed else candidate_html


def _heading_knob_rules(knobs: dict[str, float]) -> list[str]:
    """CSS var declarations for the heading/entry fit knobs.

    Keys: 'rule_gap_above' (header padding), 'section-rule-gap:<block>' (that
    section's padding = gap above the NEXT heading), 'heading-rule-gap:<block>'
    and 'heading-content-gap:<block>' (the section's own heading), and
    'entry_right'. Custom properties inherit, so per-section declarations also
    reach the section's .section-heading child.
    """
    rules: list[str] = []
    per_section: dict[str, dict[str, float]] = {}
    for key, value in sorted(knobs.items()):
        if key == "rule_gap_above":
            rules.append(f".c1-header{{--c1-rule-gap-above:{value:.3f}pt}}")
        elif key == "entry_right":
            rules.append(f".entry-right{{--c1-entry-right-offset:{value:.3f}pt}}")
        elif key.startswith("line:"):
            # per-line correction for l1 outliers in mixed-indent sections
            rules.append(f"[data-c1-line-fit='{key.partition(':')[2]}']{{margin-left:{value:.3f}pt}}")
        elif key.startswith("right:"):
            # one-shot right-edge correction (measured boundary, absolute)
            rules.append(f"[data-c1-right-fit='{key.split(':', 1)[1]}']{{margin-right:{value:.3f}pt}}")
        else:
            name, _, block_id = key.partition(":")
            per_section.setdefault(block_id, {})[f"--c1-{name.replace('_', '-')}"] = value
    for block_id, variables in sorted(per_section.items()):
        declarations = ";".join(f"{name}:{value:.3f}pt" for name, value in sorted(variables.items()))
        # Vocabulary-free structural selector (known-debt family, §10-4/#3/#4/#11
        # precedent): seeds may carry either <section> tags or <div class=\
        # "section"> vocabulary — the data-source-block attribute is the
        # structural marker, so the tag prefix must not narrow the selector.
        rules.append(f"[data-source-block='{block_id}']{{{declarations}}}")
    return rules


def apply_section_indents(
    candidate_html: str,
    indents_pt: dict[str, float],
    unit_pads_pt: dict[str, float] | None = None,
    heading_knobs: dict[str, float] | None = None,
) -> str:
    tree = lxml_html.document_fromstring(candidate_html)
    for old in tree.xpath("//style[@data-c1-section-fit]"):
        old.getparent().remove(old)
    heads = tree.xpath("//head")
    if not heads:
        return candidate_html
    rules = [
        # Vocabulary-free structural selector (known-debt family): matches both
        # <section data-source-block> and <div class="section" data-source-block>.
        f"[data-source-block='{block_id}']{{--c1-section-indent:{indent:.3f}pt}}"
        for block_id, indent in sorted(indents_pt.items())
    ]
    rules.extend(
        # The section-scoped prefix outranks the compiler's direct-child section
        # indent rule, which would otherwise override the unit padding.
        f"[data-source-block] [data-c1-bullet-unit='{unit_id}']{{padding-left:{pad:.3f}pt}}"
        for unit_id, pad in sorted((unit_pads_pt or {}).items(), key=lambda item: int(item[0]))
    )
    if heading_knobs is not None:
        # Knobs feed the var() slots the compiler's body CSS reserves; the base
        # values inside the calc() stay the target-measured constants.
        rules.extend(_heading_knob_rules(heading_knobs))
    style = lxml_html.Element("style", {"data-c1-section-fit": "body-line-x0/3"})
    style.text = "\n".join(rules)
    heads[0].append(style)
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>")


BULLET_GLYPHS = frozenset({"•", "◦", "·", "▪", "‣", "●", "∙"})


def _section_nodes(tree: Any) -> list[Any]:
    """Section ROLE by structure, not tag vocabulary (E→D cold start,
    2026-09-11; #3/#4/#11 vocabulary→structure precedent family): the C-era
    seeds emit <section> elements, the B-era D seed emits <div
    class="section">. Both are section blocks; the exact-class token test
    keeps sibling vocabulary (section-heading, section-lead) out."""
    return tree.xpath(
        "//section | //div[contains(concat(' ', normalize-space(@class), ' '), ' section ')]"
    )


def derive_body_tier_targets(target_pdf: Path, l1_x0_pt: float) -> dict[str, float]:
    """Derive the tiered body-line x0 targets from target measurement only."""
    lines, _ = _pdf_lines_and_marks(target_pdf)
    dot_x0s: list[float] = []
    after_dot_x0s: list[float] = []
    for line in lines:
        words = line["words"]
        if str(words[0]["text"]) not in BULLET_GLYPHS or len(words) < 2:
            continue
        dot_x0s.append(float(words[0]["x0"]))
        after_dot_x0s.append(float(words[1]["x0"]))
    tiers = {"l1": l1_x0_pt}
    if dot_x0s:
        tiers["bullet_dot"] = min(dot_x0s)
    if after_dot_x0s:
        tiers["bullet_text"] = min(after_dot_x0s)
    return tiers


def _grid_container_classes(candidate_html: str) -> set[str]:
    """Class names the template's own CSS lays out as grid columns."""
    return {
        name
        for stylesheet in re.findall(r"<style[^>]*>(.*?)</style>", candidate_html, re.S)
        for name in re.findall(r"\.([A-Za-z_][\w-]*)\s*\{[^}]*?display\s*:\s*grid\b", stylesheet)
    }


def _prepare_body_line_fit(candidate_html: str) -> tuple[str, list[BodyAnchor], list[str]]:
    """Annotate bullet units and discover the tiered body-line anchors.

    Bullet units are the template's entry lists plus the bullet markers created
    by the dash rule (leading list-marker dashes become .entry-item bullets).
    Every provenanced body text node becomes an anchor: level-1 body when no
    bullet unit owns it, bullet text when one does. Right-aligned .entry-date
    metadata is frozen presentation, not body text, so it is only claimed to
    keep it out of the tier inventory. Anchor discovery is deterministic, so
    unit ids are stable across renders of the same document.
    """
    tree = lxml_html.document_fromstring(candidate_html)
    grid_classes = _grid_container_classes(candidate_html)
    unit_counter = 0
    anchors: list[BodyAnchor] = []
    for section in (node for node in _section_nodes(tree) if node.get("data-source-block")):
        block_id = section.get("data-source-block") or ""
        heading = section.xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' section-heading ')]")
        label = " ".join(heading[0].itertext()).strip() if heading else block_id
        unit_seen: set[int] = set()
        for unit in section.xpath(
            # Owner ruling 2026-09-10 (E→F fourth freeze): bullet-unit discovery
            # is structural — any ul whose li descendants carry provenanced text
            # is a bullet unit, regardless of the template's class vocabulary
            # (ul.entry-list is a subset, D→E behavior unchanged). Standalone
            # .entry-item markers outside any ul (dash-rule conversions) remain
            # units of their own.
            ".//ul[li[@data-source-line]]"
            " | .//ul[contains(concat(' ', normalize-space(@class), ' '), ' entry-list ')]"
            " | .//*[contains(concat(' ', normalize-space(@class), ' '), ' entry-item ')"
            " and not(ancestor::ul)]"
        ):
            if id(unit) in unit_seen:
                continue
            unit_seen.add(id(unit))
            unit_counter += 1
            unit.set("data-c1-bullet-unit", str(unit_counter))
        for node in section.xpath(".//*[@data-source-line]"):
            classes = (node.get("class") or "").split()
            if any(
                "section-heading" in (ancestor.get("class") or "").split()
                for ancestor in (node, *node.iterancestors())
            ):
                continue
            text = " ".join(node.itertext()).strip()
            if not any(character.isalnum() for character in text):
                continue
            unit_element = next(
                (
                    ancestor
                    for ancestor in (node, *node.iterancestors())
                    if ancestor.get("data-c1-bullet-unit")
                ),
                None,
            )
            ancestor_classes = {
                name for ancestor in (node, *node.iterancestors())
                for name in (ancestor.get("class") or "").split()
            }
            if "entry-date" in classes or "entry-right" in ancestor_classes:
                # Right-aligned metadata, structurally discovered (E→F, 2026-09-11
                # orchestrator ruling, #4/#11 precedent family): the entry date
                # column is the right cell of a two-column entry row — E's
                # template names it .entry-date, F's .date; both live in the same
                # .entry-right container the compiler owns, so the container is
                # the vocabulary-free predicate (the .entry-date class check is
                # kept only because D→E leaves predate it). Frozen presentation:
                # claimed to keep it out of the tier inventory, transparently
                # reported in the entry table.
                tier, unit_id = "metadata", None
            elif unit_element is not None:
                tier, unit_id = "bullet_text", unit_element.get("data-c1-bullet-unit")
            elif ancestor_classes & grid_classes:
                # Template-owned multi-column grid placement: the column offset
                # is frozen presentation no body-line tier or knob can govern.
                tier, unit_id = "frozen_cell", None
            else:
                tier, unit_id = "l1", None
            key = f"a{len(anchors)}"
            if tier == "l1":
                # l1 anchors are addressable for the per-line correction knobs
                # (owner ruling 2026-09-10, E→F eighth freeze: mixed-indent
                # sections need a second knob family)
                node.set("data-c1-line-fit", key)
            # every anchor is structurally addressable (right-edge corrections
            # locate their row container through the anchor, never through
            # template class vocabulary — owner ruling 2026-09-10, ninth freeze)
            node.set("data-c1-anchor", key)
            anchors.append(BodyAnchor(
                key=key, block_id=block_id, label=label, text=text,
                tier=tier, unit_id=unit_id,
            ))
    units = sorted({anchor.unit_id for anchor in anchors if anchor.unit_id}, key=int)
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>"), anchors, units


def _pdf_lines_and_marks(pdf: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cluster extractable words into lines; line x0 includes glyph prefixes.

    extract_words gives reliable line grouping and tokens, but a glyph whose
    font is not word-extractable (the source's `⌣` markers) is invisible to it,
    so the line would appear to start at the first extractable word. Every
    character overlapping the line's vertical span that sits left of the first
    word therefore extends the line x0 to the real line start.
    """
    import pdfplumber

    lines: list[dict[str, Any]] = []
    marks: list[dict[str, Any]] = []
    with pdfplumber.open(pdf) as document:
        for page_number, page in enumerate(document.pages, 1):
            page_chars = [
                char for char in page.chars
                if str(char["text"]).strip()
            ]
            clusters: list[list[dict[str, Any]]] = []
            for word in sorted(page.extract_words(), key=lambda word: float(word["top"])):
                if clusters and abs(float(word["top"]) - float(clusters[-1][0]["top"])) <= 1.5:
                    clusters[-1].append(word)
                else:
                    clusters.append([word])
            for cluster in clusters:
                cluster.sort(key=lambda word: float(word["x0"]))
                top = min(float(word["top"]) for word in cluster)
                bottom = max(float(word["bottom"]) for word in cluster)
                first_word_x0 = float(cluster[0]["x0"])
                prefix_chars = sorted(
                    (
                        char for char in page_chars
                        if top - 2 <= (float(char["top"]) + float(char["bottom"])) / 2 <= bottom + 2
                        and float(char["x1"]) <= first_word_x0 + 2
                    ),
                    key=lambda char: float(char["x0"]),
                )
                x0 = min([first_word_x0, *(float(char["x0"]) for char in prefix_chars)])
                prefix = "".join(str(char["text"]) for char in prefix_chars)
                lines.append({
                    "page": page_number,
                    "top": top,
                    "bottom": bottom,
                    "x0": x0,
                    "text": (prefix + " " if prefix else "") + " ".join(str(word["text"]) for word in cluster),
                    "x1": max(float(word.get("x1", word["x0"])) for word in cluster),
                    "words": cluster,
                })
            for mark in page.rects + page.curves + (getattr(page, "lines", None) or []):
                width = float(mark["x1"]) - float(mark["x0"])
                height = float(mark["bottom"]) - float(mark["top"])
                page_width = float(getattr(page, "width", 0) or 0)
                if page_width and width > page_width * 0.5 and height <= 2.5:
                    marks.append({
                        "page": page_number,
                        "top": float(mark["top"]),
                        "bottom": float(mark["bottom"]),
                        "x0": float(mark["x0"]),
                        "x1": float(mark["x1"]),
                        "kind": "rule",
                    })
                    continue
                color = mark.get("non_stroking_color")
                # marker size window (owner ruling 2026-09-10, E→F seventh
                # freeze): collects both D→E's 5pt dots and F's 2.82pt template
                # markers; the positional filters at the call sites do the
                # real discrimination.
                if not (1 <= width <= 8 and 1 <= height <= 8):
                    continue
                if isinstance(color, (list, tuple)) and color and min(float(channel) for channel in list(color)[:3]) > 0.5:
                    continue
                marks.append({
                    "page": page_number,
                    "top": float(mark["top"]),
                    "bottom": float(mark["bottom"]),
                    "x0": float(mark["x0"]),
                    "x1": float(mark["x1"]),
                })
    return lines, marks


def _match_line(lines: list[dict[str, Any]], text: str) -> int | None:
    """Find the rendered line carrying the anchor's leading tokens.

    The reported x0 is always the matched line's FIRST word: the real line x0,
    never the x0 of a mid-line token. Coverage scoring keeps short anchors (for
    example `Certifications:`) on their own line instead of an earlier heading
    that merely contains the token. Skills-table cells wrap mid-token-list, so
    when no contiguous window matches, progressively shorter leading prefixes
    are tried; the single-token fallback requires the line to START with the
    token so a heading containing the word never wins.
    """
    needle = [token for part in text.casefold().split() if (token := re.sub(r"\W+", "", part))][:4]
    if not needle:
        return None
    for length in (len(needle), 3, 2):
        if length > len(needle):
            continue
        best: tuple[tuple[int, float], int] | None = None
        for index, line in enumerate(lines):
            tokens = [
                stripped for word in line["words"]
                if (stripped := re.sub(r"\W+", "", str(word["text"]).casefold()))
            ]
            for start in range(len(tokens) - length + 1):
                if tokens[start:start + length] == needle[:length]:
                    # a line the anchor text starts is the anchor's own line;
                    # a heading merely containing the token must not win.
                    score = (1 if start == 0 else 0, length / max(len(tokens), 1))
                    if best is None or score > best[0]:
                        best = (score, index)
                    break
        if best is not None:
            return best[1]
    head = needle[0]
    for index, line in enumerate(lines):
        tokens = [
            stripped for word in line["words"]
            if (stripped := re.sub(r"\W+", "", str(word["text"]).casefold()))
        ]
        if tokens and tokens[0] == head:
            return index
    # Hyphen-split fallback (measured-layer robustness, 2026-09-11 E→D): the
    # render may break a word at an existing hyphen ("try-" / "before-you-
    # buy"), splitting the anchor's leading token across two lines. Re-match
    # against the hyphen-joined token stream and report the CONTINUATION line
    # (the anchor's content physically starts there).
    for index in range(len(lines) - 1):
        words, next_words = lines[index]["words"], lines[index + 1]["words"]
        if not words or not next_words:
            continue
        last = str(words[-1]["text"])
        if not last.endswith("-") or len(last) < 2:
            continue
        joined = re.sub(r"\W+", "", (last[:-1] + str(next_words[0]["text"])).casefold())
        rest = [
            stripped for word in next_words[1:]
            if (stripped := re.sub(r"\W+", "", str(word["text"]).casefold()))
        ]
        stream = [joined, *rest]
        for length in (len(needle), 3, 2):
            if length > len(needle) or length > len(stream):
                continue
            if stream[:length] == needle[:length]:
                return index + 1
    return None


def _norm_tokens(text: str) -> list[str]:
    tokens = []
    for part in text.casefold().split():
        stripped = re.sub(r"\W+", "", part)
        tokens.append(stripped or part)  # keep pure-punctuation tokens comparable
    return tokens


def _section_heading_texts(candidate_html: str) -> dict[str, str]:
    """Full verbatim text of each section's heading element, by source block."""
    tree = lxml_html.document_fromstring(candidate_html)
    texts: dict[str, str] = {}
    for section in (node for node in _section_nodes(tree) if node.get("data-source-block")):
        block_id = section.get("data-source-block")
        headings = section.xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' section-heading ')]")
        if block_id and headings:
            texts[block_id] = " ".join(" ".join(headings[0].itertext()).split())
    return texts


def rendered_duplicate_bullet_lines(pdf: Path) -> list[dict[str, Any]]:
    """Render-aware duplicate-bullet detection (owner ruling 2026-09-10, E→F
    twelfth freeze): a duplicate is a rendered line that BOTH starts with a
    source bullet glyph AND carries a self-drawn marker (small mark) to its
    left. Evidence source is the final PDF artifact, not the template's CSS
    declarations — inert content:none pseudo-markers no longer cause false
    positives."""
    lines, marks = _pdf_lines_and_marks(pdf)
    duplicates: list[dict[str, Any]] = []
    for line in lines:
        words = [word for word in line["words"] if str(word["text"]).strip()]
        if not words or str(words[0]["text"]) not in BULLET_GLYPHS:
            continue
        glyph = words[0]
        # Char-marker duplicates (E→D cold start, 2026-09-11): a template's
        # ::before marker renders as extractable text, not a drawn mark, so
        # the glyph+mark test above cannot see it — a line whose second word
        # is another list glyph is a duplicate regardless of marker kind.
        if len(words) > 1 and str(words[1]["text"]) in BULLET_GLYPHS:
            duplicates.append({
                "page": line["page"], "top": round(line["top"], 2),
                "glyph": str(glyph["text"]), "marker_x0": round(float(words[1]["x0"]), 3),
                "text": line["text"][:80],
            })
            continue
        markers = [
            mark for mark in marks
            if mark["page"] == line["page"] and mark.get("kind") != "rule"
            and line["top"] - 1 <= (mark["top"] + mark["bottom"]) / 2 <= line["bottom"] + 1
            and mark["x1"] <= glyph["x0"] + 1
        ]
        if markers:
            duplicates.append({
                "page": line["page"], "top": round(line["top"], 2),
                "glyph": str(glyph["text"]),
                "marker_x0": round(min(mark["x0"] for mark in markers), 3),
                "text": line["text"][:80],
            })
    return duplicates


def measure_body_lines(
    candidate_html: str,
    pdf: Path,
    *,
    require_bullet_markers: bool = True,
) -> BodyGeometry:
    """Measure every real content line of the post-fill rendering by tier.

    require_bullet_markers mirrors the rule-gate auto-absence (owner ruling
    2026-09-10, E→F seventh freeze): only when the target declares a measured
    bullet design (a bullet_dot tier target exists) is a separate marker
    required for every bullet_text line; otherwise markers are presentation
    without a measured contract and their absence is transparent."""
    _, anchors, _ = _prepare_body_line_fit(candidate_html)
    lines, marks = _pdf_lines_and_marks(pdf)
    labels = {anchor.block_id: anchor.label for anchor in anchors}
    failures: list[str] = []
    claimed: dict[int, tuple[str, str, str]] = {}
    metadata_shared: set[int] = set()
    anchor_line: dict[str, int] = {}
    anchor_x0: dict[str, float] = {}
    for anchor in (anchor for anchor in anchors if anchor.tier in {"l1", "bullet_text"}):
        index = _match_line(lines, anchor.text)
        if index is None:
            failures.append(f"{anchor.label}: body line not measurable in rendered PDF; anchor={anchor.text!r}")
            continue
        anchor_line[anchor.key] = index
        anchor_x0[anchor.key] = lines[index]["x0"]
        claimed.setdefault(index, (anchor.key, anchor.block_id, anchor.tier))
    for anchor in (anchor for anchor in anchors if anchor.tier in {"metadata", "frozen_cell"}):
        index = _match_line(lines, anchor.text)
        if index is not None:
            shared = index in claimed
            claimed.setdefault(index, (anchor.key, anchor.block_id, anchor.tier))
            # F-style two-column row: the date shares the title's text row, so
            # the row keeps its l1 tier (x0 gate) and is additionally marked as
            # a right-column row for the entry table (boundary overshoot gate).
            if shared and anchor.tier == "metadata":
                metadata_shared.add(index)
    heading_line: dict[str, int] = {}
    for block_id, label in labels.items():
        index = _match_line(lines, label)
        if index is not None:
            heading_line[block_id] = index
    # Sections whose ONLY annotated element is the heading (no body anchors) are
    # invisible to the anchor map; discover them from the heading elements so
    # they still get a region, heading observation, and knob attribution.
    heading_texts = _section_heading_texts(candidate_html)
    for block_id, text in heading_texts.items():
        if block_id in heading_line:
            continue
        index = _match_line(lines, text)
        if index is not None:
            heading_line[block_id] = index
    heading_continuation: set[int] = set()
    heading_last_line: dict[str, int] = {}
    for block_id, index in heading_line.items():
        full = _norm_tokens(heading_texts.get(block_id, ""))
        first = _norm_tokens(lines[index]["text"])
        if not full or len(first) > len(full) or first != full[: len(first)]:
            continue
        consumed = len(first)
        heading_last_line[block_id] = index
        cursor = index
        while consumed < len(full) and cursor + 1 < len(lines):
            candidate = lines[cursor + 1]
            chunk = _norm_tokens(candidate["text"])
            if not chunk or chunk != full[consumed : consumed + len(chunk)]:
                break
            cursor += 1
            consumed += len(chunk)
            heading_continuation.add(cursor)
            heading_last_line[block_id] = cursor
    ordered_blocks = sorted(heading_line, key=lambda block: heading_line[block])
    heading_first_lines = set(heading_line.values())
    inventory: list[BodyLine] = []
    line_block: dict[int, str] = {}
    for position, block_id in enumerate(ordered_blocks):
        region_end = heading_line[ordered_blocks[position + 1]] if position + 1 < len(ordered_blocks) else len(lines)
        last_claimed_tier: str | None = None
        last_claimed_key = ""
        for index in range(heading_line[block_id] + 1, region_end):
            line = lines[index]
            if index in claimed:
                tier = last_claimed_tier = claimed[index][2]
                last_claimed_key = claimed[index][0]
            elif index in heading_continuation:
                tier = "heading"
            else:
                # an unclaimed line is a wrapped continuation of the block above it
                tier = last_claimed_tier or "unattributed"
            line_block[index] = claimed[index][1] if index in claimed else block_id
            inventory.append(BodyLine(
                page=line["page"], top=line["top"], x0=line["x0"], text=line["text"],
                block_id=block_id, label=labels.get(block_id) or heading_texts.get(block_id, block_id), tier=tier,
                x1=max(float(word["x1"]) for word in line["words"]),
                # A wrapped continuation inherits its paragraph's anchor key
                # (measurement-layer attribution completion, 2026-09-11 D→F:
                # the right-edge correction locates the row container through
                # the anchor; hyphen-broken wrap fragments — same family as
                # the E→D hyphen fallback — previously had no anchor and the
                # existing one-shot correction could not act). Tier gates are
                # unaffected: x0 continues to gate the paragraph's own line.
                anchor_key=claimed[index][0] if index in claimed else last_claimed_key,
                right_column=index in metadata_shared,
            ))
    unattributed = [entry for entry in inventory if entry.tier == "unattributed"]
    for entry in unattributed:
        failures.append(f"{entry.label}: rendered body line could not be attributed to a tier; line={entry.text!r}")
    dots: list[BodyDot] = []
    for anchor in (anchor for anchor in anchors if anchor.tier == "bullet_text" and anchor.key in anchor_line):
        line = lines[anchor_line[anchor.key]]
        candidates = [
            mark for mark in marks
            if mark["page"] == line["page"]
            and line["top"] - 1 <= (mark["top"] + mark["bottom"]) / 2 <= line["bottom"] + 1
            and mark["x0"] < line["x0"] and mark["x1"] <= line["x0"] + 2
        ]
        if not candidates:
            # Owner rulings 2026-09-10 (• retention + marker suppression): when
            # the target has no bullet design, the verbatim source glyph IS the
            # bullet — a bullet_text line that starts with a bullet glyph has
            # its marker inline (dot x0 = glyph x0), no separate mark exists.
            first_word = line["words"][0]
            if str(first_word["text"]) in BULLET_GLYPHS:
                dots.append(BodyDot(
                    block_id=anchor.block_id, label=anchor.label,
                    unit_id=anchor.unit_id or "", x0=first_word["x0"],
                ))
                continue
            if require_bullet_markers:
                failures.append(f"{anchor.label}: bullet marker not measurable for line {anchor.text!r}")
            continue
        dots.append(BodyDot(
            block_id=anchor.block_id, label=anchor.label, unit_id=anchor.unit_id or "",
            x0=min(mark["x0"] for mark in candidates),
        ))
    rule_marks = [mark for mark in marks if mark.get("kind") == "rule"]
    headings: list[BodyHeadingObservation] = []
    ordered_heading_items = sorted(heading_line.items(), key=lambda item: item[1])
    for position, (block_id, index) in enumerate(ordered_heading_items):
        line = lines[index]
        rule = max(
            (
                mark for mark in rule_marks
                if mark["page"] == line["page"] and line["top"] - 20 <= mark["top"] <= line["top"]
            ),
            key=lambda mark: mark["top"],
            default=None,
        )
        previous_line = lines[index - 1] if index > 0 and lines[index - 1]["page"] == line["page"] else None
        last_line_index = heading_last_line.get(block_id, index)
        heading_last = lines[last_line_index]
        rule_below = min(
            (
                mark for mark in rule_marks
                if mark["page"] == heading_last["page"]
                and heading_last["bottom"] <= mark["top"] <= heading_last["bottom"] + 20
            ),
            key=lambda mark: mark["top"],
            default=None,
        )
        following_index = last_line_index + 1
        following_line = (
            lines[following_index]
            if following_index < len(lines) and lines[following_index]["page"] == line["page"]
            else None
        )
        if following_index in heading_first_lines:
            # heading-only section: the next visual element is the next section's
            # heading, so the heading → content design gap does not exist here
            following_line = None
        last_heading_line = lines[last_line_index]
        headings.append(BodyHeadingObservation(
            label=labels.get(block_id) or heading_texts.get(block_id, block_id),
            top_pt=line["top"],
            x0_pt=line["x0"],
            font_height_pt=line["bottom"] - line["top"],
            rule_gap_above_pt=(rule["top"] - previous_line["bottom"]) if rule and previous_line else None,
            rule_gap_below_pt=(line["top"] - rule["top"]) if rule else None,
            content_gap_below_pt=(following_line["top"] - last_heading_line["bottom"]) if following_line else None,
            rule_below_gap_pt=(rule_below["top"] - heading_last["bottom"]) if rule_below else None,
            rule_below_x0_pt=rule_below["x0"] if rule_below else None,
            rule_below_x1_pt=rule_below["x1"] if rule_below else None,
            rendered_text=line["text"],
            block_id=block_id,
            # The content → rule gap above this heading is owned by the block
            # carrying the nearest content line above the rule — which may be a
            # heading-less section (e.g. an inline-heading summary), not the
            # previous section with a matched heading.
            prev_block_id=line_block.get(index - 1) if position > 0 else None,
        ))
    contact_separator = None
    contact_line_text = ""
    # Render-side contact-line identification by the same structural predicate
    # as the target side (E→D Phase 1 freeze②: CONTACT_KIND_CHECKS / icon-marker
    # glyphs — debt-family #3/#4/#11; the '@' heuristic fails on sources whose
    # contact row carries no email, e.g. resume_D).
    from tests.experiments.fill_plan import CONTACT_KIND_CHECKS as _CONTACT_CHECKS

    def _rendered_line_is_contact(text: str) -> bool:
        return any(check(text) for check in _CONTACT_CHECKS.values()) or "(cid:" in text
    for line in lines:
        if not _rendered_line_is_contact(line["text"]):
            continue
        contact_line_text = line["text"]
        candidates = [
            run for run in re.findall(r"\s(\W+)\s", f" {line['text']} ")
            if "@" not in run and not re.search(r"\d", run)
        ]
        if candidates:
            best, count = collections.Counter(candidates).most_common(1)[0]
            if count >= 2:
                contact_separator = f" {best} "
        break
    return BodyGeometry(anchor_x0, tuple(inventory), tuple(dots), tuple(failures), tuple(headings), contact_separator, contact_line_text)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def fit_body_line_geometry(
    candidate_html: str,
    tier_targets: dict[str, float],
    initial_indent_pt: float,
    evaluate: Callable[[str, int], Path],
    *,
    render_budget: int = 10,
    tolerance_pt: float = 1.0,
    heading_targets: dict[str, float] | None = None,
    entry_right_x1_pt: float | None = None,
    require_bullet_markers: bool = True,
    body_scaffold: BodyScaffold | None = None,
) -> BodyFitResult:
    """Close the loop on every real content line against its measured tier.

    Knobs: per-section --c1-section-indent for level-1 body lines and a measured
    padding-left per bullet unit (entry lists and dash-rule bullet markers) for
    bullet text; the ::before dot follows its text by the template's own marker
    geometry and is verified against the target-measured dot tier. With heading
    targets, the same loop fits the compiler's measured heading gaps
    (content → rule, rule → heading text, heading text → content) and the entry
    right-column edge via the reserved var() slots; heading x0 and font height
    have no knob — they are compiled measured values, so any deviation is a
    freeze-level failure, not a tuneable.
    """
    prepared, anchors, unit_ids = _prepare_body_line_fit(candidate_html)
    indents = {
        block_id: initial_indent_pt
        for block_id in {anchor.block_id for anchor in anchors}
    }
    unit_pads = {unit_id: 0.0 for unit_id in unit_ids}
    heading_knobs: dict[str, float] = {}
    line_knobs: dict[str, float] = {}
    right_knobs: dict[str, float] = {}
    right_row_corrections: dict[str, float] = {}
    right_edge_corrected = 0
    previous: dict[str, float] = {}
    failures: list[str] = []
    final_html = prepared
    geometry: BodyGeometry | None = None

    for render_number in range(1, render_budget + 1):
        final_html = apply_section_indents(prepared, indents, unit_pads, {**heading_knobs, **line_knobs, **right_knobs})
        geometry = measure_body_lines(final_html, evaluate(final_html, render_number), require_bullet_markers=require_bullet_markers)
        if geometry.failures:
            return BodyFitResult(
                False, final_html, indents, unit_pads, geometry, render_number,
                tuple(dict.fromkeys([*failures, *geometry.failures])),
                dict(heading_knobs), dict(line_knobs),
            )
        anchor_errors = {
            anchor.key: tier_targets[anchor.tier] - geometry.anchor_x0[anchor.key]
            for anchor in anchors
            if anchor.tier in tier_targets and anchor.key in geometry.anchor_x0
        }
        dot_errors = [
            tier_targets["bullet_dot"] - dot.x0
            for dot in geometry.dots
            if "bullet_dot" in tier_targets
        ]
        knob_errors: dict[str, list[float]] = {}
        hard_failures: list[str] = []

        def _knob(name: str, error: float) -> None:
            # Deadband: Chrome quantizes sub-point layout changes, so nudging a
            # knob for a sub-tolerance error causes 0.75pt layout jumps instead
            # of convergence. Only out-of-tolerance errors update knobs.
            if abs(error) > tolerance_pt:
                knob_errors.setdefault(name, []).append(error)

        if heading_targets:
            for position, observation in enumerate(geometry.headings):
                if abs(observation.x0_pt - heading_targets["x0_pt"]) > tolerance_pt:
                    hard_failures.append(
                        f"heading {observation.label!r}: x0 {observation.x0_pt:.3f}pt deviates "
                        f"{abs(observation.x0_pt - heading_targets['x0_pt']):.3f}pt from measured "
                        f"{heading_targets['x0_pt']:.3f}pt; heading x0 is compiled, not fitted"
                    )
                if abs(observation.font_height_pt - heading_targets["font_height_pt"]) > tolerance_pt:
                    hard_failures.append(
                        f"heading {observation.label!r}: glyph height {observation.font_height_pt:.3f}pt deviates "
                        f"from measured {heading_targets['font_height_pt']:.3f}pt; font size is compiled, not fitted"
                    )
                if observation.rule_gap_below_pt is not None and heading_targets.get("rule_gap_below_pt") is not None:
                    _knob(
                        f"heading-rule-gap:{observation.block_id}",
                        heading_targets["rule_gap_below_pt"] - observation.rule_gap_below_pt,
                    )
                if observation.content_gap_below_pt is not None and heading_targets.get("content_gap_below_pt") is not None:
                    _knob(
                        f"heading-content-gap:{observation.block_id}",
                        heading_targets["content_gap_below_pt"] - observation.content_gap_below_pt,
                    )
                if observation.rule_gap_above_pt is not None:
                    if position == 0:
                        if heading_targets.get("header_rule_gap_above_pt") is not None:
                            _knob(
                                "rule_gap_above",
                                heading_targets["header_rule_gap_above_pt"] - observation.rule_gap_above_pt,
                            )
                    elif observation.prev_block_id and heading_targets.get("section_rule_gap_above_pt") is not None:
                        _knob(
                            f"section-rule-gap:{observation.prev_block_id}",
                            heading_targets["section_rule_gap_above_pt"] - observation.rule_gap_above_pt,
                        )
        if entry_right_x1_pt is not None:
            # Metadata rows are the right entry column (entry-date/entry-location);
            # the row's right edge is the cluster max x1 whether or not the left
            # cell shares the text row.
            # ponytail: wrapped metadata rows inherit the tier and join the median;
            # per-anchor pairing only if a corpus shows wrapped dates.
            for line in geometry.lines:
                if line.tier == "metadata" and line.x1:
                    _knob("entry_right", entry_right_x1_pt - line.x1)
        # Right-edge phase (owner rulings 2026-09-10, ninth+twelfth freezes):
        # rows whose right edge crosses the measured content boundary get a
        # one-shot margin-right correction, located structurally through their
        # anchors (the anchor's ancestor whose parent is the section root —
        # never template class vocabulary), then re-rendered for verification.
        # Absolute semantics, no per-render approximation. The section box
        # stays untouched so the compiled full-width heading rule is unaffected.
        right_violations = [
            line for line in geometry.lines
            if entry_right_x1_pt is not None
            and line.x1 > entry_right_x1_pt + tolerance_pt
        ]
        if right_violations and right_edge_corrected >= 2:
            details = "; ".join(f"{line.text[:40]!r} x1={line.x1:.3f}" for line in right_violations[:4])
            failures.append(
                f"right-edge correction did not bring rows within the measured boundary "
                f"({entry_right_x1_pt:.3f}pt): {details}; dom=see body_fit_probes"
            )
            return BodyFitResult(
                False, final_html, indents, unit_pads, geometry, render_number,
                tuple(dict.fromkeys(failures)), dict(heading_knobs), dict(line_knobs),
                dict(right_row_corrections),
            )
        if right_violations:
            if any(not line.anchor_key for line in right_violations):
                failures.append(
                    "right-edge violation on a line without a structural anchor; "
                    f"lines={[line.text[:40] for line in right_violations if not line.anchor_key]}"
                )
                return BodyFitResult(
                    False, final_html, indents, unit_pads, geometry, render_number,
                    tuple(dict.fromkeys(failures)), dict(heading_knobs), dict(line_knobs),
                    dict(right_row_corrections),
                )
            tree = lxml_html.document_fromstring(final_html)
            container_overshoot: dict[int, tuple[Any, str, float]] = {}
            for line in right_violations:
                anchor_element = tree.xpath(f"//*[@data-c1-anchor='{line.anchor_key}']")[0]
                chain = [anchor_element, *anchor_element.iterancestors()]
                section_index = next(
                    index for index, element in enumerate(chain)
                    if element.tag == "section" and element.get("data-source-block")
                )
                row_container = chain[section_index - 1]
                slot = id(row_container)
                worst = max(line.x1 - entry_right_x1_pt, container_overshoot.get(slot, (None, "", 0.0))[2])
                container_overshoot[slot] = (row_container, f"right:{line.block_id}:{line.anchor_key}", worst)
            for slot, (row_container, key, overshoot) in container_overshoot.items():
                row_container.set("data-c1-right-fit", key.split(":", 1)[1])
                right_knobs[key] = max(0.0, min(72.0, overshoot))
                for line in right_violations:
                    anchor_element = tree.xpath(f"//*[@data-c1-anchor='{line.anchor_key}']")[0]
                    chain = [anchor_element, *anchor_element.iterancestors()]
                    section_index = next(
                        index for index, element in enumerate(chain)
                        if element.tag == "section" and element.get("data-source-block")
                    )
                    if chain[section_index - 1] is row_container:
                        right_row_corrections[line.anchor_key] = right_knobs[key]
            right_edge_corrected += 1
            # persist the stamps so every later render carries the corrections
            prepared = lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>")
            continue  # re-render: the next measurement verifies the corrections
        if body_scaffold is not None:
            # Presentation contract tiers (owner ruling 2026-09-10, E→F twelfth
            # freeze): heading case, heading rule geometry, contact separator —
            # all compiled from target measurement; deviations are freeze-level.
            contract_by_label = {h.verbatim: h for h in body_scaffold.headings}
            for observation in geometry.headings:
                contract = contract_by_label.get(observation.rendered_text) or next(
                    (h for h in body_scaffold.headings
                     if h.verbatim.casefold() == observation.rendered_text.casefold()), None
                )
                if contract is None:
                    continue
                target_upper = contract.verbatim == contract.verbatim.upper() and any(c.isalpha() for c in contract.verbatim)
                rendered = observation.rendered_text
                rendered_upper = rendered == rendered.upper() and any(c.isalpha() for c in rendered)
                if target_upper != rendered_upper:
                    hard_failures.append(
                        f"heading {rendered!r}: rendered case does not match the target-measured "
                        f"heading case ({'uppercase' if target_upper else 'sentence'}); case is compiled, not fitted"
                    )
                target_rule_gap = contract.rule_below_gap_pt
                if target_rule_gap is not None and observation.rule_below_gap_pt is not None:
                    _knob(
                        f"heading-rule-gap:{observation.block_id}",
                        target_rule_gap - observation.rule_below_gap_pt,
                    )
                elif target_rule_gap is not None and observation.rule_below_gap_pt is None:
                    hard_failures.append(
                        f"heading {observation.label!r}: the target-measured rule below the heading is not rendered"
                    )
                if target_rule_gap is not None and observation.rule_below_x1_pt is not None:
                    overshoot = observation.rule_below_x1_pt - heading_targets["rule_x1_pt"] if heading_targets and "rule_x1_pt" in heading_targets else 0.0
                    if abs(overshoot) > tolerance_pt:
                        hard_failures.append(
                            f"heading rule width: right edge {observation.rule_below_x1_pt:.3f}pt deviates "
                            f"{abs(overshoot):.3f}pt from the measured full-width boundary; rule geometry is compiled"
                        )
                target_post = contract.post_rule_content_gap_pt
                if target_post is not None and observation.content_gap_below_pt is not None:
                    _knob(
                        f"heading-content-gap:{observation.block_id}",
                        target_post - observation.content_gap_below_pt,
                    )
            target_separator = body_scaffold.contact_separator
            if target_separator is not None:
                # Char-level verification (owner ruling 2026-09-11, E→F final
                # rerun): the target declares its separator only when the
                # standalone element repeats ≥2 times on the contact row; the
                # rendered verification counts the same character on the
                # rendered contact line. Spacing around the separator is
                # collapse-prone in extracted text (a ::before space next to
                # an element boundary), so the character count — not the
                # space-wrapped token — is the honest measured contract.
                separator_char = target_separator.strip()
                rendered_contact = geometry.contact_line_text
                # Gate errata (D→F cold start, 2026-09-11): the contract — and
                # this gate's own message — say the separator must sit between
                # every adjacent value pair; the absolute ≥2 threshold was an
                # era artifact of sources carrying ≥3 contact values and
                # misjudges a legitimate 2-value contact row (1 separator, one
                # per adjacent pair). The rendered value count is the number
                # of compiler-owned contact-item elements on the row
                # (§12.1-4 gate-errata class, owner-approved precedent: gate
                # aligned to its declared measured semantics, no new rule).
                expected_values = final_html.count('class="c1-contact-item"')
                expected_separators = expected_values - 1
                rendered_separators = sum(1 for character in rendered_contact if character == separator_char)
                if rendered_separators != expected_separators:
                    hard_failures.append(
                        f"contact separator: rendered contact line {rendered_contact!r} carries {rendered_separators} "
                        f"× {separator_char!r} but the contact row compiles {expected_values} values "
                        f"(expected {expected_separators}, one between every adjacent value pair); "
                        "separator is compiled, not fitted"
                    )
        if hard_failures:
            return BodyFitResult(
                False, final_html, indents, unit_pads, geometry, render_number,
                tuple(dict.fromkeys([*failures, *hard_failures])), dict(heading_knobs), dict(line_knobs),
                dict(right_row_corrections),
            )
        if (
            not knob_errors
            and not right_violations
            and all(abs(error) <= tolerance_pt for error in anchor_errors.values())
            and all(abs(error) <= tolerance_pt for error in dot_errors)
        ):
            return BodyFitResult(
                True, final_html, indents, unit_pads, geometry, render_number,
                tuple(failures), dict(heading_knobs), dict(line_knobs),
                dict(right_row_corrections),
            )
        indent_group: dict[str, list[float]] = {}
        unit_group: dict[str, list[float]] = {}
        line_updates: dict[str, float] = {}
        l1_items: dict[str, list[tuple[BodyAnchor, float]]] = {}
        for anchor in anchors:
            if anchor.key not in anchor_errors:
                continue
            if anchor.tier == "l1":
                l1_items.setdefault(anchor.block_id, []).append((anchor, anchor_errors[anchor.key]))
            elif anchor.unit_id:
                unit_group.setdefault(anchor.unit_id, []).append(anchor_errors[anchor.key])
        for block, items in l1_items.items():
            median_all = _median([error for _, error in items])
            in_group = [error for _, error in items if abs(error - median_all) <= tolerance_pt]
            # section knob serves the in-group majority; outliers are excluded
            # from the median signal so both knob families never borrow each
            # other's signal (owner ruling 2026-09-10, E→F eighth freeze)
            delta_s = _median(in_group if in_group else [error for _, error in items])
            indent_group[block] = in_group if in_group else [error for _, error in items]
            for anchor, error in items:
                if abs(error - median_all) > tolerance_pt:
                    # absolute line-level correction: L_new = L_cur + err - ΔS
                    # (the section update also shifts this line; both together
                    # converge it without disturbing the in-group rows)
                    current = line_knobs.get(f"line:{anchor.key}", 0.0)
                    line_updates[f"line:{anchor.key}"] = max(-72.0, min(144.0, current + error - delta_s))
        updates = {
            **{f"indent:{block}": _median(errors) for block, errors in indent_group.items()},
            **{f"unit:{unit}": _median(errors) for unit, errors in unit_group.items()},
            **{f"heading:{name}": _median(errors) for name, errors in knob_errors.items()},
            **{f"line:{key}": value for key, value in line_updates.items()},
        }
        unresponsive = [
            key for key, error in updates.items()
            if key in previous and abs(previous[key] - error) < 0.1 and abs(error) > tolerance_pt
        ]
        if unresponsive:
            unresponsive_tree = lxml_html.document_fromstring(final_html)
            for key in unresponsive:
                kind, identity = key.split(":", 1)
                if kind == "indent":
                    xpath = f"//*[@data-source-block='{identity}']"
                elif "block:" in identity:
                    block_id = "block:" + identity.split("block:", 1)[1]
                    xpath = f"//*[@data-source-block='{block_id}']"
                else:
                    xpath = f"//*[@data-c1-bullet-unit='{identity}']"
                elements = unresponsive_tree.xpath(xpath)
                dom = lxml_html.tostring(elements[0], encoding="unicode")[:2000] if elements else "element not found"
                failures.append(f"{key}: padding adjustment did not move line x0; dom={dom!r}")
            return BodyFitResult(
                False, final_html, indents, unit_pads, geometry, render_number,
                tuple(dict.fromkeys(failures)), dict(heading_knobs), dict(line_knobs),
            )
        previous = updates
        for key, error in updates.items():
            kind, identity = key.split(":", 1)
            if kind == "indent":
                indents[identity] = max(0.0, indents[identity] + error)
            elif kind == "unit":
                unit_pads[identity] = max(0.0, unit_pads[identity] + error)
            elif kind == "heading":
                heading_knobs[identity] = max(-36.0, min(144.0, heading_knobs.get(identity, 0.0) + error))
            elif kind == "line":
                line_knobs[identity] = max(-72.0, min(144.0, error))
    failures.append(f"body-line x0 fit exhausted {render_budget} renders")
    return BodyFitResult(False, final_html, indents, unit_pads, geometry, render_budget, tuple(dict.fromkeys(failures)), dict(heading_knobs), dict(line_knobs))


def render_body_x0_acceptance(
    geometry: BodyGeometry,
    tier_targets: dict[str, float],
    heading_targets: dict[str, float] | None = None,
    entry_right_x1_pt: float | None = None,
    line_corrections: dict[str, float] | None = None,
    right_corrections: dict[str, float] | None = None,
    body_scaffold: BodyScaffold | None = None,
) -> str:
    """Render the tiered per-body-line x0 acceptance table."""
    def _target(tier: str) -> str:
        return f"{tier_targets[tier]:.3f}" if tier in tier_targets else "n/a"

    def _clean(text: str) -> str:
        snippet = " ".join(text.split())[:52]
        return snippet.replace("|", "\\|")

    groups = {
        "l1": f"Level-1 body text (target {_target('l1')} pt)",
        "bullet_dot": f"Bullet dots (target {_target('bullet_dot')} pt)",
        "bullet_text": (
            f"Level-2 body / bullet text / wrapped lines (target {_target('bullet_text')} pt)"
            if "bullet_text" in tier_targets
            else "Level-2 body / bullet text / wrapped lines (无目标依据：目标版式无 bullet 设计，透明层，不计入 PASS/FAIL)"
        ),
        "heading": "Section heading wrapped continuation lines (presentation; geometry gated via the heading rows)",
        "metadata": "Entry metadata — right-aligned, frozen presentation (no tier target)",
        "frozen_cell": "Grid-column lines — template-owned multi-column layout, frozen presentation (no tier target)",
        "unattributed": "Unattributed lines (fit failures)",
    }
    rows: dict[str, list[str]] = {tier: [] for tier in groups}
    for line in geometry.lines:
        if line.tier == "heading":
            rows["heading"].append(f"| {line.label} | {_clean(line.text)} | {line.x0:.3f} | — | — |")
            continue
        if line.tier in {"metadata", "frozen_cell"} or (line.tier not in tier_targets and line.tier != "unattributed"):
            # no measured target for this tier: transparent listing, outside the
            # PASS/FAIL denominator (owner ruling 2026-09-10, E→F fourth freeze)
            rows[line.tier].append(f"| {line.label} | {_clean(line.text)} | {line.x0:.3f} | — | 无目标依据 |")
            continue
        if line.tier not in tier_targets:
            rows[line.tier].append(f"| {line.label} | {_clean(line.text)} | {line.x0:.3f} | — | FAIL |")
            continue
        delta = abs(line.x0 - tier_targets[line.tier])
        correction = (line_corrections or {}).get(line.anchor_key)
        right_note = ""
        if right_corrections and line.anchor_key in right_corrections:
            right_note = f"（右缘修正 {right_corrections[line.anchor_key]:.3f}pt）"
        note = f"（行级修正 {correction:.3f}pt）{right_note}" if correction and abs(correction) > 0.01 else (right_note or "")
        rows[line.tier].append(
            f"| {line.label} | {_clean(line.text)}{note} | {line.x0:.3f} | {delta:.3f} | {'PASS' if delta <= 1 else 'FAIL'} |"
        )
    for dot in geometry.dots:
        if "bullet_dot" not in tier_targets:
            continue
        delta = abs(dot.x0 - tier_targets["bullet_dot"])
        rows["bullet_dot"].append(
            f"| {dot.label} | ::before marker | {dot.x0:.3f} | {delta:.3f} | {'PASS' if delta <= 1 else 'FAIL'} |"
        )
    report = ["# Body-line tiered x0 acceptance", ""]
    report.append("Every real content line of the final post-fill rendering, measured line x0 (first word),")
    report.append("compared against its target-measured tier. Tolerance: ≤1 pt.")
    for tier in ("l1", "bullet_dot", "bullet_text", "heading", "metadata", "frozen_cell", "unattributed"):
        report.extend(["", f"## {groups[tier]}", ""])
        if not rows[tier]:
            report.append("_none_")
            continue
        report.extend(["| Section | Line | x0 (pt) | Deviation (pt) | Result |", "|---|---|---:|---:|---|", *rows[tier]])
    if heading_targets and geometry.headings:
        report.extend(["", "## Section heading rows (heading 行)", ""])
        report.append("x0 against the measured page margin; glyph height against the target-measured glyph")
        report.append("height; local y gaps (content → rule, rule → heading, heading → content) against the")
        report.append("target-measured gaps. Absolute heading y is reported for reference only: source content")
        report.append("above a heading legitimately differs from the target's content, so only local gaps gate.")
        report.extend([
            "| Section | x0 (pt) | Glyph height (pt) | Rule gap above (pt) | Rule → heading (pt) | Heading → content (pt) | Top y (pt) | Result |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ])
        for position, heading in enumerate(geometry.headings):
            checks = [abs(heading.x0_pt - heading_targets["x0_pt"]) <= 1, abs(heading.font_height_pt - heading_targets["font_height_pt"]) <= 1]
            cells = [f"{heading.x0_pt:.3f} (Δ{abs(heading.x0_pt - heading_targets['x0_pt']):.3f})"]
            cells.append(f"{heading.font_height_pt:.3f} (Δ{abs(heading.font_height_pt - heading_targets['font_height_pt']):.3f})")
            for observed, key in (
                (heading.rule_gap_above_pt, "header_rule_gap_above_pt" if position == 0 else "section_rule_gap_above_pt"),
                (heading.rule_gap_below_pt, "rule_gap_below_pt"),
                (heading.content_gap_below_pt, "content_gap_below_pt"),
            ):
                target = heading_targets.get(key)
                if observed is None or target is None:
                    cells.append("n/a")
                else:
                    delta = abs(observed - target)
                    checks.append(delta <= 1)
                    cells.append(f"{observed:.3f} (Δ{delta:.3f})")
            cells.append(f"{heading.top_pt:.3f}")
            cells.append("PASS" if all(checks) else "FAIL")
            report.append(f"| {heading.label} | " + " | ".join(cells) + " |")
    if body_scaffold is not None:
        contracts = {h.verbatim.casefold(): h for h in body_scaffold.headings}
        # Compile-level case decision (same rule the compiler applies in
        # _body_css): the whole target heading set is all-uppercase → the
        # target-measured contract is uppercase.
        all_upper = all(
            heading.verbatim == heading.verbatim.upper() and any(c.isalpha() for c in heading.verbatim)
            for heading in body_scaffold.headings
        )
        report.extend(["", "## Heading case（标题大小写，目标实测派生）", "",
            "| Heading | Target verbatim | Rendered | Case class | Result |", "|---|---|---|---|---|"])
        for heading in geometry.headings:
            contract = contracts.get(heading.rendered_text.casefold())
            if contract is None:
                # No verbatim same-name target heading (source and target
                # word the section differently): the case contract is the
                # compile-level decision over the whole target heading set
                # (all-uppercase target set compiles uppercase), so the row
                # stays transparent against that decision instead of being
                # silently skipped.
                compile_case = "uppercase" if all_upper else "sentence"
                rendered_case = "uppercase" if heading.rendered_text == heading.rendered_text.upper() and any(c.isalpha() for c in heading.rendered_text) else "sentence"
                report.append(
                    f"| {heading.label} | —（源节标题与目标标题无逐字同文名，按编译级合同） | "
                    f"{heading.rendered_text} | {rendered_case} (target {compile_case}) | {'PASS' if rendered_case == compile_case else 'FAIL'} |"
                )
                continue
            target_case = "uppercase" if contract.verbatim == contract.verbatim.upper() and any(c.isalpha() for c in contract.verbatim) else "sentence"
            rendered_case = "uppercase" if heading.rendered_text == heading.rendered_text.upper() and any(c.isalpha() for c in heading.rendered_text) else "sentence"
            passed = target_case == rendered_case
            report.append(f"| {heading.label} | {contract.verbatim} | {heading.rendered_text} | {rendered_case} (target {target_case}) | {'PASS' if passed else 'FAIL'} |")
        rule_rows = [
            (heading, observation) for heading, observation in zip(body_scaffold.headings, geometry.headings)
            if heading.rule_below_gap_pt is not None and observation.rule_below_gap_pt is not None
        ]
        report.extend(["", "## Rule rows（rule 行：全宽 + 标题下实测间距）", "",
            "目标 = 全宽（左缘 = 实测左页边距，右缘 = 页宽 − 实测右页边距）+ 标题下实测间距。", "",
            "| Heading | Rule x0 (pt) | Rule x1 (pt) | 标题下间距 (pt) | Result |", "|---|---:|---:|---:|---|"])
        for heading, observation in rule_rows:
            checks = [
                abs(observation.x0_pt - heading.rule_below_x0_pt) <= 1,
                abs(observation.rule_below_x1_pt - heading.rule_below_x1_pt) <= 1,
                abs(observation.rule_below_gap_pt - heading.rule_below_gap_pt) <= 1,
            ]
            report.append(
                f"| {heading.label} | {observation.rule_below_x0_pt:.3f} (Δ{abs(observation.rule_below_x0_pt - heading.rule_below_x0_pt):.3f}) | "
                f"{observation.rule_below_x1_pt:.3f} (Δ{abs(observation.rule_below_x1_pt - heading.rule_below_x1_pt):.3f}) | "
                f"{observation.rule_below_gap_pt:.3f} (Δ{abs(observation.rule_below_gap_pt - heading.rule_below_gap_pt):.3f}) | "
                f"{'PASS' if all(checks) else 'FAIL'} |"
            )
        if not rule_rows:
            report.append("_none_（目标版式无标题下规则线）")
    if entry_right_x1_pt is not None:
        entry_rows = [
            line for line in geometry.lines
            if (line.tier == "metadata" or line.right_column) and line.x1
        ]
        report.extend(["", "## Entry two-column rows (entry 行)", ""])
        report.append(
            f"Right-column right edge against the target-measured {entry_right_x1_pt:.3f} pt. "
            "Left-column x0 is gated by the Level-1 tier above (same measured 46.909-class target); "
            "line spacing is gated by the compiled measured line heights and the hard gates. "
            "Geometry only: right-column content is the candidate's own fields."
        )
        report.extend(["| Section | Row | Right edge (pt) | Overshoot (pt) | Result |", "|---|---|---:|---:|---|"])
        for line in entry_rows:
            # Owner ruling 2026-09-10 (E→F thirteenth freeze): the right-edge
            # target is the measured content boundary — row right edges must
            # not cross it; per-row exact right-alignment is not gated (the
            # right column carries the candidate's own fields, whose right
            # edge legitimately varies with content). Same overshoot semantics
            # as the fit gate, so table and fit cannot disagree.
            overshoot = max(0.0, line.x1 - entry_right_x1_pt)
            report.append(f"| {line.label} | {_clean(line.text)} | {line.x1:.3f} | {overshoot:.3f} | {'PASS' if overshoot <= 1 else 'FAIL'} |")
        if not entry_rows:
            report.append("_none_")
    if body_scaffold is not None:
        # Char-level evidence (owner ruling 2026-09-11, E→F final rerun): the
        # target declares its separator only when the standalone element
        # repeats ≥2 times on the contact row; the rendered verification
        # counts the same character on the rendered contact line at the same
        # bar. Spacing around the separator collapses asymmetrically in
        # extracted text, so the character count — not the space-wrapped
        # token — is the honest measured contract.
        rendered_contact = geometry.contact_line_text
        if body_scaffold.contact_separator is not None:
            separator_char = body_scaffold.contact_separator.strip()
            rendered_count = rendered_contact.count(separator_char)
            report.extend(["", "## Contact separator（联系行分隔符呈现，目标实测派生）", "",
                "目标：独立分隔符元素在联系行重复 ≥2 次（每对相邻值之间）；渲染验证 = 渲染联系行携带", "",
                "同一字符 ≥2 次（与目标声明门槛对称）。", "",
                f"| Target separator | Rendered occurrences on contact line | Result |", "|---|---|---|",
                f"| {separator_char!r} | {rendered_count}（{_clean(rendered_contact)}） | "
                f"{'PASS' if rendered_count >= 2 else 'FAIL'}"])
        else:
            report.extend(["", "## Contact separator（联系行分隔符呈现，目标实测派生）", "",
                "_none_（目标联系行无重复独立分隔符元素；无声明则不门控）"])
    return "\n".join(report) + "\n"


def remove_unfilled_header_slots(candidate_html: str, required_slots: list[str]) -> str:
    """Drop optional target slots the source does not supply; never invent fallback facts."""
    tree = lxml_html.document_fromstring(candidate_html)
    required = set(required_slots)
    for slot in tree.xpath("//header//*[@data-slot]"):
        if slot.get("data-slot") in required:
            continue
        contact = next(
            (node for node in slot.iterancestors() if "contact-item" in (node.get("class") or "").split() or "c1-contact-item" in (node.get("class") or "").split()),
            None,
        )
        remove = contact if contact is not None else slot
        parent = remove.getparent()
        if parent is not None:
            parent.remove(remove)
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>")


def remove_unused_placeholders(candidate_html: str) -> str:
    """Remove unfilled generic template leaves; source-owned leaves are untouched."""
    tree = lxml_html.document_fromstring(candidate_html)
    placeholder = re.compile(r"\[[A-Z][A-Z0-9 _/\-–—]+\]")
    for node in tree.xpath("//body//*[not(*) and not(@data-source-line)]"):
        text = " ".join(node.itertext()).strip()
        if placeholder.search(text) and not any(character.isalnum() for character in placeholder.sub("", text)):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>")


def _list_slot_classes(template_html: str) -> set[str]:
    """Section classes whose target-template structure declares a list slot."""
    tree = lxml_html.document_fromstring(template_html)
    return {
        section.get("class") or ""
        for section in _section_nodes(tree)
        if section.xpath(
            ".//ul[contains(concat(' ', normalize-space(@class), ' '), ' entry-list ')]"
        )
    }


def remove_entry_leading_dashes(
    candidate_html: str,
    template_html: str | None = None,
    *,
    convert_bullets: bool = False,
) -> str:
    """Drop source separators when the renderer already supplies structure or markers.

    A leading list-marker dash on a body text line becomes the template's
    standard CSS bullet (.entry-item marker class) only where the target
    structure declares a list slot: the line's section class must be one the
    target template renders with entry lists (owner refinement 2026-09-10).
    Sections without a target list slot lose the dash silently — presentation
    follows the target; the literal dash character never survives. Structural
    entry-role/entry-program separators keep their frozen strip-only behavior.
    """
    tree = lxml_html.document_fromstring(candidate_html)
    list_slot_classes = _list_slot_classes(template_html) if template_html else None
    for node in tree.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' entry-role ') "
        "or contains(concat(' ', normalize-space(@class), ' '), ' entry-program ')]"
    ):
        if node.text:
            node.text = re.sub(r"^\s*[-–—]\s*", "", node.text, count=1)
    for node in tree.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' entry-item ')]"
    ):
        if node.text:
            node.text = re.sub(r"^\s*[-–—]\s*", "", node.text, count=1)
    for node in (
        descendant
        for section in _section_nodes(tree)
        for descendant in section.xpath(".//*[@data-source-line]")
    ):
        classes = (node.get("class") or "").split()
        if not node.text or any(
            name in classes
            for name in ("section-heading", "entry-role", "entry-program", "entry-item")
        ):
            continue
        if not re.match(r"^\s*[-–—]\s*", node.text):
            continue
        node.text = re.sub(r"^\s*[-–—]\s*", "", node.text, count=1)
        section_classes = {
            name for ancestor in (node, *node.iterancestors())
            for name in (ancestor.get("class") or "").split()
            if name.endswith("-section") or name == "section-block"
        }
        declared = list_slot_classes is None or bool(section_classes & list_slot_classes)
        if declared:
            node.set("class", " ".join(dict.fromkeys([*classes, "entry-item"])))
    for node in tree.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' section-heading ')]"
    ):
        if node.text:
            node.text = re.sub(r"\s*[-–—]\s*$", "", node.text, count=1)
    if convert_bullets:
        # General rule (F→E cold start, 2026-09-11): presentation follows the
        # target — the same predicate as the dash rule above. When the target
        # declares a measured bullet design, the compiled template marker
        # supplies the presentation and a literal leading list glyph must not
        # survive in the text (the seventh-freeze retention ruling is its own
        # stated precondition: no measured target basis, glyph retained).
        # Structure-determined via the fourth-freeze bullet-unit discovery
        # (any ul whose li descendants carry provenanced text); no pair
        # vocabulary, no pair values.
        glyph_class = "[" + re.escape("".join(sorted(BULLET_GLYPHS))) + "]"
        for unit in tree.xpath("//ul[li[@data-source-line]]"):
            for li in unit.xpath(".//li[@data-source-line]"):
                if li.text and re.match(rf"^\s*{glyph_class}", li.text):
                    li.text = re.sub(rf"^\s*{glyph_class}\s*", "", li.text, count=1)
                    continue
                for descendant in li.iter():
                    if descendant is li:
                        continue
                    if descendant.text and re.match(rf"^\s*{glyph_class}", descendant.text):
                        descendant.text = re.sub(rf"^\s*{glyph_class}\s*", "", descendant.text, count=1)
                        break
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>")


def restore_compiler_header_attributes(template_html: str, candidate_html: str) -> str:
    """Restore C1-owned header attributes while retaining Filler-owned text/provenance."""
    template = lxml_html.document_fromstring(template_html)
    candidate = lxml_html.document_fromstring(candidate_html)
    template_rows = template.xpath("//header//*[@data-c1-role]")
    candidate_rows = candidate.xpath("//header//*[@data-c1-role]")
    by_role: dict[str, list[Any]] = {}
    for row in template_rows:
        by_role.setdefault(row.get("data-c1-role"), []).append(row)
    seen: dict[str, int] = {}
    for row in candidate_rows:
        role = row.get("data-c1-role")
        index = seen.get(role, 0)
        seen[role] = index + 1
        if index < len(by_role.get(role, [])):
            source = by_role[role][index]
            for name in ("class", "data-align", "data-evidence-ids"):
                if source.get(name) is not None:
                    row.set(name, source.get(name))
    headers = candidate.xpath("//header")
    template_headers = template.xpath("//header")
    if headers and template_headers:
        for name in ("class", "data-c1-dna"):
            if template_headers[0].get(name) is not None:
                headers[0].set(name, template_headers[0].get(name))
    return lxml_html.tostring(candidate, encoding="unicode", doctype="<!DOCTYPE html>")


def _probe_html(template: str, probe: str) -> tuple[str, dict[str, list[str]]]:
    slot_names = ("name", "title", "tagline", "location", "phone", "envelope", "github", "linkedin")
    lengths = {
        "short": {slot: 1 for slot in slot_names},
        "fit": {slot: 1 for slot in slot_names},
        "long": {"name": 3, "title": 4, "tagline": 5, "location": 3, "phone": 2, "envelope": 1, "github": 1, "linkedin": 1},
    }
    tree = lxml_html.document_fromstring(template)
    markers: dict[str, list[str]] = {}
    for row in tree.xpath("//*[@data-c1-role]"):
        role = row.get("data-c1-role")
        markers.setdefault(role, [])
        for slot in row.xpath(".//*[@data-slot]"):
            name = slot.get("data-slot").upper()
            value = " ".join(
                f"C1{name}{index}" for index in range(1, lengths[probe][slot.get("data-slot")] + 1)
            )
            slot.text = value
            markers[role].extend(value.split())
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>"), markers


def _observe_probe(pdf: Path, markers: dict[str, list[str]], row_count: int, page_width: float) -> GeometryObservation:
    lines = _page_text_lines(pdf, max_lines=30)
    tops: dict[str, float] = {}
    bounds: dict[str, tuple[float, float, float, float]] = {}
    wrapped: list[str] = []
    occupied: set[float] = set()
    overflow = False
    for role, tokens in markers.items():
        matched = [
            row for row in lines
            if any(token in " ".join(str(word["text"]) for word in row["words"]) for token in tokens)
        ]
        role_tops = sorted({float(row["top"]) for row in matched})
        occupied.update(role_tops)
        if role_tops:
            tops[role] = role_tops[0]
            bounds[role] = (
                min(float(word["x0"]) for row in matched for word in row["words"]),
                min(float(row["top"]) for row in matched),
                max(float(word["x1"]) for row in matched for word in row["words"]),
                max(float(row["top"]) + max(float(word["height"]) for word in row["words"]) for row in matched),
            )
        if len(role_tops) > 1:
            wrapped.append(role)
        for row in matched:
            overflow = overflow or min(float(word["x0"]) for word in row["words"]) < -0.5
            overflow = overflow or max(float(word["x1"]) for word in row["words"]) > page_width + 0.5
    overlaps: list[str] = []
    roles = list(bounds)
    for index, left_role in enumerate(roles):
        left = bounds[left_role]
        for right_role in roles[index + 1:]:
            right = bounds[right_role]
            if left[0] < right[2] and right[0] < left[2] and left[1] < right[3] and right[1] < left[3]:
                overlaps.append(f"row overlap: {left_role}={left!r}; {right_role}={right!r}")
    return GeometryObservation(tops, len(occupied) if occupied else row_count, tuple(wrapped), overflow, bounds, tuple(overlaps))


def _target_measured_font_families(summary: dict[str, Any]) -> list[str]:
    """Families actually measured in the target PDF, icon fonts excluded.

    Icon glyphs surface as non-unicode '(cid:' content on an icon-font family
    (Font Awesome); the icon-font hard gate owns them, not the typography
    contract. Everything here comes from target measurement only.
    """
    families: list[str] = []
    for style in (summary.get("style_groups") or {}).values():
        family = str(style.get("font_family") or "").strip()
        if not family:
            continue
        key = family.casefold()
        if "awesome" in key or "cid:" in key:
            continue
        if family not in families:
            families.append(family)
    return families


def _pdf_font_name_matches(generated_fonts: list[str], family: str) -> bool:
    """Match generated-PDF font resources against a requested family.

    Same resolution order as the frozen module's own check: flatten the
    resource names (subset prefixes included, spaces dropped, casefolded) and
    test containment of the family key — 'AAAAAA+Roboto-BoldMT' carries the
    'roboto' family. A substitute family (Arial for Roboto) never contains it.

    Same-design variants (E→D cold start, 2026-09-11): a multi-word measured
    family (e.g. 'Charter BT') and the embedded resource of the same design
    (e.g. 'Charter-Roman') share the family head token — that is the A-era
    approved-substitution map demoted to structure (a declared, same-design
    variant, not a silent pass). A different design (Arial for Roboto) does
    not share the head token and still fails.
    """
    resolved = " ".join(generated_fonts).casefold().replace(" ", "")
    if family.casefold().replace(" ", "") in resolved:
        return True
    head = family.casefold().split()[0] if family.split() else ""
    return bool(head) and len(head) >= 3 and head in resolved


def fail_closed_font_gate(
    gates: HardGateResult,
    template_html: str,
    summary: dict[str, Any],
) -> HardGateResult:
    """Fail-closed required-font check layered on the frozen hard gates.

    run_hard_gates (frozen a_pipeline pyc) still reports substitutions
    transparently; this stage-1 owner ruling upgrades the typography contract:
    the required font set is target-measured families ∩ template-requested
    families, and every required family must be embedded in the generated PDF.
    The A-era approved-substitution map is demoted to an explicit, transparent
    declaration — a required family served by a substitute is a FAIL, not a
    silent pass (merit doctrine: substitution needs its own measured basis).
    """
    fonts_by_render = {
        key: (value or {}).get("fonts", {})
        for key, value in (gates.independent or {}).items()
    }
    required = [
        family for family in _target_measured_font_families(summary)
        if family in _requested_font_families(template_html)
    ]
    if not required:
        return gates
    failures = list(gates.failures)
    annotations: dict[str, Any] = {"required_fonts": required}
    for key, fonts in fonts_by_render.items():
        generated = list(fonts.get("generated_pdf_fonts") or [])
        substitutions = list(fonts.get("approved_substitutions_used") or [])
        if substitutions:
            # Explicit declarative substitution record — never a silent pass.
            annotations[f"{key}_declared_substitutions"] = substitutions
        for family in required:
            if not _pdf_font_name_matches(generated, family):
                got = sorted({font.split("+", 1)[-1] for font in generated})
                failures.append(GateFailure(
                    code="font_gate_fail_closed",
                    details={
                        "render": key, "family": family,
                        "basis": "target-measured ∩ template-requested",
                        "generated_pdf_fonts": generated,
                        "note": "required font not embedded (substitute families are explicit, not approved)",
                        "available": got,
                    },
                ))
    if len(failures) == len(gates.failures):
        return gates
    return gates.model_copy(update={"passed": False, "failures": failures})


def per_line_render_stability(
    first_pdf: Path,
    second_pdf: Path,
    tolerance_pt: float = 0.1,
) -> tuple[bool, dict[str, Any]]:
    """Double-render determinism, per text line (stage-1 owner ruling).

    The thirteenth freeze measured ~3.7pt right-edge drift between two renders
    of one artifact (font-load timing), which drowns the ≤1pt acceptance
    criterion. The export now blocks on virtual-time-budget; this gate checks
    the outcome directly: every matched text line's x0/x1 must repeat within
    tolerance across two independent renders of the same HTML.
    """
    first_lines, _ = _pdf_lines_and_marks(first_pdf)
    second_lines, _ = _pdf_lines_and_marks(second_pdf)
    deltas_x0: list[float] = []
    deltas_x1: list[float] = []
    mismatches: list[dict[str, Any]] = []
    if len(first_lines) != len(second_lines):
        mismatches.append({"code": "line_count", "first": len(first_lines), "second": len(second_lines)})
    for index, (first, second) in enumerate(zip(first_lines, second_lines)):
        if " ".join(str(first["text"]).split()) != " ".join(str(second["text"]).split()):
            mismatches.append({"code": "line_text", "index": index, "first": first["text"], "second": second["text"]})
            continue
        delta_x0 = abs(float(first["x0"]) - float(second["x0"]))
        delta_x1 = abs(float(first["x1"]) - float(second["x1"]))
        deltas_x0.append(delta_x0)
        deltas_x1.append(delta_x1)
        if delta_x0 > tolerance_pt or delta_x1 > tolerance_pt:
            mismatches.append({
                "code": "line_geometry", "index": index, "line": first["text"][:60],
                "delta_x0_pt": round(delta_x0, 4), "delta_x1_pt": round(delta_x1, 4),
                "first": {"x0": first["x0"], "x1": first["x1"]},
                "second": {"x0": second["x0"], "x1": second["x1"]},
            })
    stable = not mismatches and (not deltas_x0 or max(deltas_x0) <= tolerance_pt) and (not deltas_x1 or max(deltas_x1) <= tolerance_pt)
    details = {
        "tolerance_pt": tolerance_pt,
        "lines_compared": len(deltas_x0),
        "max_delta_x0_pt": round(max(deltas_x0), 4) if deltas_x0 else None,
        "max_delta_x1_pt": round(max(deltas_x1), 4) if deltas_x1 else None,
        "mismatches": mismatches[:20],
    }
    return stable, details


def pinned_export_environment(summary: dict[str, Any]) -> dict[str, Any]:
    """Pinned Chrome export environment with font-load determinism forced on.

    --virtual-time-budget lets the print run only after the renderer's virtual
    clock has drained pending font loads; the data-URI @font-face injection is
    synchronous to parse but Chrome still decodes/fonts asynchronously, which
    measurably shifted the date column by ~3.7pt between renders (run
    20260910T182945Z, thirteenth freeze).
    """
    environment = _pinned_chrome_environment(summary)
    environment["flags"] = [*environment.get("flags", []), "--virtual-time-budget=10000"]
    return environment


def run(
    target: Path = DEFAULT_TARGET,
    source: Path = DEFAULT_SOURCE,
    output: Path | None = None,
    *,
    render_budget: int = 12,
    time_budget_seconds: float = 60,
    topology_override: Path | None = None,
    filler_override: Path | None = None,
) -> Path:
    """Run one C1 pair: one Architect call, zero-API fitting, one Filler call."""
    from datetime import UTC, datetime

    from app.ingestion.pdf_reader import read_pdf_text
    from tests.experiments.a_pipeline import (
        RUNS, _analyze_target, _artifact_ref, _export_pinned_html_to_pdf, _filler_prompt,
        _html_text, _image_sources, _inject_icon_fonts, _inject_local_fonts,
        _normalize_provenance_annotations, _normalize_section_order, _pinned_chrome_environment,
        _render_pages, _require_geometry, _restore_template_presentation, _side_by_side, _visual_evidence_board,
        build_filler_context, build_format_summary, run_hard_gates,
    )
    from tests.experiments.b_pipeline import (
        _ensure_icon_font_family, _filler_call, _review_call,
        _seed_template, template_contamination, template_slot_gaps, validate_filler_header_structure,
    )
    from tests.experiments.deepseek import _chat_content, visual_client
    from tests.experiments.fill_plan import (
        analyze_candidate_provenance, deduplicate_candidate_html, normalize_header_element,
        normalize_section_headings, provenance_report, validate_candidate_html,
    )
    from tests.experiments.refinement import validate_filler_conformance

    load_dotenv(ROOT / ".env")
    if not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")):
        raise RuntimeError("Missing live configuration: DEEPSEEK_API_KEY or OPENAI_API_KEY")
    model = os.getenv("C_PIPELINE_MODEL") or os.getenv("B_PIPELINE_MODEL") or os.getenv("A_PIPELINE_MODEL") or "deepseek-v4-flash-vision-exp"
    reviewer_client, reviewer_model = visual_client() or (None, model)
    run_dir = output or RUNS / datetime.now(UTC).strftime("c_pipeline_D_to_E_%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    shutil.copy2(target, run_dir / "target.pdf")

    evidence, raw = _analyze_target(target, run_dir, use_persistent_cache=True)
    summary = build_format_summary(evidence, raw, target)
    _require_geometry(summary)
    run_dir.joinpath("format_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    source_text, target_text = read_pdf_text(source), read_pdf_text(target)
    run_dir.joinpath("source_text.txt").write_text(source_text, encoding="utf-8")
    scaffold = derive_header_scaffold(target, summary, required_slots=required_slot_kinds(source_text))
    required = required_slot_kinds(source_text)
    run_dir.joinpath("header_scaffold.json").write_text(json.dumps([row.model_dump(mode="json") for row in scaffold], indent=2), encoding="utf-8")
    margin_left = float(summary.get("margins_pt", {}).get("default", {}).get("left", 0))
    page_width = float(summary["pages"][0]["width_pt"])
    body_scaffold = derive_body_scaffold(target, summary, header_scaffold=scaffold)
    run_dir.joinpath("body_scaffold.json").write_text(
        json.dumps(body_scaffold.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    body_rejections: list[dict[str, Any]] = []
    body_topology = None
    for candidate in derive_body_topology_candidates(body_scaffold, margin_left, page_width).candidates:
        failures = validate_body_topology(candidate, body_scaffold, margin_left, page_width)
        if failures:
            body_rejections.append({"candidate_id": candidate.candidate_id, "failures": failures})
            continue
        body_topology = candidate
        break
    run_dir.joinpath("body_topology_rejections.json").write_text(json.dumps(body_rejections, indent=2), encoding="utf-8")
    if body_topology is None:
        run_dir.joinpath("run_metadata.json").write_text(json.dumps({
            "experiment": "c_pipeline", "source": str(source), "target": str(target),
            "models": {"layout_architect": model, "filler": model},
            "calls": {"layout_architect": 0, "geometry_fit": 0, "filler": 0, "final_reviewer": 0},
            "hard_gates_passed": False, "stop_reason": "body_topology_rejected",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }, indent=2), encoding="utf-8")
        raise RuntimeError("No C1 body topology passed the measured validation gates")
    run_dir.joinpath("body_topology.json").write_text(body_topology.model_dump_json(indent=2), encoding="utf-8")
    entry_lefts = [
        float(row["bbox_pt"]["x0"])
        for row in summary.get("elements", [])
        if row.get("entry_path") and row.get("bbox_pt") and float(row["bbox_pt"]["x0"]) > margin_left
    ]
    target_body_x0 = body_scaffold.entry.left_x0_pt if body_scaffold.entry else min(entry_lefts)
    tier_targets = derive_body_tier_targets(target, target_body_x0)
    bullet_marker_target = "bullet_dot" in tier_targets

    # ADR-C1-2026-09-11 (draft, Phase 2 ablation): the Architect LLM call is
    # removed — the ablation proved the deterministic derivation equals the
    # adopted Architect topology on every frozen pair (rows, slots, measured
    # alignment; zero validate failures), and every other Architect candidate
    # was either redundant or deterministically rejected. Header topology is
    # derived from measured evidence + source semantics only (option C).
    # The --topology override remains the replay/approval channel.
    if topology_override:
        architect = TopologyCandidates(candidates=[HeaderTopology.model_validate_json(topology_override.read_text())])
    else:
        deterministic = apply_measured_alignment(
            derive_deterministic_topology(scaffold, required), scaffold
        )
        architect = TopologyCandidates(candidates=[deterministic])
    architect_calls = 0
    run_dir.joinpath("topology_candidates.json").write_text(architect.model_dump_json(indent=2), encoding="utf-8")
    base, seed_path = _seed_template(target)
    base = normalize_header_element(base)
    environment = pinned_export_environment(summary)
    fitted: list[tuple[float, HeaderTopology, FitResult]] = []
    rejected: list[dict[str, Any]] = []
    slot_relocations: list[dict[str, Any]] = []

    def consider(candidates: list[HeaderTopology], attempt: int) -> None:
        for topology in candidates:
            # Owner ruling 2026-09-10: normalize and validate BEFORE the measured
            # alignment override — an invented target row must be relocated or
            # rejected with details, never crash the override.
            topology, relocations = relocate_misplaced_slots(topology, scaffold)
            if relocations:
                slot_relocations.append({"attempt": attempt, "candidate_id": topology.candidate_id, "actions": relocations})
            topology = apply_measured_alignment(topology, scaffold)
            failures = validate_topology(topology, scaffold, required)
            if failures:
                rejected.append({"attempt": attempt, "candidate_id": topology.candidate_id, "failures": failures})
                continue
            candidate_dir = run_dir / "topologies" / (topology.candidate_id if attempt == 1 else f"retry_{topology.candidate_id}")
            candidate_dir.mkdir(parents=True, exist_ok=True)
            render_number = 0

            def evaluate(params: FitParameters, probe: str) -> GeometryObservation:
                nonlocal render_number
                render_number += 1
                template = compile_header_template(base, topology, scaffold, summary, params, body_scaffold=body_scaffold, bullet_marker_target=bullet_marker_target, required_slots=required)
                probe_document, markers = _probe_html(template, probe)
                probe_document = _inject_local_fonts(_ensure_icon_font_family(_inject_icon_fonts(probe_document)))
                html_path = candidate_dir / f"render_{render_number:02d}_{probe}.html"
                html_path.write_text(probe_document, encoding="utf-8")
                pdf = _export_pinned_html_to_pdf(html_path, html_path.with_suffix(".pdf"), environment)
                return _observe_probe(pdf, markers, len(topology.rows), page_width)

            result = fit_geometry(
                scaffold, len(topology.rows), evaluate,
                render_budget=render_budget, time_budget_seconds=time_budget_seconds,
            )
            candidate_dir.joinpath("fit.json").write_text(json.dumps({
                "passed": result.passed,
                "parameters": result.parameters.model_dump(mode="json"),
                "max_delta_pt": result.max_delta_pt,
                "renders": result.renders,
                "elapsed_seconds": result.elapsed_seconds,
                "failures": result.failures,
            }, indent=2), encoding="utf-8")
            if result.passed:
                fitted.append((result.max_delta_pt, topology, result))
            else:
                rejected.append({"attempt": attempt, "candidate_id": topology.candidate_id, "failures": list(result.failures)})

    consider(architect.candidates, 1)
    # ADR-C1-2026-09-11 (draft): no bounded Architect retry — with the
    # deterministic derivation there is no semantic candidate space to retry;
    # a rejected deterministic topology is a schema/contract failure and
    # surfaces as the run failure below with the rejection details.
    run_dir.joinpath("topology_rejections.json").write_text(json.dumps(rejected, indent=2), encoding="utf-8")
    run_dir.joinpath("topology_normalizations.json").write_text(json.dumps(slot_relocations, indent=2), encoding="utf-8")
    if not fitted:
        run_dir.joinpath("run_metadata.json").write_text(json.dumps({
            "experiment": "c_pipeline", "source": str(source), "target": str(target),
            "models": {"layout_architect": model, "filler": model},
            "calls": {"layout_architect": architect_calls, "geometry_fit": 0, "filler": 0, "final_reviewer": 0},
            "usage": {"layout_architect": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "attempts": 0}},
            "hard_gates_passed": False,
            "stop_reason": "topology_rejected",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }, indent=2), encoding="utf-8")
        raise RuntimeError("No C1 topology passed deterministic topology and calibration gates")
    _, topology, fit = min(fitted, key=lambda row: (row[0], row[1].candidate_id))
    template = compile_header_template(base, topology, scaffold, summary, fit.parameters, body_scaffold=body_scaffold, bullet_marker_target=bullet_marker_target, required_slots=required)
    contamination = template_contamination(template, source_text, target_text)
    slot_gaps = template_slot_gaps(template, source_text, contact_icons_present=body_scaffold.contact_icons_present)
    if contamination or slot_gaps:
        raise RuntimeError(f"selected topology failed template gates: contamination={contamination}, slots={slot_gaps}")
    run_dir.joinpath("selected_topology.json").write_text(topology.model_dump_json(indent=2), encoding="utf-8")
    run_dir.joinpath("empty_template.html").write_text(template, encoding="utf-8")

    target_pages = _render_pages(run_dir / "target.pdf", run_dir, "target")
    target_board = _visual_evidence_board(run_dir / "target.pdf", target_pages, summary, run_dir, "target")
    fill_context = build_filler_context(
        source_text=source_text, template_html=template, champion_filled_html="", issue=None,
        action="initial_fill", resolved_measurements=[], target_evidence=_artifact_ref(target_board) or {},
        champion_evidence=None, protected_rubrics=[], gate_failures=[], same_issue_outcomes=[], repair_outcomes=[],
    )
    filler_calls = 0
    filler_usage: dict[str, int] = {}
    attempt_log: list[dict[str, Any]] = []
    normalization_log: list[dict[str, Any]] = []
    details: list[str] = []
    filled = ""
    ownership: dict[str, str] = {}
    max_filler_calls = 1 if filler_override else 2
    for filler_attempt in range(1, max_filler_calls + 1):
        context = fill_context if filler_attempt == 1 else build_filler_context(
            source_text=source_text, template_html=template, champion_filled_html=filled, issue=None,
            action="bounded_gate_retry", resolved_measurements=[], target_evidence=_artifact_ref(target_board) or {},
            champion_evidence=None, protected_rubrics=[],
            gate_failures=[{"code": "c1_gate_failure", "details": failure} for failure in details],
            same_issue_outcomes=[], repair_outcomes=[],
        )
        if filler_override:
            raw = filler_override.read_text(encoding="utf-8")
            usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "attempts": 0}
        else:
            raw, usage = _filler_call(c1_filler_prompt(_filler_prompt(context)), model)
            filler_calls += 1
        for key, value in usage.items():
            if isinstance(value, int):
                filler_usage[key] = filler_usage.get(key, 0) + value
        run_dir.joinpath(f"filler_raw_attempt_{filler_attempt}.html").write_text(raw, encoding="utf-8")
        filled = _restore_template_presentation(raw, template)
        filled = restore_compiler_header_attributes(template, filled)
        filled = normalize_header_element(filled)
        filled = _normalize_provenance_annotations(filled, source_text)
        filled, block_actions = normalize_source_block_owners(filled, source_text)
        filled, inherit_actions = inherit_container_source_line(filled, source_text)
        filled, split_actions = split_merged_source_line_nodes(filled, source_text)
        filled, punctuation_actions = remove_unprovenanced_punctuation_nodes(filled)
        filled, heading_actions = inject_missing_source_headings(filled, template, source_text)
        filled, _ = deduplicate_candidate_html(filled, source_text)
        filled = merge_unprovenanced_sibling_headings(filled, source_text)
        filled = normalize_source_section_semantics(filled, source_text)
        filled = _normalize_section_order(filled, template, source_text)
        filled = remove_unfilled_header_slots(filled, required)
        filled = remove_unused_placeholders(filled)
        filled = remove_entry_leading_dashes(filled, template, convert_bullets=bullet_marker_target)
        run_dir.joinpath(f"filler_normalized_attempt_{filler_attempt}.html").write_text(filled, encoding="utf-8")
        normalization_log.append({"attempt": filler_attempt, "actions": [*block_actions, *inherit_actions, *split_actions, *punctuation_actions, *heading_actions]})

        analysis = analyze_candidate_provenance(filled, source_text, allowed_text=_html_text(re.sub(r"\[[^]]+\]", "", template))[0])
        errors, ownership = validate_candidate_html(filled, source_text, analysis=analysis)
        errors += c1_provenance_contract_failures(filled, source_text, analysis)
        errors += validate_filler_conformance(template, filled)
        errors += validate_filler_header_structure(template, filled)
        new_images = sorted(_image_sources(filled) - _image_sources(template))
        if new_images:
            errors.append("Filler introduced image assets")
        details = [*errors, *[row["code"] for row in analysis["findings"]]]
        attempt_log.append({"attempt": filler_attempt, "usage": usage, "gate_failures": details})
        if not details:
            break
    run_dir.joinpath("filler_raw.html").write_text(raw, encoding="utf-8")
    run_dir.joinpath("filler_normalized.html").write_text(filled, encoding="utf-8")
    run_dir.joinpath("filler_attempts.json").write_text(json.dumps(attempt_log, indent=2), encoding="utf-8")
    run_dir.joinpath("normalization_log.json").write_text(json.dumps(normalization_log, indent=2), encoding="utf-8")
    if details:
        run_dir.joinpath("filler_gate_failures.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
        run_dir.joinpath("run_metadata.json").write_text(json.dumps({
            "experiment": "c_pipeline", "source": str(source), "target": str(target),
            "models": {"layout_architect": model, "filler": model},
            "calls": {"layout_architect": architect_calls, "geometry_fit": 0, "filler": filler_calls, "final_reviewer": 0},
            "usage": {"layout_architect": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "attempts": 0}, "filler": filler_usage},
            "selected_topology": topology.candidate_id, "topology_dna": topology_fingerprint(topology),
            "fit_renders": fit.renders, "fit_max_delta_pt": fit.max_delta_pt,
            "hard_gates_passed": False, "stop_reason": "filler_failed_after_bounded_retry",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }, indent=2), encoding="utf-8")
        run_dir.joinpath("RUN_README.md").write_text(
            "# C1 header experiment\n\n- Hard gates: **FAILED**\n- Stop reason: `filler_failed_after_bounded_retry`\n"
            f"- Filler calls: **{filler_calls}**. No generated PDF was promoted.\n",
            encoding="utf-8",
        )
        raise RuntimeError("C1 Filler failed deterministic gates after its bounded retry")
    run_dir.joinpath("source_provenance.json").write_text(json.dumps({**provenance_report(ownership, source_text), "warnings": analysis["warnings"]}, indent=2), encoding="utf-8")

    heading_targets: dict[str, float] | None = None
    if body_scaffold.headings:
        heading_targets = {
            "x0_pt": margin_left,
            "font_height_pt": _median([heading.font_height_pt for heading in body_scaffold.headings]),
            "rule_gap_below_pt": next(
                (heading.rule_gap_below_pt for heading in body_scaffold.headings if heading.rule_gap_below_pt is not None), None
            ),
            "content_gap_below_pt": next(
                (heading.content_gap_below_pt for heading in body_scaffold.headings if heading.content_gap_below_pt is not None), None
            ),
            "header_rule_gap_above_pt": body_scaffold.headings[0].rule_gap_above_pt,
            "section_rule_gap_above_pt": next(
                (
                    heading.rule_gap_above_pt
                    for heading in body_scaffold.headings[1:]
                    if heading.rule_gap_above_pt is not None
                ),
                body_scaffold.headings[0].rule_gap_above_pt,
            ),
        }
        heading_targets = {key: value for key, value in heading_targets.items() if value is not None}

    def evaluate_body_fit(probe_html: str, render_number: int) -> Path:
        probe = _inject_local_fonts(_ensure_icon_font_family(_inject_icon_fonts(probe_html)))
        html_path = run_dir / f"body_fit_probe_{render_number:02d}.html"
        html_path.write_text(probe, encoding="utf-8")
        return _export_pinned_html_to_pdf(html_path, html_path.with_suffix(".pdf"), environment)

    entry_right_x1_pt = body_scaffold.entry.right_x1_pt if body_scaffold.entry else None
    body_fit = fit_body_line_geometry(
        filled,
        tier_targets,
        target_body_x0 - margin_left,
        evaluate_body_fit,
        render_budget=10,
        heading_targets=heading_targets,
        entry_right_x1_pt=entry_right_x1_pt,
        require_bullet_markers="bullet_dot" in tier_targets,
        body_scaffold=body_scaffold,
    )
    run_dir.joinpath("body_fit.json").write_text(json.dumps({
        "passed": body_fit.passed,
        "tier_targets_pt": tier_targets,
        "heading_targets_pt": heading_targets,
        "entry_right_x1_pt": entry_right_x1_pt,
        "renders": body_fit.renders,
        "indents_pt": body_fit.indents_pt,
        "unit_pads_pt": body_fit.unit_pads_pt,
        "heading_knobs_pt": body_fit.heading_knobs,
        "line_knobs_pt": body_fit.line_knobs,
        "right_row_corrections_pt": body_fit.right_row_corrections,
        "failures": body_fit.failures,
    }, indent=2), encoding="utf-8")
    if body_fit.geometry is not None:
        run_dir.joinpath("SECTION_X0_ACCEPTANCE.md").write_text(
            render_body_x0_acceptance(body_fit.geometry, tier_targets, heading_targets, entry_right_x1_pt,
                                      {key.split(":", 1)[1]: value for key, value in body_fit.line_knobs.items()},
                                      body_fit.right_row_corrections, body_scaffold),
            encoding="utf-8",
        )
    filled = body_fit.html
    if not body_fit.passed:
        run_dir.joinpath("run_metadata.json").write_text(json.dumps({
            "experiment": "c_pipeline", "source": str(source), "target": str(target),
            "models": {"layout_architect": model, "filler": model},
            "calls": {"layout_architect": architect_calls, "geometry_fit": 0, "filler": filler_calls, "final_reviewer": 0},
            "usage": {"layout_architect": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "attempts": 0}, "filler": filler_usage},
            "selected_topology": topology.candidate_id, "topology_dna": topology_fingerprint(topology),
            "fit_renders": fit.renders, "fit_max_delta_pt": fit.max_delta_pt,
            "body_fit_renders": body_fit.renders, "hard_gates_passed": False,
            "stop_reason": "body_line_x0_fit_failed", "elapsed_seconds": round(time.monotonic() - started, 3),
        }, indent=2), encoding="utf-8")
        raise RuntimeError("C1 body-line x0 fit failed within its render budget")

    rendered = _inject_local_fonts(_ensure_icon_font_family(_inject_icon_fonts(filled)))
    run_dir.joinpath("filled.html").write_text(rendered, encoding="utf-8")
    first_pdf = _export_pinned_html_to_pdf(run_dir / "filled.html", run_dir / "generated.pdf", environment)
    second_pdf = _export_pinned_html_to_pdf(run_dir / "filled.html", run_dir / "generated_2.pdf", environment)
    first_pages = _render_pages(first_pdf, run_dir, "generated")
    second_pages = _render_pages(second_pdf, run_dir, "generated_2")
    generated_board = _visual_evidence_board(first_pdf, first_pages, summary, run_dir, "generated")
    gates = run_hard_gates(source_text, template, rendered, first_pdf, second_pdf, first_pages, second_pages, target, summary, allow_slot_splitting=True)
    # Stage-1 fail-closed layers (owner ruling 2026-09-10/11): the frozen pyc
    # gates stay untouched; these post-checks may only turn a pass into a fail.
    gates = fail_closed_font_gate(gates, template, summary)
    stability_pass, stability_details = per_line_render_stability(first_pdf, second_pdf)
    if not stability_pass:
        gates = gates.model_copy(update={
            "passed": False,
            "failures": [*gates.failures, GateFailure(code="per_line_render_stability", details=stability_details)],
        })
    run_dir.joinpath("hard_gates.json").write_text(gates.model_dump_json(indent=2), encoding="utf-8")
    run_dir.joinpath("render_stability.json").write_text(json.dumps(stability_details, indent=2), encoding="utf-8")
    if not gates.passed:
        run_dir.joinpath("run_metadata.json").write_text(json.dumps({
            "experiment": "c_pipeline", "source": str(source), "target": str(target), "seed_template": str(seed_path),
            "models": {"layout_architect": model, "filler": model},
            "calls": {"layout_architect": architect_calls, "geometry_fit": 0, "filler": filler_calls, "final_reviewer": 0},
            "usage": {"layout_architect": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "attempts": 0}, "filler": filler_usage},
            "selected_topology": topology.candidate_id, "topology_dna": topology_fingerprint(topology),
            "fit_renders": fit.renders, "fit_max_delta_pt": fit.max_delta_pt,
            "body_fit_renders": body_fit.renders, "hard_gates_passed": False,
            "stop_reason": "final_render_hard_gates_failed", "elapsed_seconds": round(time.monotonic() - started, 3),
        }, indent=2), encoding="utf-8")
        raise RuntimeError("C1 final render failed hard gates")

    final_prompt = final_reviewer_prompt(source_text)
    final, final_usage = _review_call(
        reviewer_model,
        [
            {"role": "system", "content": "You are an independent read-only acceptance reviewer. Return JSON only."},
            {"role": "user", "content": _chat_content(final_prompt, [target_pages[0], *first_pages])},
        ],
        FinalReview,
        reviewer_client,
    )
    assert isinstance(final, FinalReview)
    run_dir.joinpath("final_review.json").write_text(final.model_dump_json(indent=2), encoding="utf-8")
    for index, page in enumerate(first_pages):
        _side_by_side(target_pages[min(index, len(target_pages) - 1)], page, run_dir / f"side_by_side_page_{index + 1:03d}.png")
    _side_by_side(target_pages[0], first_pages[0], run_dir / "side_by_side.png")
    run_dir.joinpath("run_metadata.json").write_text(json.dumps({
        "experiment": "c_pipeline", "source": str(source), "target": str(target), "seed_template": str(seed_path),
        "models": {"layout_architect": model, "filler": model, "final_reviewer": reviewer_model},
        "calls": {"layout_architect": architect_calls, "geometry_fit": 0, "filler": filler_calls, "final_reviewer": 1},
        "usage": {"layout_architect": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "attempts": 0}, "filler": filler_usage, "final_reviewer": final_usage},
        "selected_topology": topology.candidate_id, "topology_dna": topology_fingerprint(topology),
        "fit_renders": fit.renders, "fit_max_delta_pt": fit.max_delta_pt,
        "body_fit_renders": body_fit.renders,
        "hard_gates_passed": True, "final_reviewer_accepted": final.accepted,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }, indent=2), encoding="utf-8")
    run_dir.joinpath("RUN_README.md").write_text(
        f"# C1 header experiment\n\n- Hard gates: **PASSED**\n- Final Reviewer: **{'ACCEPTED' if final.accepted else 'REJECTED'}**\n"
        f"- Layout Architect calls: **{architect_calls}**; geometry-fit API calls: **0**; Filler calls: **{filler_calls}**.\n"
        "- Owner review: inspect `target.pdf`, `generated.pdf`, and `side_by_side.png`.\n",
        encoding="utf-8",
    )
    if not final.accepted:
        raise RuntimeError("C1 final visual reviewer rejected the generated output")
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="required for the live Adobe/model/Chrome experiment")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--render-budget", type=int, default=12)
    parser.add_argument("--time-budget-seconds", type=float, default=60)
    parser.add_argument("--topology", type=Path, help="replay one previously approved HeaderTopology JSON")
    parser.add_argument("--filler-html", type=Path, help="replay one previously recorded raw Filler output")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required because C1 makes live provider calls")
    print(run(
        args.target.resolve(), args.source.resolve(), args.output.resolve() if args.output else None,
        render_budget=args.render_budget, time_budget_seconds=args.time_budget_seconds,
        topology_override=args.topology.resolve() if args.topology else None,
        filler_override=args.filler_html.resolve() if args.filler_html else None,
    ))


if __name__ == "__main__":
    main()
