"""Measured target scaffolds: header/body/category-grid scaffolding.

Split out of ``c_pipeline`` (the C1 workflow). What survives is the part later
generations actually reuse: deriving measured header and body scaffolds, tier
targets, the two-column category grid, and the pinned export environment. The
C1 topology search, fitting loop and CLI are retired.
"""

from __future__ import annotations

from app.pipeline_e.resume_layout.render import _pinned_chrome_environment
from app.pipeline_e.resume_layout.state import (
    CONTACT_KIND_CHECKS,
    CONTACT_KIND_CHECKS as _CKC,
    _ROLE_WORDS,
)
from app.pipeline_e.resume_layout.target import _page_text_lines
import collections
import math
import re
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Literal


BULLET_GLYPHS = frozenset({"•", "◦", "·", "▪", "‣", "●", "∙"})


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
    # Measured presentation color of the heading's own style group (local PDF
    # character evidence; C2-0cC). None = unmeasured (explicit, never inferred).
    color_hex: str | None = None
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


class BodyScaffold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headings: list[BodyHeadingScaffold] = Field(min_length=1)
    entry: BodyEntryScaffold | None = None
    contact_icons_present: bool = True
    contact_separator: str | None = None
    category_grids: list[CategoryGridScaffold] = Field(default_factory=list)


class CategoryGridColumnScaffold(BaseModel):
    """Measured anchors of one category-grid column (C2-0cS, local PDF
    evidence): the shared right edge of the column's bold category labels
    (right-aligned label column) and the shared left edge of the column's
    value texts, plus the measured label→value gap used as the label cell's
    right margin. All values are measured clusters, never inferred."""

    model_config = ConfigDict(extra="forbid")

    label_right_x_pt: float
    value_x0_pt: float
    label_value_gap_pt: float
    evidence_ids: list[str] = Field(default_factory=list)


class CategoryGridScaffold(BaseModel):
    """Measured category-grid structure of one target section's content
    region (C2-0cS). Detected when the region's text rows carry ≥2 columns
    whose bold labels share a right edge and whose values share a left edge
    (the aligned-pair topology); uniform row pitch recorded. The scaffold
    carries GEOMETRY ONLY — no target label/value text, no per-cell colors
    (those stay a recorded capability gap; no target candidate facts enter
    the reusable state)."""

    model_config = ConfigDict(extra="forbid")

    heading_index: int = Field(ge=0)
    columns: list[CategoryGridColumnScaffold] = Field(min_length=2)
    row_pitch_pt: float
    column_splits_x_pt: list[float] = Field(default_factory=list)
    row_count: int = Field(ge=1)
    evidence_ids: list[str] = Field(default_factory=list)


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
    # Measured color of the row's own matched style group (C2-0cC); None =
    # unmeasured (explicit; documented fallback, never an inferred color).
    color_hex: str | None = None


SlotKind = Literal["name", "title", "tagline", "location", "phone", "envelope", "github", "linkedin"]


_CATEGORY_CLUSTER_TOLERANCE_PT = 2.0


_CATEGORY_MAX_LABEL_VALUE_GAP_PT = 10.0


_CATEGORY_ROW_PITCH_UNIFORMITY_PT = 1.0


_CATEGORY_ROW_TOLERANCE_PT = 2.0


