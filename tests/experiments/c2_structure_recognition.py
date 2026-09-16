"""Deterministic target-structure recognition from normalized layout evidence.

C2 phase-2 research module (owner work order 2026-09-18): answers ONE question
— can the semantic structure C2 needs (section boundaries; dated entry heads;
employer -> titled sub-groups -> own bullets; same-line mixed weights; bullet
identity/attribution) be inferred by GENERIC, EXPLAINABLE, deterministic rules
from the cached provider-neutral evidence, WITHOUT any annotation input,
without G/H-specific branches (no filenames, no target text, no single
coordinate hacks), and failing CLOSED wherever the rules cannot decide?

This module is EVALUATION-ONLY research code. Nothing in the C2 pipeline
imports it; the recognition output must never become runtime state. Rules use
only generic geometry/typography features (boldness, size vs body mode,
all-caps SHAPE, left-margin alignment, right-edge alignment, vertical line
grouping, provider list roles, indentation, adjacency).

Two passes, both explainable:

1. LINE CLASSIFICATION (first match wins, each carries its rule name):
   page_header | section_heading | bullet | dated_entry_head |
   bold_undated_left | inline_label_line | continuation
2. RESOLUTION of ``bold_undated_left`` by ADJACENCY to what FOLLOWS:
   bullets -> titled sub-group; a dated line -> entry title line; anything
   else -> UNRESOLVED (fail closed: never guess and continue).

Every predicted item carries the evidence ``element_id``s it was derived from.
Bullet MARKER glyphs are absent from normalized evidence (the raw provider
response keeps them as list-label elements); identity therefore comes from the
provider list role / measured indentation, and the output says so honestly.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

SCHEMA_VERSION = "c2-target-structure-recognition/1"

# A segment belongs to the right zone when its block's right edge is nearly
# flush with the PAGE's right margin. Right-column dates are right-ALIGNED to
# the page margin; left content and full-width bullets are not. (The x0 of
# these provider blocks is unreliable: some right-column blocks carry padded
# boxes that start mid-page, so only the FLUSH RIGHT EDGE is used, and the
# reference is the page width, not the widest evidence block, so a narrow
# document cannot make its own content look flush.)
RIGHT_FLUSH_PAGE_SHARE = 0.9

# Left-margin alignment tolerance in points.
LEFT_MARGIN_TOLERANCE_PT = 3.0

# Two blocks sit on the same visual line when their vertical overlap is at
# least this share of the SMALLER block's height.
LINE_Y_OVERLAP_SHARE = 0.5

# A bold segment "dominates" a line when it covers at least this share of the
# line's total text width (separates all-bold lines from inline-label lines
# such as "<bold label>: <regular content>").
BOLD_DOMINANT_SHARE = 0.5

# Bullet fallback indent, in body text sizes.
BULLET_INDENT_BODY_SIZES = 1.5


def _is_all_caps(text: str) -> bool:
    """Generic case-SHAPE feature: >=3 letters, no lowercase letters.

    Punctuation and digits are ignored. This is a text-shape class, not a
    content match: no target-specific strings appear anywhere in this module.
    """
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 3 and not any(c.islower() for c in letters)


def _load_blocks(evidence: dict[str, Any]) -> dict[str, Any]:
    pages = {p["page_number"]: p for p in evidence["pages"]}
    blocks = []
    for b in evidence["text_blocks"]:
        page = pages[b["page_number"]]
        width_pt, height_pt = page["width_pt"], page["height_pt"]
        bb = b["bbox"]
        blocks.append({
            "element_id": b["element_id"],
            "reading_order": b["reading_order"],
            "page": b["page_number"],
            "x0": bb["x0"] * width_pt,
            "x1": bb["x1"] * width_pt,
            "top": bb["top"] * height_pt,
            "bottom": bb["bottom"] * height_pt,
            "size": b["font_size_pt"],
            "bold": bool(b["bold"]),
            "role": b["structural_role"],
            "text": b["text"],
        })
    return {
        "blocks": sorted(blocks, key=lambda x: (x["page"], x["top"], x["x0"])),
        "width_pt": max(p["width_pt"] for p in pages.values()),
    }


def _group_lines(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge same-page blocks whose vertical overlap is large into lines."""
    lines: list[dict[str, Any]] = []
    for b in blocks:
        for line in lines:
            if line["page"] != b["page"]:
                continue
            overlap = min(line["bottom"], b["bottom"]) - max(line["top"], b["top"])
            smaller = min(line["bottom"] - line["top"], b["bottom"] - b["top"])
            if smaller > 0 and overlap / smaller >= LINE_Y_OVERLAP_SHARE:
                line["segments"].append(b)
                line["top"] = min(line["top"], b["top"])
                line["bottom"] = max(line["bottom"], b["bottom"])
                break
        else:
            lines.append({
                "page": b["page"], "top": b["top"], "bottom": b["bottom"],
                "segments": [b],
            })
    for line in lines:
        line["segments"].sort(key=lambda s: s["x0"])
        line["element_ids"] = [s["element_id"] for s in line["segments"]]
        widths = [max(s["x1"] - s["x0"], 1.0) for s in line["segments"]]
        total = sum(widths)
        bold_share = sum(
            w for s, w in zip(line["segments"], widths) if s["bold"]
        ) / total
        line["bold_dominant"] = bold_share >= BOLD_DOMINANT_SHARE
        line["mixed_weight"] = len({s["bold"] for s in line["segments"]}) > 1
        line["left_x0"] = line["segments"][0]["x0"]
        letters = [c for s in line["segments"] for c in s["text"] if c.isalpha()]
        line["all_caps"] = len(letters) >= 3 and not any(c.islower() for c in letters)
    return lines


