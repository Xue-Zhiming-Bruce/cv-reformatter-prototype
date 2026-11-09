"""Shared pipeline measurement implementation."""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from app.generation.template_mapper import CONTACT_FIELD_SLOTS, CONTACT_LABEL_FIELDS
from app.pipeline.evidence import MeasurementRequest, MeasurementResult, _page_pt_size, _pdf_line_rows
from app.pipeline.rendering import _pinned_chrome_environment

HEADER_VERTICAL_RULE_MEASUREMENT_VERSION = "header_vertical_rule_geometry/2"
HEADER_CONTACT_LABEL_MEASUREMENT_VERSION = "header_contact_label_geometry/1"

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

def _bind_role_gap_anchors(
    request: MeasurementRequest,
    *,
    target_pdf: Path,
    current_pdf: Path,
    region_span: tuple[float, float] | None,
    render_leaf_texts: list[str],
    render_non_head_texts: list[str] | None = None,
) -> MeasurementRequest | MeasurementResult:
    """Phase 0 measurement binding: resolve a SEMANTIC measurement intent
    into actual final-PDF objects (exact line-prefix anchors on BOTH sides).
    The render side binds against the render's own candidate entry-head
    texts; the target side binds against the target's measured content rows
    within the region span. A Reviewer OCR mismatch never becomes an
    automatic measurement_failure: an unresolvable intent returns
    `evidence_missing` for re-verification, with the binding method recorded."""
    intent = (request.intent or "").casefold()
    if not (
        any(word in intent for word in ("gap", "distance", "spacing"))
        and any(word in intent for word in ("entry", "role", "job"))
        and not any(word in intent for word in (
            "contact", "typography", "font", "vertical rule",
        ))
    ):
        return MeasurementResult(
            request_id=request.request_id,
            status="not_measurable",
            reason="the current deterministic metric measures entry-head spacing only",
            method="role_gap_binding/3",
            warnings=["a different visual dimension requires its own deterministic metric"],
        )
    if region_span is None:
        return MeasurementResult(
            request_id=request.request_id, status="evidence_missing",
            reason="entry-head spacing requires a measured owning-section boundary",
            method="role_gap_binding/3",
        )
    render_rows = _pdf_line_rows(current_pdf, request.page)

    def _norm_row(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()

    prefixes = [
        _norm_row(text)[:12]
        for text in render_leaf_texts
        if text and len(text.strip()) >= 6
    ]
    render_anchors: list[str] = []
    render_head_rows: list[dict[str, Any]] = []
    for row in render_rows:
        row_text = _norm_row(row["text"])
        for prefix in prefixes:
            if row_text.startswith(prefix) and prefix not in render_anchors:
                render_anchors.append(prefix)
                render_head_rows.append(row)
                break
        if len(render_anchors) == 2:
            break
    if len(render_anchors) < 2:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            reason=(
                "the semantic intent could not be bound to two render entry-head "
                "rows in the final PDF (binding, not OCR, decides)"
            ),
            method="role_gap_binding/3",
            warnings=["binding resolves the Reviewer's semantic intent against actual PDF rows"],
        )
    target_rows = _pdf_line_rows(target_pdf, request.page)
    if region_span is not None:
        span_top, span_bottom = region_span
        target_rows = [
            row for row in target_rows if span_top < row["top"] < span_bottom
        ]
    import pdfplumber

    def row_styles(path: Path) -> dict[float, tuple[float, bool, Any]]:
        with pdfplumber.open(path) as document:
            words = document.pages[request.page - 1].extract_words(
                extra_attrs=["fontname", "size", "non_stroking_color"])
        styles = {}
        for word in sorted(words, key=lambda item: item["x0"]):
            styles.setdefault(round(float(word["top"]), 1), (
                float(word["size"]), "bold" in word["fontname"].casefold(),
                word["non_stroking_color"],
            ))
        return styles

    render_styles, target_styles = row_styles(current_pdf), row_styles(target_pdf)
    anchor_styles = [render_styles.get(row["top"]) for row in render_head_rows]

    def same_role(style: tuple | None, reference: tuple | None) -> bool:
        return bool(style and reference and abs(style[0] - reference[0]) < .5
                    and style[1:] == reference[1:])

    other_prefixes = [_norm_row(text)[:12] for text in render_non_head_texts or [] if text.strip()]
    if any(any(_norm_row(row["text"]).startswith(prefix) for prefix in other_prefixes)
           and any(same_role(render_styles.get(row["top"]), style) for style in anchor_styles)
           for row in render_rows):
        return MeasurementResult(
            request_id=request.request_id, status="ambiguous",
            reason="entry-head typography also belongs to other declared entry fields",
            method="role_gap_binding/3",
        )

    # Bind the same measured typographic role, rather than adjacent title,
    # company or date rows. Unsupported style differences stay unmeasured.
    head_rows = [row for row in target_rows
                 if not row["text"].startswith(("•", "-", "–"))
                 and any(character.isalpha() for character in row["text"])
                 and all(same_role(target_styles.get(row["top"]), style)
                         for style in anchor_styles)]
    if len(head_rows) < 2:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            reason="the region's target content rows could not yield two head anchors",
            method="role_gap_binding/3",
            warnings=["binding resolves the Reviewer's semantic intent against actual PDF rows"],
        )
    if len(head_rows) > 2:
        return MeasurementResult(
            request_id=request.request_id, status="ambiguous",
            reason="matching typography does not identify a unique pair of target entry-head rows",
            method="role_gap_binding/3",
            target_evidence=[{"page": request.page, "top": row["top"], "text": row["text"]} for row in head_rows],
        )
    return request.model_copy(
        update={
            "from_text": _norm_row(head_rows[0]["text"])[:16],
            "to_text": _norm_row(head_rows[1]["text"])[:16],
            "render_from_text": render_anchors[0],
            "render_to_text": render_anchors[1],
        }
    )

