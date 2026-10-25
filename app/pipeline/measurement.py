"""Shared pipeline measurement implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from app.pipeline.rendering import _pinned_chrome_environment

SlotKind = Literal["name", "title", "tagline", "location", "phone", "envelope", "github", "linkedin"]

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

class BodyScaffold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headings: list[BodyHeadingScaffold] = Field(min_length=1)
    entry: BodyEntryScaffold | None = None
    contact_icons_present: bool = True
    contact_separator: str | None = None
    category_grids: list[CategoryGridScaffold] = Field(default_factory=list)

BULLET_GLYPHS = frozenset({"•", "◦", "·", "▪", "‣", "●", "∙"})

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


def header_contact_label_evidence(
    pdf: Path, *, name_region: dict[str, float], font_size_pt: float,
    labels: dict[str, str], separator: str | None, values: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Measure canonical label prefixes in one declared header contact row."""
    import re
    import pdfplumber

    _, marks = _pdf_lines_and_marks(pdf)
    top = name_region["bottom"]
    bottom = min([top + 72, *(mark["top"] for mark in marks
        if mark["page"] == 1 and mark.get("kind") == "rule" and mark["top"] > top + .5)])
    with pdfplumber.open(pdf) as document:
        page = document.pages[0]
        chars = [char for char in page.chars if str(char["text"]).strip()
            and char["top"] >= top - .25 and char["bottom"] <= bottom + .25
            and abs(float(char["size"]) - font_size_pt) <= .25]
        if not chars:
            return {"status": "evidence_missing", "reason": "no text binds to the declared contact font/window"}

        def in_scope(hit: dict[str, Any]) -> bool:
            return bool(hit.get("chars")) and all(char in chars for char in hit["chars"])

        value_boxes = []
        for value in (values or {}).values():
            pattern = r"\s+".join(re.escape(word) for word in value.split())
            found = [hit for hit in page.search(pattern, regex=True) or [] if in_scope(hit)]
            if len(found) != 1:
                return {"status": "evidence_missing", "reason": "a disclosed contact value does not bind uniquely to the actual header"}
            value_boxes.append(found[0])

        def label_prefix(hit: dict[str, Any]) -> bool:
            if not in_scope(hit):
                return False
            if any(box["x0"] - .25 <= hit["x0"] and hit["x1"] <= box["x1"] + .25
                   and abs(box["top"] - hit["top"]) < 1.5 for box in value_boxes):
                return False  # A colon within a source value is not a generated label.
            preceding = [char for char in chars if abs(char["top"] - hit["top"]) < 1.5
                         and char["x1"] <= hit["x0"] + .1]
            previous = max(preceding, key=lambda char: char["x1"], default=None)
            return previous is None or previous["text"] in (separator or "")

        widths = {}
        for slot, label in labels.items():
            hits = [hit for hit in page.search(r"(?<!\w)" + re.escape(label) + r"\s*:",
                regex=True, case=False) or [] if label_prefix(hit)]
            if len(hits) > 1:
                return {"status": "ambiguous", "reason": "multiple label prefixes bind to the same header field"}
            widths[slot] = round(hits[0]["x1"] - hits[0]["x0"], 3) if hits else 0.0
        known = {label.casefold() for label in labels.values()}
        for hit in page.search(r"(?<![A-Za-z])([A-Za-z][A-Za-z -]{0,24}):", regex=True) or []:
            token = hit["text"][:-1].strip().casefold()
            if label_prefix(hit) and token not in known | {"http", "https", "mailto", "tel"}:
                return {"status": "not_measurable", "reason": "an unrecognized header label prefix requires review"}
        visible = {slot for slot, width in widths.items() if width > 0}
        if visible and visible != set(labels):
            return {"status": "ambiguous", "reason": "mixed per-field contact labels cannot bind to one uniform label choice"}
        return {"status": "confirmed", "page": 1, "window_top_pt": round(top, 3),
            "window_bottom_pt": round(bottom, 3), "font_size_pt": font_size_pt,
            "label_mode": bool(visible), "label_widths_pt": widths,
            "selection": "declared contact font after name, before first horizontal rule or 72pt bound"}


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
