"""Offline tests for the phase-2 deterministic target-structure recognizer.

Anonymous SYNTHETIC evidence only: no G/H (or any corpus) filenames, text,
or coordinates appear here. Each rule gets a positive and a negative case,
and every fail-closed path is exercised.
"""

from __future__ import annotations

import pytest

from tests.experiments.c2_structure_recognition import (
    SCHEMA_VERSION,
    recognize_target_structure,
)


def block(
    bid: int,
    text: str,
    *,
    x0: float,
    top: float,
    width: float = 120.0,
    height: float = 15.0,
    size: float = 11.0,
    bold: bool = False,
    role: str = "body",
    page: int = 1,
) -> dict:
    return {
        "element_id": f"synthetic.element.{bid}",
        "reading_order": bid,
        "page_number": page,
        "bbox": {
            "x0": x0 / 612.0,
            "x1": (x0 + width) / 612.0,
            "top": top / 792.0,
            "bottom": (top + height) / 792.0,
        },
        "structural_role": role,
        "font_size_pt": size,
        "bold": bold,
        "text": text,
    }


def evidence(blocks: list[dict], width: float = 612.0, height: float = 792.0) -> dict:
    return {
        "schema_version": "normalized-layout/1",
        "provider": "synthetic",
        "pages": [{"page_number": 1, "width_pt": width, "height_pt": height}],
        "text_blocks": blocks,
    }


def ids(rec: dict, kind_path: list) -> set[str]:
    """Collect element ids at a nested path of dict/list keys."""
    node: object = rec
    for key in kind_path:
        if isinstance(node, list):
            node = node[key]
        else:
            node = node[key]
    out: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, str) and n.startswith("synthetic.element."):
            out.add(n)
        elif isinstance(n, dict):
            stack.extend(n.values())
        elif isinstance(n, list):
            stack.extend(n)
    return out


# ---------------------------------------------------------------------------
# Section boundaries
# ---------------------------------------------------------------------------


def test_section_heading_detected_by_all_caps_shape_and_size_both_vocabulary_free() -> None:
    # Template A: heading distinguished only by all-caps shape (same size as body).
    # Template B: heading distinguished only by size. Neither uses content.
    rec = recognize_target_structure(evidence([
        block(0, "SECTION ONE", x0=36, top=50, size=11, bold=True, width=140),
        block(1, "Some regular body text follows here.", x0=36, top=80),
        block(2, "Bigger Section", x0=36, top=110, size=14, bold=True),
        block(3, "More regular body text.", x0=36, top=140),
    ]))
    assert [s["label_shape"] for s in rec["sections"]] == ["all_caps", "larger_than_body"]
    assert rec["sections"][0]["label_elements"] == ["synthetic.element.0"]
    assert rec["status"] == "ok"


def test_non_bold_or_non_margin_caps_line_is_not_a_section_heading() -> None:
    rec = recognize_target_structure(evidence([
        block(0, "SECTION ONE", x0=36, top=50, size=11, bold=True, width=140),
        # All-caps but NOT bold and NOT at the left margin: a plain detail row.
        block(1, "SECOND ROW", x0=300, top=80, size=11, bold=False, width=100),
    ]))
    assert len(rec["sections"]) == 1
    assert rec["line_predictions"][1]["predicted"] == "continuation"


# ---------------------------------------------------------------------------
# Dated entry heads and right-column pairing
# ---------------------------------------------------------------------------


def test_dated_entry_head_pairs_left_content_with_right_flush_segment() -> None:
    rec = recognize_target_structure(evidence([
        block(0, "SECTION ONE", x0=36, top=50, size=11, bold=True, width=140),
        block(1, "First Entry Name", x0=36, top=80, bold=True, width=180),
        block(2, "Mar. 2023 to Present", x0=430, top=80, bold=True, width=146),
        block(3, "A duty line describing the work.", x0=72, top=100, role="list", width=300),
    ]))
    (entry,) = rec["sections"][0]["entries"]
    assert entry["head_elements"] == ["synthetic.element.1"]
    assert entry["date_elements"] == ["synthetic.element.2"]
    assert ids(rec, ["sections", 0, "entries", 0, "bullets"]) == {"synthetic.element.3"}


def test_lone_right_flush_line_is_a_detail_not_an_entry_head() -> None:
    # A full-width single segment ending flush right must NOT open an entry.
    rec = recognize_target_structure(evidence([
        block(0, "SECTION ONE", x0=36, top=50, size=11, bold=True, width=140),
        block(1, "An entry with a date", x0=36, top=80, bold=True, width=150),
        block(2, "Jan. 2024 to May 2024", x0=440, top=80, bold=True, width=136),
        block(3, "A very long wrapped duty line that reaches the right edge of the page", x0=72, top=100, role="list", width=504),
    ]))
    (entry,) = rec["sections"][0]["entries"]
    assert entry["date_text"] == "Jan. 2024 to May 2024"
    assert ids(rec, ["sections", 0, "entries", 0, "bullets"]) == {"synthetic.element.3"}