def _header_vertical_rule_evidence(
    pdf: Path, page_number: int, *, name_text: str | None = None
) -> dict[str, Any] | None:
    """Measure header rules against the exact candidate-name row when known.

    Target PDFs have no candidate lineage, so their bounded fallback is the
    single tallest header line. This stays one metric, not a visual framework.
    """
    import pdfplumber

    page_width, page_height = _page_pt_size(pdf, page_number)
    lines = [
        line for line in _pdf_lines_and_marks(pdf)[0]
        if line["page"] == page_number and line["top"] < page_height * 0.25
    ]
    if not lines:
        return None
    name_lines: list[dict[str, Any]] = []
    exact_name_match = False
    if name_text:
        expected = re.sub(r"\s+", " ", name_text).strip().casefold()
        for start in range(len(lines)):
            selected: list[dict[str, Any]] = []
            for line in lines[start:start + 3]:
                selected.append(line)
                observed = re.sub(
                    r"\s+", " ", " ".join(item["text"] for item in selected)
                ).strip().casefold()
                if observed == expected:
                    name_lines = selected
                    exact_name_match = True
                    break
                if not expected.startswith(observed + " "):
                    break
            if name_lines:
                break
    if not name_lines:
        max_height = max(float(line["bottom"]) - float(line["top"]) for line in lines)
        name_lines = [
            line for line in lines
            if math.isclose(
                float(line["bottom"]) - float(line["top"]),
                max_height,
                abs_tol=0.25,
            )
        ]
    name = {
        "x0": min(float(line["x0"]) for line in name_lines),
        "x1": max(float(line["x1"]) for line in name_lines),
        "top": min(float(line["top"]) for line in name_lines),
        "bottom": max(float(line["bottom"]) for line in name_lines),
    }
    header_top = max(0.0, name["top"] - 80.0)
    header_bottom = min(page_height, name["bottom"] + 80.0)
    rules: list[dict[str, Any]] = []
    with pdfplumber.open(pdf) as document:
        page = document.pages[page_number - 1]
        objects = [*page.rects, *page.curves, *(getattr(page, "lines", None) or [])]
        seen: set[tuple[float, float, float]] = set()
        for mark in objects:
            x0, x1 = float(mark["x0"]), float(mark["x1"])
            top, bottom = float(mark["top"]), float(mark["bottom"])
            width, height = abs(x1 - x0), abs(bottom - top)
            center_x = (x0 + x1) / 2
            if width > 4.0 or height < 8.0 or bottom < header_top or top > header_bottom:
                continue
            horizontal_gap = max(name["x0"] - x1, x0 - name["x1"], 0.0)
            if horizontal_gap > 80.0 or center_x < 12.0 or center_x > page_width - 12.0:
                continue
            key = (round(center_x, 2), round(top, 2), round(bottom, 2))
            if key in seen:
                continue
            seen.add(key)
            dx = horizontal_gap
            dy = max(name["top"] - bottom, top - name["bottom"], 0.0)
            overlap = max(0.0, min(bottom, name["bottom"]) - max(top, name["top"]))
            rules.append({
                "x": round(center_x, 3),
                "top": round(top, 3),
                "bottom": round(bottom, 3),
                "height": round(height, 3),
                "relative_x_to_name_left": round(center_x - name["x0"], 3),
                "relative_top_to_name_top": round(top - name["top"], 3),
                "relative_bottom_to_name_top": round(bottom - name["top"], 3),
                "nearest_distance_to_name_bbox": round(math.hypot(dx, dy), 3),
                "name_vertical_overlap_pt": round(overlap, 3),
            })
    rules.sort(key=lambda rule: (rule["relative_top_to_name_top"], rule["relative_x_to_name_left"]))
    return {
        "page": page_number,
        "name_anchor_source": (
            "candidate_name_text" if exact_name_match else "tallest_header_line"
        ),
        "name_region": {key: round(value, 3) for key, value in name.items()},
        "vertical_rule_count": len(rules),
        "vertical_rules": rules,
    }

