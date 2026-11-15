"""Bounded deterministic local-PDF color, rule, badge, and spacing measurement.

ADR 0002 (amended 2026-08-27): Adobe PDF Extract does not output text fill
colors at the product level (verified against the official styling JSON
schema and live calls — only decoration/border/background colors exist).
Adobe remains the sole analyzer for pages, text structure, reading order, and
typography. This module measures only dimensions Adobe does not expose at the
product boundary: text fill color, long horizontal line/rectangle objects,
repeated filled short-text badge shapes, and inter-section line-box gaps. All
come from the same PDF bytes and record provenance source="local_pdf".
Blocks that stay colorless still fail the compile bridge closed.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import median

import pdfplumber

from app.resource_limits import bounded_pdf_pages
from app.template_analysis.commercial.models import (
    MeasurementProvenance,
    NormalizedBadgeCluster,
    NormalizedBox,
    NormalizedLayoutEvidence,
    NormalizedRule,
    NormalizedTextBlock,
)


def _hex_from_color(value: object) -> str | None:
    """Convert a pdfplumber non_stroking_color (gray/RGB/CMYK) to #RRGGBB."""
    if isinstance(value, (int, float)):
        value = (value,)
    if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 4:
        return None
    try:
        channels = [float(channel) for channel in value]
    except (TypeError, ValueError):
        return None
    if len(channels) == 1:
        red = green = blue = channels[0]
    elif len(channels) == 3:
        red, green, blue = channels
    else:
        cyan, magenta, yellow, key = channels
        red = (1 - cyan) * (1 - key)
        green = (1 - magenta) * (1 - key)
        blue = (1 - yellow) * (1 - key)

    def byte(channel: float) -> int:
        return round(max(0.0, min(1.0, channel)) * 255)

    return f"#{byte(red):02X}{byte(green):02X}{byte(blue):02X}"


def _containing_block(
    blocks: list[NormalizedTextBlock], center_x: float, center_y: float
) -> NormalizedTextBlock | None:
    for block in blocks:
        box = block.bbox
        assert box is not None  # guarded by caller
        if box.x0 <= center_x <= box.x1 and box.top <= center_y <= box.bottom:
            return block
    return None