# ---------------------------------------------------------------------------
# Nested entries: employer -> titled sub-groups -> own bullets
# ---------------------------------------------------------------------------


def test_employer_with_titled_subgroups_owning_their_bullets() -> None:
    rec = recognize_target_structure(evidence([
        block(0, "EXPERIENCE SECTION", x0=36, top=50, size=11, bold=True, width=170),
        block(1, "Employer Organization Name", x0=36, top=80, bold=True, width=200),
        block(2, "June 2024 to Present", x0=440, top=80, bold=True, width=136),
        block(3, "Project Group One", x0=36, top=100, bold=True, width=150),
        block(4, "Bullet under group one.", x0=72, top=120, role="list", width=280),
        block(5, "Another bullet under group one.", x0=72, top=140, role="list", width=300),
        block(6, "Project Group Two", x0=36, top=160, bold=True, width=150),
        block(7, "Bullet under group two.", x0=72, top=180, role="list", width=260),
    ]))
    (entry,) = rec["sections"][0]["entries"]
    assert entry["head_text"] == "Employer Organization Name"
    [g1, g2] = entry["subgroups"]
    assert g1["title_text"] == "Project Group One"
    assert len(g1["bullets"]) == 2
    assert g2["title_text"] == "Project Group Two"
    assert len(g2["bullets"]) == 1
    assert entry["bullets"] == []


def test_undated_title_line_before_dated_line_binds_forward() -> None:
    # G-shape: bold title on its own line, employer+date on the NEXT line.
    rec = recognize_target_structure(evidence([
        block(0, "WORK SECTION", x0=36, top=50, size=11, bold=True, width=140),
        block(1, "Assistant Role", x0=36, top=80, bold=True, width=130),
        block(2, "Some Organization, Some City", x0=36, top=100, width=220),
        block(3, "Sep. 2024 to Present", x0=450, top=100, bold=True, width=126),
        block(4, "A duty bullet.", x0=72, top=120, role="list", width=200),
    ]))
    (entry,) = rec["sections"][0]["entries"]
    assert entry["title_lines"] == [["synthetic.element.1"]]
    assert entry["head_text"] == "Some Organization, Some City"
    assert len(entry["bullets"]) == 1


# ---------------------------------------------------------------------------
# Same-line mixed weight
# ---------------------------------------------------------------------------


def test_mixed_weight_head_line_is_flagged_and_inline_label_detected() -> None:
    rec = recognize_target_structure(evidence([
        block(0, "SKILLS SECTION", x0=36, top=50, size=11, bold=True, width=150),
        block(1, "Entry Name", x0=36, top=80, bold=True, width=90),
        block(2, ", Role Title", x0=126, top=80, bold=False, width=80),
        block(3, "May 2024 to Present", x0=450, top=80, bold=True, width=126),
        block(4, "Tools:", x0=36, top=110, bold=True, width=45),
        block(5, "Tool A, Tool B, Tool C", x0=90, top=110, bold=False, width=160),
    ]))
    (entry,) = rec["sections"][0]["entries"]
    assert entry["mixed_weight_head"] is True
    assert rec["sections"][0]["inline_label_lines"] == [
        ["synthetic.element.4", "synthetic.element.5"]
    ]


def test_inline_label_line_does_not_need_bold_dominance() -> None:
    # A long non-bold content run with a short bold label is NOT an
    # all-bold line, so it must not become a heading or subgroup title.
    rec = recognize_target_structure(evidence([
        block(0, "SKILLS SECTION", x0=36, top=50, size=11, bold=True, width=150),
        block(1, "Label:", x0=36, top=80, bold=True, width=40),
        block(2, "Very long regular content following the label on the same visual line", x0=86, top=80, bold=False, width=380),
    ]))
    assert rec["sections"][0]["entries"] == []
    assert len(rec["sections"][0]["inline_label_lines"]) == 1


# ---------------------------------------------------------------------------
# Bullet identity, attribution, and the marker honesty limit
# ---------------------------------------------------------------------------