def _header_vertical_rule_target_context(pdf: Path) -> dict[str, Any] | None:
    """Small Builder context for the already-supported header-rule metric."""
    evidence = _header_vertical_rule_evidence(pdf, 1)
    if evidence is None:
        return None
    rules = evidence["vertical_rules"]
    return {
        "scope": "header_name",
        "target_final_pdf_evidence": evidence,
        "preserve_separate_name_gap": (
            len(rules) == 2
            and all(rule["name_vertical_overlap_pt"] == 0 for rule in rules)
        ),
        "forbidden_interpretation": "continuous_or_page_spanning_vertical_rail",
    }

def _target_body_text_color_context(pdf: Path) -> dict[str, Any] | None:
    """Measured dominant text fill by visible-character volume."""
    import pdfplumber

    colors: Counter[tuple[float, float, float]] = Counter()
    with pdfplumber.open(pdf) as document:
        for page in document.pages:
            for char in page.chars:
                color = char.get("non_stroking_color")
                if not str(char.get("text") or "").strip() or not isinstance(color, (list, tuple)) or len(color) != 3:
                    continue
                rgb = tuple(round(float(channel), 4) for channel in color)
                if all(0.0 <= channel <= 1.0 for channel in rgb):
                    colors[rgb] += 1
    if not colors:
        return None
    rgb, count = colors.most_common(1)[0]
    return {
        "scope": "ordinary_body_copy",
        "source": "target_final_pdf_character_fill",
        "selection": "dominant_non_whitespace_character_color",
        "dominant_color_rgb": list(rgb),
        "dominant_color_hex": "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb),
        "supporting_character_count": count,
        "excluded_roles": ["header_name", "section_labels", "rules"],
    }