def recognize_target_structure(evidence: dict[str, Any]) -> dict[str, Any]:
    """Predict the C2-relevant semantic structure of one target document.

    Input: the provider-neutral ``normalized-layout/1`` evidence dict (as
    persisted in the C2 target cache). Annotations are never an input.
    Output: a JSON-serializable prediction; every ambiguity becomes an
    explicit ``unresolved`` record (fail closed).
    """
    loaded = _load_blocks(evidence)
    blocks = loaded["blocks"]
    width_pt = loaded["width_pt"]
    if not blocks:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "no_evidence",
            "body_size_pt": None,
            "sections": [],
            "line_predictions": [],
            "unresolved": [{"reason": "no text blocks in evidence"}],
        }

    # Body-size mode: the size carrying the most alphabetic text.
    size_weights: Counter[float] = Counter()
    for b in blocks:
        size_weights[round(b["size"], 1)] += sum(1 for c in b["text"] if c.isalpha())
    body_size = size_weights.most_common(1)[0][0]

    left_margin = min(b["x0"] for b in blocks)
    right_edge = max(b["x1"] for b in blocks)

    def is_bullet(b: dict[str, Any]) -> bool:
        # Provider list role first, then a pure-indentation fallback so the
        # rule does not depend on one provider's vocabulary. The fallback
        # only accepts NEAR-LEFT indents: real bullet columns sit a small
        # step right of the margin, while right-column rows sit far out.
        if b["role"] == "list":
            return True
        return (
            left_margin + BULLET_INDENT_BODY_SIZES * body_size
            < b["x0"]
            < left_margin + 0.25 * width_pt
        )

    def right_date_segment(line: dict[str, Any]) -> dict[str, Any] | None:
        if len(line["segments"]) < 2:
            # A lone right-flush segment is a full-width line (bullet wrap,
            # justified row), never a left-head + right-date pairing.
            return None
        for s in line["segments"]:
            if s["x1"] >= width_pt * RIGHT_FLUSH_PAGE_SHARE:
                return s
        return None

    # ---- Pass 1: classify every line (first rule that matches wins) --------
    kinds: list[str] = []
    dates: list[dict[str, Any] | None] = []
    lines = _group_lines(blocks)
    seen_section = False
    for line in lines:
        primary = line["segments"][0]
        heading = (
            line["bold_dominant"]
            and primary["x0"] <= left_margin + LEFT_MARGIN_TOLERANCE_PT
            and (line["all_caps"] or primary["size"] > body_size + 0.5)
        )
        if not seen_section:
            if heading:
                seen_section = True
                kinds.append("section_heading")
            else:
                kinds.append("page_header")
            dates.append(None)
            continue
        if heading:
            kinds.append("section_heading")
            dates.append(None)
            continue
        if is_bullet(primary):
            kinds.append("bullet")
            dates.append(None)
            continue
        right = right_date_segment(line)
        if right is not None and len(line["segments"]) > 1:
            # A right-flush segment with NO left part (e.g. a lone right-aligned
            # detail row) is not an entry head; it falls through and attaches
            # to the current entry as a detail line.
            kinds.append("dated_entry_head")
            dates.append(right)
            continue
        if (
            line["bold_dominant"]
            and primary["x0"] <= left_margin + LEFT_MARGIN_TOLERANCE_PT
            and not line["all_caps"]
            and primary["size"] <= body_size + 0.5
        ):
            kinds.append("bold_undated_left")
            dates.append(None)
            continue
        if line["mixed_weight"]:
            kinds.append("inline_label_line")
            dates.append(None)
            continue
        kinds.append("continuation")
        dates.append(None)

    # ---- Pass 2: resolve bold_undated_left by adjacency to what follows ----
    # bullets -> titled sub-group; dated head -> entry title line; else
    # unresolved (fail closed).
    resolved = list(kinds)
    for i, kind in enumerate(kinds):
        if kind != "bold_undated_left":
            continue
        nxt = kinds[i + 1] if i + 1 < len(kinds) else None
        if nxt == "bullet":
            resolved[i] = "subgroup_title"
        elif nxt == "dated_entry_head":
            resolved[i] = "entry_title_line"
        else:
            resolved[i] = "unresolved"

    # ---- Assemble the structural tree --------------------------------------
    sections: list[dict[str, Any]] = []
    section: dict[str, Any] | None = None
    entry: dict[str, Any] | None = None
    subgroup: dict[str, Any] | None = None
    unresolved: list[dict[str, Any]] = []

    def close_pending() -> None:
        nonlocal subgroup
        subgroup = None

    for line, kind, date in zip(lines, resolved, dates):
        element_ids = line["element_ids"]
        if kind == "section_heading":
            section = {
                "label_shape": "all_caps" if line["all_caps"] else "larger_than_body",
                "label_elements": element_ids,
                "top_pt": round(line["top"], 1),
                "entries": [],
                "section_level_bullets": [],
                "section_level_lines": [],
                "inline_label_lines": [],
            }
            sections.append(section)
            entry, subgroup = None, None
        elif kind == "page_header":
            pass
        elif section is None:
            unresolved.append({
                "reason": "content line before any section heading",
                "element_ids": element_ids,
            })
        elif kind == "bullet":
            if subgroup is not None:
                subgroup["bullets"].append(element_ids)
            elif entry is not None:
                entry["bullets"].append(element_ids)
            else:
                section["section_level_bullets"].append(element_ids)
        elif kind == "dated_entry_head":
            left_segments = [s for s in line["segments"] if s is not date]
            if entry is not None and entry.get("_forward_title"):
                # Fill the pending forward-declared entry (title line seen
                # directly above) instead of opening a new one.
                entry["_forward_title"] = False
                entry["head_elements"] = [s["element_id"] for s in left_segments]
                entry["head_text"] = " ".join(s["text"] for s in left_segments).strip()
                entry["date_elements"] = [date["element_id"]]
                entry["date_text"] = date["text"]
                entry["mixed_weight_head"] = len({s["bold"] for s in left_segments}) > 1
                continue
            entry = {
                "head_elements": [s["element_id"] for s in left_segments],
                "head_text": " ".join(s["text"] for s in left_segments).strip(),
                "date_elements": [date["element_id"]],
                "date_text": date["text"],
                "mixed_weight_head": len({s["bold"] for s in left_segments}) > 1,
                "title_lines": [],
                "detail_lines": [],
                "subgroups": [],
                "bullets": [],
            }
            section["entries"].append(entry)
            close_pending()
        elif kind == "entry_title_line":
            if entry is None or not entry.get("_forward_title"):
                # Title line before its dated head: declare the entry
                # forward; the NEXT dated line fills it.
                entry = {
                    "head_elements": [],
                    "head_text": "",
                    "date_elements": [],
                    "date_text": None,
                    "mixed_weight_head": False,
                    "title_lines": [element_ids],
                    "detail_lines": [],
                    "subgroups": [],
                    "bullets": [],
                    "_forward_title": True,
                }
                section["entries"].append(entry)
            else:
                # Consecutive title lines of one pending entry.
                entry["title_lines"].append(element_ids)
        elif kind == "subgroup_title":
            if entry is None:
                unresolved.append({
                    "reason": "titled sub-group with no enclosing dated entry",
                    "element_ids": element_ids,
                })
                subgroup = None
            else:
                subgroup = {
                    "title_elements": element_ids,
                    "title_text": line["segments"][0]["text"],
                    "bullets": [],
                }
                entry["subgroups"].append(subgroup)
        elif kind == "inline_label_line":
            section["inline_label_lines"].append(element_ids)
            close_pending()
        elif kind == "unresolved":
            unresolved.append({
                "reason": (
                    "bold undated left-margin line followed by neither bullets "
                    "nor a dated line; refusing to guess (fail closed)"
                ),
                "element_ids": element_ids,
            })
            close_pending()
        elif kind == "continuation":
            if entry is not None:
                entry["detail_lines"].append(element_ids)
            else:
                # A free paragraph in a section is unambiguous section-level
                # content, not a structural ambiguity.
                section["section_level_lines"].append(element_ids)
            close_pending()

    for e in section_entries_all(sections):
        e.pop("_forward_title", None)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if not unresolved else "ok_with_unresolved",
        "body_size_pt": body_size,
        "left_margin_pt": round(left_margin, 1),
        "right_edge_pt": round(right_edge, 1),
        "sections": sections,
        "line_predictions": [
            {
                "predicted": kind,
                "top_pt": round(line["top"], 1),
                "element_ids": line["element_ids"],
                "mixed_weight": line["mixed_weight"],
            }
            for line, kind in zip(lines, resolved)
        ],
        "unresolved": unresolved,
        "limits": {
            "bullet_markers": (
                "bullet MARKER glyphs are absent from normalized evidence; "
                "bullet identity comes from the provider list role or measured "
                "indentation, never from the marker glyph"
            ),
            "annotations": "annotations were never an input to this module",
        },
    }


def section_entries_all(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for s in sections for e in s["entries"]]