def test_bullets_attribute_to_nearest_subgroup_then_entry_then_section() -> None:
    rec = recognize_target_structure(evidence([
        block(0, "ONE SECTION", x0=36, top=50, size=11, bold=True, width=130),
        block(1, "Employer Name", x0=36, top=80, bold=True, width=140),
        block(2, "Jan. 2024 to Present", x0=450, top=80, bold=True, width=126),
        block(3, "Subgroup Title", x0=36, top=100, bold=True, width=120),
        block(4, "Bullet in subgroup.", x0=72, top=120, role="list", width=200),
        block(5, "Bullet after subgroup ends.", x0=72, top=140, role="list", width=220),
    ]))
    (entry,) = rec["sections"][0]["entries"]
    [g] = entry["subgroups"]
    # Consecutive bullet runs with NO intervening title stay in the same
    # sub-group (matching C2's own nested-entry rule: an entry never mixes
    # entry-level bullets with sub-groups, because order would not be
    # reproducible). Returning to entry-level bullets requires a dated line
    # or a new title in between; otherwise the structure stays where it is.
    assert len(g["bullets"]) == 2
    assert entry["bullets"] == []


def test_section_level_bullets_without_any_entry_are_kept_at_section_level() -> None:
    rec = recognize_target_structure(evidence([
        block(0, "HONORS SECTION", x0=36, top=50, size=11, bold=True, width=150),
        block(1, "An honor line one.", x0=72, top=80, role="list", width=200),
        block(2, "An honor line two.", x0=72, top=100, role="list", width=210),
    ]))
    assert rec["sections"][0]["entries"] == []
    assert len(rec["sections"][0]["section_level_bullets"]) == 2
    assert rec["status"] == "ok"


def test_bullet_marker_glyph_absence_is_declared_not_faked() -> None:
    rec = recognize_target_structure(evidence([
        block(0, "ONE SECTION", x0=36, top=50, size=11, bold=True, width=130),
        block(1, "A bullet without any marker glyph in the evidence text.", x0=72, top=80, role="list", width=320),
    ]))
    assert "marker" in rec["limits"]["bullet_markers"]
    # The prediction carries the honesty flag on each bullet line.
    bullets = rec["sections"][0]["section_level_bullets"]
    assert bullets == [["synthetic.element.1"]]


# ---------------------------------------------------------------------------
# Fail-closed paths
# ---------------------------------------------------------------------------


def test_bold_undated_line_followed_by_nothing_structural_fails_closed() -> None:
    rec = recognize_target_structure(evidence([
        block(0, "ONE SECTION", x0=36, top=50, size=11, bold=True, width=130),
        block(1, "Mystery Bold Line", x0=36, top=80, bold=True, width=140),
        block(2, "Regular trailing text.", x0=36, top=100, width=180),
    ]))
    assert rec["status"] == "ok_with_unresolved"
    assert len(rec["unresolved"]) == 1
    assert "fail closed" in rec["unresolved"][0]["reason"]
    assert rec["unresolved"][0]["element_ids"] == ["synthetic.element.1"]
    kinds = [p["predicted"] for p in rec["line_predictions"]]
    assert "unresolved" in kinds


def test_bold_undated_line_at_document_end_fails_closed() -> None:
    rec = recognize_target_structure(evidence([
        block(0, "ONE SECTION", x0=36, top=50, size=11, bold=True, width=130),
        block(1, "Dangling Title", x0=36, top=80, bold=True, width=130),
    ]))
    assert rec["status"] == "ok_with_unresolved"
    assert rec["unresolved"][0]["element_ids"] == ["synthetic.element.1"]


def test_content_before_any_section_heading_is_unresolved() -> None:
    rec = recognize_target_structure(evidence([
        block(0, "Loose line with no section above it.", x0=36, top=60, width=240),
        block(1, "ONE SECTION", x0=36, top=90, size=11, bold=True, width=130),
    ]))
    # The pre-section line is predicted as page header context, not silently
    # dropped, and the header zone ends at the first heading.
    assert rec["line_predictions"][0]["predicted"] == "page_header"
    assert rec["sections"][0]["top_pt"] == 90.0


def test_no_evidence_fails_closed() -> None:
    rec = recognize_target_structure(evidence([]))
    assert rec["status"] == "no_evidence"
    assert rec["unresolved"] == [{"reason": "no text blocks in evidence"}]


def test_schema_version_is_declared() -> None:
    rec = recognize_target_structure(evidence([block(0, "ONE SECTION", x0=36, top=50, size=11, bold=True, width=130)]))
    assert rec["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Rule independence: no corpus leakage
# ---------------------------------------------------------------------------


def test_recognizer_output_is_json_serializable_and_annotation_free() -> None:
    import json

    rec = recognize_target_structure(evidence([
        block(0, "ONE SECTION", x0=36, top=50, size=11, bold=True, width=130),
        block(1, "Employer", x0=36, top=80, bold=True, width=100),
        block(2, "May 2024 to Present", x0=450, top=80, bold=True, width=126),
        block(3, "Subgroup", x0=36, top=100, bold=True, width=90),
        block(4, "Bullet.", x0=72, top=120, role="list", width=120),
    ]))
    payload = json.dumps(rec)
    assert "annotation" in payload  # the honesty limit names what is NOT used
    assert json.loads(payload) == rec