def _measure_header_contact_label_geometry(
    request: MeasurementRequest, *, target_pdf: Path, current_pdf: Path,
    state: Any, plan: Any, current_name_text: str | None,
) -> MeasurementResult:
    """Physical glyph-width cost of a mismatched uniform header label policy."""
    def unavailable(status: str, reason: str, target: dict[str, Any] | None = None,
                    current: dict[str, Any] | None = None) -> MeasurementResult:
        return MeasurementResult(request_id=request.request_id, status=status, reason=reason,
            method=HEADER_CONTACT_LABEL_MEASUREMENT_VERSION,
            target_evidence=[target] if target else [], current_evidence=[current] if current else [])

    nodes = [node for node in state.nodes if node.kind == "header_row"
             and any(field.slot in CONTACT_FIELD_SLOTS.values() for field in node.fields)]
    if request.page != 1 or len(nodes) != 1 or plan is None:
        return unavailable("not_measurable", "label geometry requires one declared page-one contact row")
    node = nodes[0]
    if request.region_id != node.node_id:
        return unavailable("not_measurable", "label geometry must bind to the declared contact row")
    row = next((row for row in plan.header_rows if row.node_id == node.node_id), None)
    fields = list(row.fields) if row else []
    if not fields or any(field.contact_item is None for field in fields):
        return unavailable("not_measurable", "label geometry requires structured disclosed contact fields")
    font = next(style.font_size_pt for style in state.styles if style.style_id == node.style_id)
    target_name = _header_vertical_rule_evidence(target_pdf, 1)
    current_name = _header_vertical_rule_evidence(current_pdf, 1, name_text=current_name_text)
    if target_name is None or current_name is None or current_name["name_anchor_source"] != "candidate_name_text":
        return unavailable("evidence_missing", "the header name did not bind to the actual PDF")
    labels = {CONTACT_FIELD_SLOTS[field]: label for label, field in CONTACT_LABEL_FIELDS.items()
              if CONTACT_FIELD_SLOTS[field] in {item.slot for item in node.fields}}
    target = header_contact_label_evidence(target_pdf, name_region=target_name["name_region"],
        font_size_pt=font, labels=labels, separator=node.separator)
    current = header_contact_label_evidence(current_pdf, name_region=current_name["name_region"],
        font_size_pt=font, labels={field.slot: field.contact_item.label for field in fields},
        values={field.slot: field.contact_item.value for field in fields}, separator=row.separator)
    for evidence in (target, current):
        if evidence["status"] != "confirmed":
            return unavailable(evidence["status"], evidence["reason"], target, current)
    error = 0.0
    if target["label_mode"] != current["label_mode"]:
        visible_side = target if target["label_mode"] else current
        error = sum(visible_side["label_widths_pt"][field.slot] for field in fields)
    return MeasurementResult(request_id=request.request_id, status="confirmed", target_value_pt=0.0,
        current_value_pt=round(error, 3), delta_pt=round(error, 3),
        method=HEADER_CONTACT_LABEL_MEASUREMENT_VERSION, target_evidence=[target], current_evidence=[current],
        warnings=["error_pt sums actual label-prefix glyph widths only where uniform presence differs; field values, delimiters, fonts and other contact styling are separate properties"])


def _measure_header_vertical_rule_geometry(
    request: MeasurementRequest,
    *,
    target_pdf: Path,
    current_pdf: Path,
    current_name_text: str | None = None,
) -> MeasurementResult:
    """One transparent scalar error over the complete detected rule sets.

    Count and name-overlap penalties prevent an extra retained rule from being
    ignored; raw geometry remains in evidence for owner inspection.
    """
    intent = (request.intent or "").casefold()
    if not (
        any(word in intent for word in ("header", "name"))
        and "vertical" in intent
        and any(word in intent for word in ("rule", "line", "bar"))
    ):
        return MeasurementResult(
            request_id=request.request_id,
            status="not_measurable",
            reason="header_vertical_rule_geometry requires an explicit header/name vertical-rule intent",
            method=HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
        )
    target = _header_vertical_rule_evidence(target_pdf, request.page)
    current = _header_vertical_rule_evidence(
        current_pdf, request.page, name_text=current_name_text
    )
    if target is None or current is None:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            reason="the name/header region could not be located in one of the final PDFs",
            method=HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
        )
    target_rules = target["vertical_rules"]
    current_rules = current["vertical_rules"]
    if bool(target_rules) != bool(current_rules):
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            target_evidence=[target],
            current_evidence=[current],
            reason="one header vertical-rule set is empty; deletion is not automatic improvement",
            method=HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
        )
    paired_error = sum(
        abs(current_rule["relative_x_to_name_left"] - target_rule["relative_x_to_name_left"])
        + abs(current_rule["relative_top_to_name_top"] - target_rule["relative_top_to_name_top"])
        + abs(current_rule["relative_bottom_to_name_top"] - target_rule["relative_bottom_to_name_top"])
        for target_rule, current_rule in zip(target_rules, current_rules)
    )
    target_overlap = sum(rule["name_vertical_overlap_pt"] > 0 for rule in target_rules)
    current_overlap = sum(rule["name_vertical_overlap_pt"] > 0 for rule in current_rules)
    error = (
        100.0 * abs(len(current_rules) - len(target_rules))
        + 100.0 * abs(current_overlap - target_overlap)
        + paired_error
    )
    formula = (
        "error_pt = 100*abs(rule_count-target_rule_count) + "
        "100*abs(name_overlap_count-target_name_overlap_count) + "
        "sum paired abs(relative_x, relative_top, relative_bottom) deltas; "
        "all detected header vertical rules are included"
    )
    target["error_calculation"] = formula
    current["error_calculation"] = formula
    return MeasurementResult(
        request_id=request.request_id,
        status="confirmed",
        target_value_pt=0.0,
        current_value_pt=round(error, 3),
        delta_pt=round(error, 3),
        target_evidence=[target],
        current_evidence=[current],
        method=HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
        warnings=["coordinates are page-local pt; geometry is relative to the detected name region"],
    )

