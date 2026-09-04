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
        for page_number, page_blocks in blocks_by_page.items():
            if not 1 <= page_number <= len(pdf.pages):
                continue
            page = pdf.pages[page_number - 1]
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
        counter = votes.get((page_number := block.page_number, block.element_id))
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
        for page_number, page in enumerate(pdf.pages, start=1):
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


def enrich_badges_from_local_pdf(
    normalized: NormalizedLayoutEvidence,
    pdf_path: str | Path,
) -> NormalizedLayoutEvidence:
    """Measure repeated filled short-text shapes without interpreting their text."""

    clusters: list[NormalizedBadgeCluster] = []
    warnings = list(normalized.warnings)
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            width, height = float(page.width), float(page.height)
            words = page.extract_words(extra_attrs=["non_stroking_color"])
            candidates: list[dict[str, object]] = []
            for object_index, item in enumerate(page.rects):
                x0, x1 = sorted((float(item.get("x0") or 0), float(item.get("x1") or 0)))
                top = float(item.get("top") or 0)
                bottom = float(item.get("bottom") or top)
                object_height = abs(bottom - top)
                fill = _hex_from_color(item.get("non_stroking_color"))
                if not item.get("fill") or fill is None:
                    continue
                if not 10 <= object_height <= 30:
                    continue
                flat = next(
                    (
                        other
                        for other in page.rects
                        if other is not item
                        and abs(float(other.get("top") or 0) - (top + object_height / 2)) <= 0.2
                        and _hex_from_color(other.get("non_stroking_color")) == fill
                        and float(other.get("x0") or 0) <= x0
                        and float(other.get("x1") or 0) >= x1
                    ),
                    None,
                )
                full_width = (
                    float(flat["x1"]) - float(flat["x0"])
                    if flat is not None
                    else x1 - x0
                )
                if full_width <= 20:
                    continue
                inside = [
                    word
                    for word in words
                    if x0 <= (float(word["x0"]) + float(word["x1"])) / 2 <= x1
                    and top <= (float(word["top"]) + float(word["bottom"])) / 2 <= bottom
                ]
                if not inside or len({round(float(word["top"]), 1) for word in inside}) != 1:
                    continue
                text = " ".join(str(word["text"]) for word in inside).strip()
                if not text or len(text) > 80:
                    continue
                colors = [
                    color
                    for word in inside
                    if (color := _hex_from_color(word.get("non_stroking_color"))) is not None
                ]
                if not colors:
                    continue
                padding = (
                    max(0.0, (float(flat["x1"]) - float(flat["x0"]) - (x1 - x0)) / 2)
                    if flat is not None
                    else 0.0
                )
                candidates.append(
                    {
                        "object_index": object_index,
                        "x0": float(flat["x0"]) if flat is not None else x0,
                        "x1": float(flat["x1"]) if flat is not None else x1,
                        "top": top,
                        "bottom": bottom,
                        "fill": fill,
                        "text_color": Counter(colors).most_common(1)[0][0],
                        "height": object_height,
                        "padding": padding,
                    }
                )

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
                    warnings.append(
                        "A filled short-text shape was detected but not classified "
                        "as a repeated badge decoration."
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
                            method="filled_rectangle_containing_one_short_text_run",
                        ),
                    )
                )
            potential_rounded_shapes = sum(
                10 <= abs(float(curve.get("bottom") or 0) - float(curve.get("top") or 0)) <= 30
                and bool(curve.get("fill"))
                for curve in page.curves
            )
            if potential_rounded_shapes >= 4 and not candidates:
                warnings.append(
                    "Repeated filled rounded shapes were detected but badge measurement "
                    "failed; decoration remains absent pending review."
                )
    return normalized.model_copy(
        update={"badge_clusters": clusters, "warnings": warnings}
    )