def enrich_colors_from_local_pdf(
    normalized: NormalizedLayoutEvidence, pdf_path: str | Path
) -> NormalizedLayoutEvidence:
    """Fill color_hex on Adobe blocks from locally measured char fill colors.

    Provider-measured colors are never overwritten. Chars are matched to a
    block by center-point containment in the block bbox; a block adopts the
    majority char color.
    # ponytail: majority color per block — mixed-color blocks (e.g. colored
    # link inside body text) collapse to the dominant color; per-span split
    # if fidelity on such targets ever demands it.
    """
    blocks_by_page: dict[int, list[NormalizedTextBlock]] = {}
    for block in normalized.text_blocks:
        if block.color_hex is None and block.bbox is not None:
            blocks_by_page.setdefault(block.page_number, []).append(block)
    if not blocks_by_page:
        return normalized

    votes: dict[tuple[int, str], Counter[str]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        # One bounded walk for every requested page, in page order.
        for page_number, page in enumerate(bounded_pdf_pages(pdf), 1):
            page_blocks = blocks_by_page.get(page_number)
            if not page_blocks:
                continue
            width, height = float(page.width), float(page.height)
            for char in page.chars:
                hex_color = _hex_from_color(char.get("non_stroking_color"))
                if hex_color is None:
                    continue
                center_x = (char["x0"] + char["x1"]) / 2 / width
                center_y = (char["top"] + char["bottom"]) / 2 / height
                block = _containing_block(page_blocks, center_x, center_y)
                if block is not None:
                    votes.setdefault((page_number, block.element_id), Counter())[
                        hex_color
                    ] += 1

    if not votes:
        return normalized

    def with_color(block: NormalizedTextBlock) -> NormalizedTextBlock:
        counter = votes.get((block.page_number, block.element_id))
        if counter is None or block.color_hex is not None:
            return block
        hex_color, _count = counter.most_common(1)[0]
        provenance = dict(block.provenance)
        provenance["color_hex"] = MeasurementProvenance(
            source="local_pdf",
            provider="pdfplumber",
            source_element_id=block.element_id,
            method="char_non_stroking_color",
        )
        return block.model_copy(
            update={"color_hex": hex_color, "provenance": provenance}
        )

    return normalized.model_copy(
        update={"text_blocks": [with_color(block) for block in normalized.text_blocks]}
    )


def enrich_rules_from_local_pdf(
    normalized: NormalizedLayoutEvidence,
    pdf_path: str | Path,
) -> NormalizedLayoutEvidence:
    """Record long horizontal separators; short underlines stay excluded."""

    rules: list[NormalizedRule] = []
    lines_by_page: dict[int, list[dict[str, object]]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(bounded_pdf_pages(pdf), start=1):
            width, height = float(page.width), float(page.height)
            words = page.extract_words()
            lines_by_page[page_number] = page.extract_text_lines(strip=True)
            objects = [*page.lines, *page.rects]
            for object_index, item in enumerate(objects):
                x0 = float(item.get("x0") or 0)
                x1 = float(item.get("x1") or 0)
                top = float(item.get("top") or 0)
                bottom = float(item.get("bottom") or top)
                object_height = abs(bottom - top)
                if abs(x1 - x0) < width * 0.5 or object_height > 1.5:
                    continue
                stroke_width = float(item.get("linewidth") or object_height or 0)
                if not 0.25 <= stroke_width <= 6.0:
                    continue
                color = _hex_from_color(
                    item.get("stroking_color")
                    if item.get("stroking_color") is not None
                    else item.get("non_stroking_color")
                )
                if color is None:
                    continue
                left, right = min(x0, x1), max(x0, x1)
                overlapping_words = [
                    word
                    for word in words
                    if float(word["x1"]) >= left and float(word["x0"]) <= right
                ]
                words_above = [
                    word
                    for word in overlapping_words
                    if float(word["bottom"]) <= top + 0.1
                ]
                words_below = [
                    word
                    for word in overlapping_words
                    if float(word["top"]) >= bottom - 0.1
                ]
                element_id = f"local.page.{page_number}.rule.{object_index}"
                rules.append(
                    NormalizedRule(
                        element_id=element_id,
                        page_number=page_number,
                        bbox=NormalizedBox(
                            x0=max(0, min(1, min(x0, x1) / width)),
                            top=max(0, min(1, min(top, bottom) / height)),
                            x1=max(0, min(1, max(x0, x1) / width)),
                            bottom=max(0, min(1, max(top, bottom) / height)),
                        ),
                        stroke_width_pt=stroke_width,
                        gap_above_pt=(
                            max(0.0, top - max(float(word["bottom"]) for word in words_above))
                            if words_above
                            else None
                        ),
                        gap_below_pt=(
                            max(0.0, min(float(word["top"]) for word in words_below) - bottom)
                            if words_below
                            else None
                        ),
                        color_hex=color,
                        provenance=MeasurementProvenance(
                            source="local_pdf",
                            provider="pdfplumber",
                            source_element_id=element_id,
                            method="long_horizontal_line_or_thin_rectangle",
                        ),
                    )
                )
    enriched = normalized.model_copy(
        update={
            "rules": rules,
            "graphic_count": max(normalized.graphic_count, len(rules)),
        }
    )
    return _enrich_section_spacing(enriched, lines_by_page)


def _enrich_section_spacing(
    normalized: NormalizedLayoutEvidence,
    lines_by_page: dict[int, list[dict[str, object]]],
) -> NormalizedLayoutEvidence:
    """Attach measured line-box gaps to headings followed by local rules."""

    page_by_number = {page.page_number: page for page in normalized.pages}
    weighted_styles: Counter[tuple[str | None, float, str | None]] = Counter()
    for block in normalized.text_blocks:
        weighted_styles[(block.font_family, round(block.font_size_pt or 0, 2), block.color_hex)] += max(
            1, len(block.text.strip())
        )
    if not weighted_styles:
        return normalized
    body_signature = weighted_styles.most_common(1)[0][0]
    body_line_height = next(
        (
            block.line_height_pt
            for block in normalized.text_blocks
            if (block.font_family, round(block.font_size_pt or 0, 2), block.color_hex)
            == body_signature
            and block.line_height_pt is not None
        ),
        None,
    )
    if body_line_height is None:
        return normalized.model_copy(
            update={
                "warnings": [
                    *normalized.warnings,
                    "Inter-section gaps were not measured because body line height is unavailable.",
                ]
            }
        )

    warnings = list(normalized.warnings)

    def measured_spacing(block: NormalizedTextBlock) -> float | None:
        if block.bbox is None or block.structural_role != "heading_candidate":
            return block.spacing_before_pt
        page = page_by_number.get(block.page_number)
        if page is None:
            return block.spacing_before_pt
        block_top = block.bbox.top * page.height_pt
        block_bottom = block.bbox.bottom * page.height_pt
        if not any(
            rule.page_number == block.page_number
            and -2.0 <= rule.bbox.top * page.height_pt - block_bottom <= 12.0
            for rule in normalized.rules
        ):
            return block.spacing_before_pt
        lines = lines_by_page.get(block.page_number, [])
        candidates = [
            line
            for line in lines
            if abs(float(line["top"]) - block_top) <= 4.0
        ]
        if not candidates:
            warnings.append(
                f"Inter-section gap unmeasurable for heading element '{block.element_id}'."
            )
            return None
        heading_line = min(candidates, key=lambda line: abs(float(line["top"]) - block_top))
        preceding = [
            line
            for line in lines
            if float(line["bottom"]) <= float(heading_line["top"]) + 0.1
        ]
        if not preceding:
            warnings.append(
                f"Inter-section gap unmeasurable for heading element '{block.element_id}'."
            )
            return None
        previous_line = max(preceding, key=lambda line: float(line["top"]))
        return max(
            0.0,
            float(heading_line["top"])
            - float(previous_line["top"])
            - body_line_height,
        )

    blocks: list[NormalizedTextBlock] = []
    for block in normalized.text_blocks:
        spacing = measured_spacing(block)
        if spacing == block.spacing_before_pt:
            blocks.append(block)
            continue
        provenance = dict(block.provenance)
        if spacing is not None:
            provenance["spacing_before_pt"] = MeasurementProvenance(
                source="local_pdf",
                provider="pdfplumber",
                source_element_id=block.element_id,
                method="previous_text_line_top_to_heading_top_minus_body_line_height",
            )
        blocks.append(
            block.model_copy(
                update={"spacing_before_pt": spacing, "provenance": provenance}
            )
        )
    return normalized.model_copy(update={"text_blocks": blocks, "warnings": warnings})


def _shape_bounds(item: dict[str, object]) -> tuple[float, float, float, float]:
    x0, x1 = sorted((float(item.get("x0") or 0), float(item.get("x1") or 0)))
    top = float(item.get("top") or 0)
    bottom = float(item.get("bottom") or top)
    return x0, x1, top, bottom


# Chip classification bounds, each read off what a filled label box is. A chip
# body lines up with one text line (10-30 pt high); a filled chip body covers its
# box (a rounded box fills 79% or more of it, a triangle about half); a chip's
# label sits inside its own edges; and a chip is a label with padding, so a box
# many times wider than it is tall is a container unless its label spans it. A
# label inset sets the badge padding, which the cluster schema bounds at 72 pt.
_MIN_BADGE_HEIGHT_PT = 10.0
_MAX_BADGE_HEIGHT_PT = 30.0
_MAX_CHIP_ASPECT_RATIO = 6.0
_MIN_CHIP_LABEL_WIDTH_FRACTION = 0.5
_MIN_CHIP_FILL_FRACTION = 2 / 3
_MAX_CHIP_PADDING_PT = 72.0  # NormalizedBadgeCluster.horizontal_padding_pt bound


def _in_chip_height_band(item: dict[str, object]) -> bool:
    """True when this shape is as high as a chip body, not a rule or a panel."""
    _x0, _x1, top, bottom = _shape_bounds(item)
    return _MIN_BADGE_HEIGHT_PT <= abs(bottom - top) <= _MAX_BADGE_HEIGHT_PT


def _text_run(
    words: list[dict[str, object]],
    box: tuple[float, float, float, float],
) -> dict[str, object] | None:
    """The box's single short text line, with its measured edge insets and width."""
    x0, x1, top, bottom = box
    inside = [
        word
        for word in words
        if x0 <= (float(word["x0"]) + float(word["x1"])) / 2 <= x1
        and top <= (float(word["top"]) + float(word["bottom"])) / 2 <= bottom
    ]
    if not inside or len({round(float(word["top"]), 1) for word in inside}) != 1:
        return None
    text = " ".join(str(word["text"]) for word in inside).strip()
    colors = [
        color
        for word in inside
        if (color := _hex_from_color(word.get("non_stroking_color"))) is not None
    ]
    if not text or len(text) > 80 or not colors:
        return None
    first = min(float(word["x0"]) for word in inside)
    last = max(float(word["x1"]) for word in inside)
    return {
        "color": Counter(colors).most_common(1)[0][0],
        # Raw edges: a run crossing the shape's edge is a negative inset.
        "left": first - x0,
        "right": x1 - last,
        # The run's ink extent: the gaps between its words are label too.
        "width": last - first,
    }


def _filled_area_ratio(item: dict[str, object]) -> float | None:
    """Filled area over bounding-box area; a triangle fills about half of its box."""
    x0, x1, top, bottom = _shape_bounds(item)
    box = (x1 - x0) * abs(bottom - top)
    path = item.get("path") or []
    points: list[tuple[float, float]] = []
    current = (0.0, 0.0)
    for segment in path:
        values = [float(value) for point in segment[1:] for value in point]
        if segment[0] in {"m", "l"}:
            current = (values[0], values[1])
            points.append(current)
        elif segment[0] == "c":
            ends = [
                (current[0], values[0], values[2], values[4]),
                (current[1], values[1], values[3], values[5]),
            ]
            for step in range(1, 9):
                t = step / 8
                weights = ((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3)
                points.append(
                    (
                        sum(weight * value for weight, value in zip(weights, ends[0])),
                        sum(weight * value for weight, value in zip(weights, ends[1])),
                    )
                )
            current = (values[4], values[5])
        elif segment[0] != "h":
            return None
    if not path:
        points = [(float(x), float(y)) for x, y in item.get("pts") or []]
    if len(points) < 3 or box <= 0:
        return None
    area = abs(
        sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))
    ) / 2
    return max(0.0, area / box)


def _not_a_chip_reason(
    item: dict[str, object],
    box: tuple[float, float, float, float],
    run: dict[str, object] | None,
) -> str | None:
    """Geometry-derived reason this filled in-band shape is not a chip, else None."""
    x0, x1, top, bottom = box
    width, height = x1 - x0, abs(bottom - top)
    place = f"x={x0:.1f} pt, top={top:.1f} pt"

    def say(clause: str) -> str:
        return f"{width:g}x{height:g} pt shape {clause} (at {place})"

    if run is None:
        return say("holds no single short text run")
    # A chip's label sits inside its own edges; a label hanging over them is not
    # a chip, and its negative inset must never be reported as zero padding.
    overflow = max(0.0, -float(run["left"]), -float(run["right"]))
    if overflow:
        return say(f"has its text run {overflow:g} pt outside its own edge")
    ratio = _filled_area_ratio(item)
    if ratio is None:
        return say("has a fill path this reader cannot measure")
    if ratio < _MIN_CHIP_FILL_FRACTION:
        return say(f"fills only {ratio:.0%} of its bounding box")
    label_fraction = float(run["width"]) / width
    if (
        width > _MAX_CHIP_ASPECT_RATIO * height
        and label_fraction < _MIN_CHIP_LABEL_WIDTH_FRACTION
    ):
        return say(
            f"is more than {_MAX_CHIP_ASPECT_RATIO:g}x wider than it is tall while "
            f"its label spans only {label_fraction:.0%} of that width"
        )
    inset = min(float(run["left"]), float(run["right"]))
    if inset > _MAX_CHIP_PADDING_PT:
        return say(
            f"holds a label inset {inset:.1f} pt, past the "
            f"{_MAX_CHIP_PADDING_PT:g} pt badge padding ceiling"
        )
    return None


def _overlaps(shape: dict[str, object], other: dict[str, object]) -> bool:
    """True when the two shapes' boxes intersect."""
    x0, x1, top, bottom = _shape_bounds(shape)
    other_x0, other_x1, other_top, other_bottom = _shape_bounds(other)
    return x0 < other_x1 and x1 > other_x0 and top < other_bottom and bottom > other_top


def _pill_span(
    rects: list[dict[str, object]], item: dict[str, object], fill: str
) -> tuple[float, float] | None:
    """Full span of a pill drawn as a body plus a same-fill band through its centre."""
    x0, x1, top, bottom = _shape_bounds(item)
    middle = (top + bottom) / 2
    flat = next(
        (
            other
            for other in rects
            if other is not item
            and abs(float(other.get("top") or 0) - middle) <= 0.2
            and _hex_from_color(other.get("non_stroking_color")) == fill
            and float(other.get("x0") or 0) <= x0
            and float(other.get("x1") or 0) >= x1
        ),
        None,
    )
    return (float(flat["x0"]), float(flat["x1"])) if flat is not None else None


def _chip_candidate(
    object_index: int,
    box: tuple[float, float, float, float],
    fill: str,
    run: dict[str, object],
    *,
    rounded: bool,
) -> dict[str, object]:
    """One measured chip body, as the cluster pass consumes it."""
    x0, x1, top, bottom = box
    return {
        "object_index": object_index,
        "x0": x0,
        "x1": x1,
        "top": top,
        "bottom": bottom,
        "fill": fill,
        "text_color": str(run["color"]),
        "height": abs(bottom - top),
        "padding": min(float(run["left"]), float(run["right"])),
        "rounded": rounded,
    }


def _rect_badge_candidates(
    page: pdfplumber.page.Page, words: list[dict[str, object]]
) -> tuple[list[dict[str, object]], list[str]]:
    """Rectangular badge bodies: a filled rect holding one short text run."""
    candidates: list[dict[str, object]] = []
    unmeasured: list[str] = []
    seen: set[tuple[float, ...]] = set()
    for object_index, item in enumerate(page.rects):
        fill = _hex_from_color(item.get("non_stroking_color"))
        if not item.get("fill") or fill is None or not _in_chip_height_band(item):
            continue
        x0, x1, top, bottom = _shape_bounds(item)
        shape_key = (round(x0, 1), round(x1, 1), round(top, 1), round(bottom, 1), fill)
        if shape_key in seen:
            continue  # the same fill path emitted twice is one shape
        seen.add(shape_key)
        # A pill drawn as a body plus end caps spans the band through its centre,
        # and its label is measured against that full span, not the body alone.
        span = _pill_span(page.rects, item, fill)
        box = (span[0], span[1], top, bottom) if span is not None else (x0, x1, top, bottom)
        run = _text_run(words, box)
        reason = _not_a_chip_reason(item, box, run)
        if reason is not None:
            unmeasured.append(reason)
            continue
        assert run is not None  # a chip candidate carries its measured run
        candidates.append(_chip_candidate(object_index, box, fill, run, rounded=False))
    return candidates, unmeasured


def _rounded_badge_candidates(
    page: pdfplumber.page.Page,
    words: list[dict[str, object]],
    measured: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[str]]:
    """Rounded badge bodies as the HTML renderer draws them: filled bezier paths."""
    candidates: list[dict[str, object]] = []
    unmeasured: list[str] = []
    seen: set[tuple[float, ...]] = set()
    for object_index, item in enumerate(page.curves):
        fill = _hex_from_color(item.get("non_stroking_color"))
        if not item.get("fill") or fill is None or not _in_chip_height_band(item):
            continue
        x0, x1, top, bottom = _shape_bounds(item)
        shape_key = (round(x0, 1), round(x1, 1), round(top, 1), round(bottom, 1), fill)
        if shape_key in seen:
            continue  # the same fill path emitted twice is one shape
        seen.add(shape_key)
        if any(_overlaps(item, other) for other in measured):
            continue  # an end cap of a pill already measured as a rectangle
        box = (x0, x1, top, bottom)
        run = _text_run(words, box)
        reason = _not_a_chip_reason(item, box, run)
        if reason is not None:
            unmeasured.append(reason)
            continue
        assert run is not None  # a chip candidate carries its measured run
        candidates.append(_chip_candidate(object_index, box, fill, run, rounded=True))
    return candidates, unmeasured


def _page_badge_candidates(
    page: pdfplumber.page.Page, words: list[dict[str, object]]
) -> tuple[list[dict[str, object]], list[str]]:
    """Chip bodies on one page, plus why every other in-band filled shape is not one."""
    candidates, unmeasured = _rect_badge_candidates(page, words)
    rounded, rounded_unmeasured = _rounded_badge_candidates(page, words, candidates)
    return [*candidates, *rounded], [*unmeasured, *rounded_unmeasured]


def _unfilled_shape_labels(page: pdfplumber.page.Page) -> list[str]:
    """Named in-band shapes with no fill colour: outlines, not chip bodies."""
    labels: list[str] = []
    for item in [*page.rects, *page.curves]:
        if not _in_chip_height_band(item):
            continue
        filled = bool(item.get("fill")) and _hex_from_color(
            item.get("non_stroking_color")
        ) is not None
        if filled:
            continue
        x0, x1, top, bottom = _shape_bounds(item)
        labels.append(
            f"{x1 - x0:g}x{abs(bottom - top):g} pt outline "
            f"(at x={x0:.1f} pt, top={top:.1f} pt)"
        )
    return labels


def _page_shape_warnings(
    page: pdfplumber.page.Page, unmeasured: list[str]
) -> list[str]:
    """Warnings naming every in-band shape on this page that no chip rule measured."""
    warnings: list[str] = []
    if unmeasured:
        warnings.append(
            f"{len(unmeasured)} filled shape(s) between {_MIN_BADGE_HEIGHT_PT:g} and "
            f"{_MAX_BADGE_HEIGHT_PT:g} pt high were detected but not classified as "
            f"chips ({'; '.join(unmeasured)}); they remain unmeasured pending inspection."
        )
    outlines = _unfilled_shape_labels(page)
    if outlines:
        warnings.append(
            f"{len(outlines)} shape(s) between {_MIN_BADGE_HEIGHT_PT:g} and "
            f"{_MAX_BADGE_HEIGHT_PT:g} pt high carry no fill colour "
            f"({'; '.join(outlines)}); a chip body is filled, so they remain "
            "unmeasured pending inspection."
        )
    return warnings


def enrich_badges_from_local_pdf(
    normalized: NormalizedLayoutEvidence,
    pdf_path: str | Path,
) -> NormalizedLayoutEvidence:
    """Measure repeated filled short-text shapes without interpreting their text."""

    clusters: list[NormalizedBadgeCluster] = []
    warnings = list(normalized.warnings)
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(bounded_pdf_pages(pdf), start=1):
            width, height = float(page.width), float(page.height)
            words = page.extract_words(extra_attrs=["non_stroking_color"])
            candidates, unmeasured = _page_badge_candidates(page, words)
            warnings.extend(_page_shape_warnings(page, unmeasured))

            grouped: dict[tuple[str, str, float], list[dict[str, object]]] = {}
            for candidate in candidates:
                key = (
                    str(candidate["fill"]),
                    str(candidate["text_color"]),
                    round(float(candidate["height"]), 1),
                )
                grouped.setdefault(key, []).append(candidate)
            for cluster_index, ((fill, text_color, _), items) in enumerate(grouped.items()):
                if len(items) < 2:
                    heights = ", ".join(
                        f"{value:g}"
                        for value in sorted(
                            {round(float(item["height"]), 1) for item in items}
                        )
                    )
                    shapes = "; ".join(
                        f"{float(item['x1']) - float(item['x0']):g}x"
                        f"{float(item['height']):g} pt shape "
                        f"(at x={float(item['x0']):.1f} pt, "
                        f"top={float(item['top']):.1f} pt)"
                        for item in items
                    )
                    warnings.append(
                        f"{len(items)} filled short-text shape(s) with fill {fill}, "
                        f"text {text_color} at {heights} pt were detected but not "
                        f"classified as a repeated badge decoration ({shapes}); "
                        "they remain unmeasured pending inspection."
                    )
                    continue
                row_tops = sorted({round(float(item["top"]), 1) for item in items})
                items_per_line = [
                    sum(abs(float(item["top"]) - row_top) <= 0.2 for item in items)
                    for row_top in row_tops
                ]
                element_id = f"local.page.{page_number}.badge-cluster.{cluster_index}"
                clusters.append(
                    NormalizedBadgeCluster(
                        element_id=element_id,
                        page_number=page_number,
                        bbox=NormalizedBox(
                            x0=min(float(item["x0"]) for item in items) / width,
                            top=min(float(item["top"]) for item in items) / height,
                            x1=max(float(item["x1"]) for item in items) / width,
                            bottom=max(float(item["bottom"]) for item in items) / height,
                        ),
                        fill_color_hex=fill,
                        text_color_hex=text_color,
                        height_pt=median(float(item["height"]) for item in items),
                        horizontal_padding_pt=median(float(item["padding"]) for item in items),
                        items_per_line=items_per_line,
                        badge_count=len(items),
                        provenance=MeasurementProvenance(
                            source="local_pdf",
                            provider="pdfplumber",
                            source_element_id=element_id,
                            method=(
                                "filled_shape_containing_one_short_text_run"
                                if any(item["rounded"] for item in items)
                                else "filled_rectangle_containing_one_short_text_run"
                            ),
                        ),
                    )
                )
    return normalized.model_copy(
        update={"badge_clusters": clusters, "warnings": warnings}
    )