def _measure_category_grids(
    target_pdf: Path,
    headings: list[BodyHeadingScaffold],
    page_height: float,
) -> list[CategoryGridScaffold]:
    """Bounded deterministic local measurement (local_pdf provenance) of
    category-grid structure per section content range: the provider evidence
    may drop wrapped grid cells (Resume D's right SKILLS POOL column), so the
    region's word rows are re-measured from the same input bytes. Geometry
    only; no target text is recorded."""
    import pdfplumber

    grids: list[CategoryGridScaffold] = []
    if len(headings) < 2:
        return grids
    with pdfplumber.open(target_pdf) as document:
        pages = document.pages
        for index, heading in enumerate(headings):
            following = headings[index + 1] if index + 1 < len(headings) else None
            page = pages[heading.page - 1]
            rows: list[list[dict[str, Any]]] = []
            current: list[dict[str, Any]] = []
            for word in sorted(
                page.extract_words(extra_attrs=["fontname"]),
                key=lambda word: (float(word["top"]), float(word["x0"])),
            ):
                top = float(word["top"])
                range_end = (
                    following.top_pt
                    if following is not None and following.page == heading.page
                    else page_height
                )
                if not (heading.top_pt + 2 <= top < range_end):
                    continue
                if current and top - float(current[-1]["top"]) <= _CATEGORY_ROW_TOLERANCE_PT:
                    current.append(word)
                else:
                    if current:
                        rows.append(current)
                    current = [word]
            if current:
                rows.append(current)
            word_rows = [
                [
                    {
                        "x0": float(word["x0"]),
                        "x1": float(word["x1"]),
                        "top": float(word["top"]),
                        "bold": "bold" in str(word.get("fontname", "")).lower(),
                    }
                    for word in sorted(row, key=lambda word: float(word["x0"]))
                ]
                for row in rows
            ]
            detected = detect_category_grid(word_rows)
            if detected is None:
                continue
            grids.append(
                CategoryGridScaffold(
                    heading_index=index,
                    columns=[
                        CategoryGridColumnScaffold(
                            label_right_x_pt=column["label_right_x_pt"],
                            value_x0_pt=column["value_x0_pt"],
                            label_value_gap_pt=column["label_value_gap_pt"],
                            evidence_ids=column["evidence"],
                        )
                        for column in detected["columns"]
                    ],
                    row_pitch_pt=detected["row_pitch_pt"],
                    column_splits_x_pt=detected["column_splits_x_pt"],
                    row_count=detected["row_count"],
                    evidence_ids=detected["evidence"],
                )
            )
    return grids


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
            color_hex=style.get("color_hex"),
            rule_top_pt=rule["top_pt"] if rule else None,
            rule_gap_above_pt=float(rule["gap_above_pt"]) if rule and rule["gap_above_pt"] is not None else None,
            rule_gap_below_pt=float(rule["gap_below_pt"]) if rule and rule["gap_below_pt"] is not None else None,
            rule_stroke_pt=float(rule["stroke_pt"]) if rule else None,
            rule_color_hex=str(rule["color"]) if rule else None,
            content_gap_below_pt=max(0.0, following["top"] - line["bottom"]) if following else None,
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
        category_grids=_measure_category_grids(
            target_pdf,
            headings,
            page_height=float(summary["pages"][0]["height_pt"]),
        ),
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
    # A measured header title may itself be left aligned; it is not a body boundary.
    title_boxes = [row["bbox_pt"] for row in header_elements if row.get("structural_role") == "title" and row.get("bbox_pt") and row.get("page", 1) == 1]
    margin_line_tops = [
        float(row["top"])
        for row in all_header_candidates
        if row["words"]
        and abs(min(float(word["x0"]) for word in row["words"]) - margin_left) <= 2
        and not _cap_line_is_contact(row)
        and not any(box["top"] <= row["top"] <= box["bottom"] and box["x0"] <= min(float(word["x0"]) for word in row["words"]) < box["x1"] for box in title_boxes)
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
            "color_hex": best.get("color_hex"),
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


def detect_category_grid(word_rows: list[list[dict[str, Any]]]) -> dict[str, Any] | None:
    """Detect a measured category-grid structure from one section's word rows
    (pure; unit-testable offline; no LLM/VLM, no pair-specific condition).

    A category grid is present when the rows carry ≥2 columns, each anchored
    by a CLUSTER of ≥2 bold label words sharing their right edge (a
    right-aligned label column, the aligned-pair topology) with the column's
    value texts starting at a shared left edge a measured gap to the right.
    The scaffold carries geometry only (label/value anchors, measured gap,
    uniform row pitch, and the documented column split derived as the
    midpoint of the two adjacent measured bounds: the previous column's
    max value extent and the next column's min label x0). Any missing
    cluster -> None (no grid; the caller keeps the ordinary list rendering).
    """
    label_columns: list[dict[str, Any]] = []
    grid_row_indices: list[int] = []
    for row_index, words in enumerate(word_rows):
        labels = [word for word in words if word.get("bold")]
        values = [word for word in words if not word.get("bold")]
        for label in labels:
            column = next(
                (
                    entry for entry in label_columns
                    if abs(float(entry["label_right_x_pt"]) - float(label["x1"]))
                    <= _CATEGORY_CLUSTER_TOLERANCE_PT
                ),
                None,
            )
            if column is None:
                column = {
                    "label_right_x_pt": round(float(label["x1"]), 3),
                    "label_x0s": [float(label["x0"])],
                    "value_x0s": [],
                    "rows": [],
                    "matched": [],
                    "evidence": [],
                }
                label_columns.append(column)
            else:
                column["label_x0s"].append(float(label["x0"]))
            column["rows"].append(row_index)
            column["matched"].append({"row": row_index, "x0": float(label["x0"]), "x1": float(label["x1"])})
            # The column's value anchor is the first non-bold word to the
            # right of this label within the measured gap bound.
            following = next(
                (
                    word for word in values
                    if 0 < float(word["x0"]) - float(label["x1"])
                    <= _CATEGORY_MAX_LABEL_VALUE_GAP_PT
                ),
                None,
            )
            if following is not None:
                column["value_x0s"].append(float(following["x0"]))
    columns = []
    for column in label_columns:
        if len(column["rows"]) < 2 or len(column["value_x0s"]) < 2:
            continue  # no measured cluster: no column, never a guessed anchor
        label_right = round(float(column["label_right_x_pt"]), 3)
        sorted_values = sorted(column["value_x0s"])
        middle = len(sorted_values) // 2
        value_x0 = round(
            sorted_values[middle]
            if len(sorted_values) % 2
            else (sorted_values[middle - 1] + sorted_values[middle]) / 2,
            3,
        )
        if value_x0 is None or not (0.0 < value_x0 - label_right <= _CATEGORY_MAX_LABEL_VALUE_GAP_PT):
            continue
        columns.append(
            {
                "label_right_x_pt": label_right,
                "value_x0_pt": value_x0,
                "label_value_gap_pt": round(value_x0 - label_right, 3),
                "rows": list(column["rows"]),
                "matched": list(column["matched"]),
                "evidence": [
                    f"local_pdf.category_grid.col{len(columns)}.label_right_x:{label_right:.3f}",
                    f"local_pdf.category_grid.col{len(columns)}.value_x0:{value_x0:.3f}",
                ],
            }
        )
    if len(columns) < 2:
        return None
    columns.sort(key=lambda entry: entry["value_x0_pt"])
    grid_rows = sorted({index for column in columns for index in column["rows"]})
    pitches = [
        round(word_rows[grid_rows[index + 1]][0]["top"] - word_rows[grid_rows[index]][0]["top"], 3)
        for index in range(len(grid_rows) - 1)
    ]
    if pitches and max(pitches) - min(pitches) > _CATEGORY_ROW_PITCH_UNIFORMITY_PT:
        return None  # rows are not a uniform rhythm: not the measured grid
    row_pitch = round(sum(pitches) / len(pitches), 3) if pitches else 0.0
    splits: list[float] = []
    for index in range(len(columns) - 1):
        left_column = columns[index]
        right_column = columns[index + 1]
        # Per shared row, the left column's value run ends before the right
        # column's label begins (both bounds measured on the same row).
        right_label_x0_by_row = {
            match["row"]: match["x0"] for match in right_column["matched"]
        }
        left_extents: list[float] = []
        right_bounds: list[float] = []
        for row_index in left_column["rows"]:
            right_label_x0 = right_label_x0_by_row.get(row_index)
            if right_label_x0 is None:
                continue
            # The left value RUN may wrap onto further words; take every
            # non-bold word right of the anchor up to the next label.
            row_values = [
                float(word["x1"])
                for word in word_rows[row_index]
                if not word.get("bold")
                and float(word["x0"]) >= left_column["value_x0_pt"] - _CATEGORY_CLUSTER_TOLERANCE_PT
                and float(word["x1"]) < right_label_x0_by_row[row_index]
            ]
            if row_values:
                left_extents.append(max(row_values))
                right_bounds.append(right_label_x0_by_row[row_index])
        if not left_extents:
            continue
        splits.append(round((max(left_extents) + min(right_bounds)) / 2, 3))
    return {
        "columns": columns,
        "row_pitch_pt": row_pitch,
        "column_splits_x_pt": splits,
        "row_count": len(grid_rows),
        "evidence": [
            *(id_ for column in columns for id_ in column["evidence"]),
            f"local_pdf.category_grid.row_pitch:{row_pitch:.3f}",
        ],
    }


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