def _measurement_improvement(
    before: MeasurementResult, after: MeasurementResult
) -> tuple[bool, str]:
    if before.method != after.method:
        return False, "measurement methods differ; improvement is not comparable"
    if after.status != "confirmed":
        return False, f"identical re-measurement was {after.status}"
    if any(value is None for value in (
        before.current_value_pt, before.target_value_pt,
        after.current_value_pt, after.target_value_pt,
    )):
        return False, "identical re-measurement left scalar evidence missing"
    if before.method == after.method and before.method in {
        "header_vertical_rule_geometry/1", HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
    }:
        before_rules = (before.current_evidence[0].get("vertical_rules", []) if before.current_evidence else [])
        after_rules = (after.current_evidence[0].get("vertical_rules", []) if after.current_evidence else [])
        if len(after_rules) > len(before_rules):
            retained_crossing_rule = any(
                old.get("name_vertical_overlap_pt", 0) > 0
                and any(
                    new.get("name_vertical_overlap_pt", 0) > 0
                    and abs(new.get("height", 0) - old.get("height", 0)) <= 2.0
                    for new in after_rules
                )
                for old in before_rules
            )
            if retained_crossing_rule:
                return False, "added a vertical rule while retaining the original long name-crossing rule"
    improved = abs(after.current_value_pt - after.target_value_pt) < abs(
        before.current_value_pt - before.target_value_pt
    )
    return improved, (
        "identical deterministic error decreased" if improved
        else "identical deterministic error did not decrease"
    )

def _header_typography_context(pdf: Path, *, name_text: str | None = None) -> dict[str, Any]:
    """Content-free font facts over the existing final-PDF header anchor."""
    import pdfplumber

    evidence = _header_vertical_rule_evidence(pdf, 1, name_text=name_text)
    if evidence is None or (name_text and evidence["name_anchor_source"] != "candidate_name_text"):
        return {"state": "unbound", "reason": "the requested header name did not bind to final-PDF text"}
    region = evidence["name_region"]
    with pdfplumber.open(pdf) as document:
        chars = [char for char in document.pages[0].chars if str(char.get("text") or "").strip()]
        selected = [char for char in chars
            if region["x0"] - .5 <= (char["x0"] + char["x1"]) / 2 <= region["x1"] + .5
            and region["top"] - .5 <= (char["top"] + char["bottom"]) / 2 <= region["bottom"] + .5]
        following = [char for char in chars if char["top"] > region["bottom"] + .5]
        next_top = min((char["top"] for char in following), default=None)
        return {
            "state": "measured", "source": "final_pdf_characters", "page": 1,
            "selection": evidence["name_anchor_source"], "region": region,
            "font_sizes_pt": sorted({round(float(char["size"]), 3) for char in selected}),
            "font_names": sorted({re.sub(r"^[A-Z]{6}\+", "", str(char["fontname"])) for char in selected}),
            "following_line_font_sizes_pt": sorted({round(float(char["size"]), 3) for char in following
                if next_top is not None and abs(char["top"] - next_top) < 1.5}),
        }

