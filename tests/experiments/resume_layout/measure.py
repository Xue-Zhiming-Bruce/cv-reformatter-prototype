"""Measurement of rendered HTML/PDF output.

Split out of ``c2_renderer``. Pure measurement: character and rule colours
normalized to hex, rendered heading positions, rule extents and the region tests
built on them. The delivery gates import these; nothing here imports a gate.
"""

from __future__ import annotations

from tests.experiments.resume_layout.text import _norm
from pathlib import Path
from typing import Any


RULE_REGION_ABOVE_PT = 40.0


RULE_REGION_BELOW_PT = 60.0


RULE_GEOMETRY_TOLERANCE_PT = 1.0


CSS_EXTENT_TOLERANCE_PT = 0.05


def _pdf_color_hex(color: Any) -> str | None:
    """Normalize a pdfplumber PDF color (stroking/non-stroking) to hex RGB.

    Deterministic: None stays None (unmeasured); a scalar or 1-tuple is gray
    (replicated); 3-tuple is RGB; 4-tuple is CMYK (standard conversion).
    Values are 0..1 floats (values above 1 are treated as already 8-bit).
    Output is uppercase ``#RRGGBB`` (C2-0cC color verification)."""
    if color is None:
        return None
    values = list(color) if isinstance(color, (list, tuple)) else [color]
    if not values:
        return None
    if len(values) == 4:  # CMYK
        c, m, y, k = (float(v) for v in values)
        values = [
            (1 - c) * (1 - k),
            (1 - m) * (1 - k),
            (1 - y) * (1 - k),
        ]
    elif len(values) == 1:
        values = [values[0], values[0], values[0]]
    elif len(values) != 3:
        return None
    scaled = [
        round(float(v) * 255) if float(v) <= 1.0 else round(float(v))
        for v in values
    ]
    return "#%02X%02X%02X" % tuple(max(0, min(255, v)) for v in scaled)


def _rendered_heading_positions(pdf: Path | None, labels: list[str]) -> dict[str, tuple[int, float]]:
    """First rendered (page, top_pt) of each section heading label in the
    exported PDF (line-granularity text match; case/whitespace insensitive)."""
    if pdf is None:
        return {}
    import pdfplumber

    wanted = {_norm(label) for label in labels if label}
    positions: dict[str, tuple[int, float]] = {}
    with pdfplumber.open(pdf) as document:
        for page_index, page in enumerate(document.pages, 1):
            lines: dict[float, list[str]] = {}
            for word in page.extract_words():
                top = round(float(word["top"]), 1)
                lines.setdefault(top, []).append(word["text"])
            for top in sorted(lines):
                line_text = _norm(" ".join(lines[top]))
                if line_text in wanted and line_text not in positions:
                    positions[line_text] = (page_index, top)
    return positions


def _rendered_rule_extents(pdf: Path | None) -> list[dict[str, Any]]:
    """Rendered horizontal vector objects (rules) with their page and vertical
    position, so a required rule can be associated with the correct page and
    section vertical region — not only its x-extent. C2-0cC adds the measured
    stroke color (normalized hex) additively; existing consumers unaffected."""
    if pdf is None:
        return []
    import pdfplumber

    rendered: list[dict[str, Any]] = []
    with pdfplumber.open(pdf) as document:
        for page_index, page in enumerate(document.pages, 1):
            for obj in [*page.rects, *page.lines]:
                height = float(obj["bottom"]) - float(obj["top"])
                width = float(obj["x1"]) - float(obj["x0"])
                if height <= 2.0 and width > 2.0:
                    rendered.append(
                        {
                            "page": page_index,
                            "top_pt": round(float(obj["top"]), 3),
                            "x0_pt": round(float(obj["x0"]), 3),
                            "x1_pt": round(float(obj["x1"]), 3),
                            "stroke_pt": round(
                                float(obj.get("linewidth") or 0.0) or max(height, 0.0), 3
                            ),
                            "color_hex": _pdf_color_hex(obj.get("stroking_color")),
                        }
                    )
    return rendered


def _rule_in_section_region(
    extent: dict[str, Any],
    heading_position: tuple[int, float] | None,
    placement: str | None,
) -> bool:
    """A rendered rule belongs to a section only when it sits on the heading's
    page and within the documented vertical region on the correct side of the
    rendered heading text (above for above_heading, below for below_heading)."""
    if heading_position is None:
        return False
    page, heading_top = heading_position
    if extent["page"] != page:
        return False
    if placement == "below_heading":
        return -2.0 <= extent["top_pt"] - heading_top <= RULE_REGION_BELOW_PT
    return -2.0 <= heading_top - extent["top_pt"] <= RULE_REGION_ABOVE_PT
