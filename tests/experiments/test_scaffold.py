from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.experiments.resume_layout.scaffold import (
    BodyEntryScaffold,
    BodyHeadingScaffold,
    BodyScaffold,
    HeaderScaffold,
    derive_body_scaffold,
    derive_body_tier_targets,
    derive_header_scaffold,
)
from pydantic import ValidationError


























def _chars_of(words):
    chars = []
    for index, word in enumerate(words):
        count = len(word["text"])
        for offset, character in enumerate(word["text"]):
            step = (word["x1"] - word["x0"]) / count
            chars.append({
                "text": character,
                "top": word["top"],
                "bottom": word["bottom"],
                "x0": word["x0"] + offset * step,
                "x1": word["x0"] + (offset + 1) * step,
            })
        if index + 1 < len(words):
            chars.append({"text": " ", "top": word["top"], "bottom": word["bottom"], "x0": word["x1"]})
    return chars



















def test_derive_body_tier_targets_comes_from_target_measurement(monkeypatch) -> None:
    class FakePage:
        def extract_words(self):
            return [
                {"text": "Level", "top": 100.0, "bottom": 110.0, "x0": 46.909},
                {"text": "\u2022", "top": 120.0, "bottom": 130.0, "x0": 60.0},
                {"text": "Item", "top": 120.0, "bottom": 130.0, "x0": 70.909},
                {"text": "text", "top": 120.3, "bottom": 130.0, "x0": 130.0},
            ]

        chars = [
            {"text": "L", "top": 100.0, "bottom": 110.0, "x0": 46.909, "x1": 49.0},
            {"text": "e", "top": 100.0, "bottom": 110.0, "x0": 50.0, "x1": 52.0},
            {"text": "v", "top": 100.0, "bottom": 110.0, "x0": 53.0, "x1": 55.0},
            {"text": "e", "top": 100.0, "bottom": 110.0, "x0": 56.0, "x1": 58.0},
            {"text": "l", "top": 100.0, "bottom": 110.0, "x0": 59.0, "x1": 61.0},
            {"text": "\u2022", "top": 120.0, "bottom": 130.0, "x0": 60.0, "x1": 65.0},
            {"text": "I", "top": 120.0, "bottom": 130.0, "x0": 70.909, "x1": 73.0},
            {"text": "t", "top": 120.0, "bottom": 130.0, "x0": 74.0, "x1": 76.0},
            {"text": "e", "top": 120.0, "bottom": 130.0, "x0": 77.0, "x1": 79.0},
            {"text": "m", "top": 120.0, "bottom": 130.0, "x0": 80.0, "x1": 84.0},
            {"text": "t", "top": 120.3, "bottom": 130.0, "x0": 130.0, "x1": 133.0},
        ]
        rects = []
        curves = []

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda path: FakePdf())

    tiers = derive_body_tier_targets(Path("target.pdf"), 46.909)

    assert tiers == {"l1": 46.909, "bullet_dot": 60.0, "bullet_text": 70.909}

































# --- C1 body rollout: section headings (Phase A) + entry two-column rows (Phase B) ---



class _FakePage:
    def __init__(self, words, chars=None, rects=(), curves=(), lines=(), width=612.0):
        self._words = words
        self.chars = chars if chars is not None else _chars_of(words)
        self.rects = list(rects)
        self.curves = list(curves)
        self.lines = list(lines)
        self.width = width

    def extract_words(self, *args, **kwargs):
        # pdfplumber's extra_attrs (e.g. fontname for C2-0cS grid detection)
        # is accepted and ignored: these fixtures carry no font data, so the
        # grid detection sees no bold words and honestly finds no grid.
        return self._words


class _FakePdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_derive_body_scaffold_measures_headings_rules_and_entry_columns(monkeypatch) -> None:
    words = [
        {"text": "previous", "top": 555.70, "bottom": 566.61, "x0": 60.0, "x1": 100.0},
        {"text": "Experience", "top": 98.99, "bottom": 113.34, "x0": 36.0, "x1": 106.74},
        {"text": "Microsoft", "top": 123.68, "bottom": 134.59, "x0": 46.909, "x1": 96.98},
        {"text": "Redmond,WA", "top": 123.68, "bottom": 134.59, "x0": 500.0, "x1": 575.94},
        {"text": "SoftwareEngineerII", "top": 137.28, "bottom": 148.19, "x0": 46.909, "x1": 150.0},
        {"text": "April2019\u2013Present", "top": 137.28, "bottom": 148.19, "x0": 400.0, "x1": 576.01},
        {"text": "Skills", "top": 584.98, "bottom": 599.33, "x0": 36.0, "x1": 68.52},
    ]
    summary = {
        "pages": [{"width_pt": 612.0, "height_pt": 792.0}],
        "margins_pt": {"default": {"left": 36.0, "right": 36.0}},
        "style_groups": {"style_h": {"font_family": "Lato", "font_size_pt": 14.346, "line_height_pt": 17.25, "bold": True}},
        "elements": [
            {"id": "adobe.1", "page": 1, "style_id": "style_h", "structural_role": "heading_candidate",
             "text_sample": "Experience", "entry_path": None, "bbox_pt": {"x0": 36.0, "top": 79.4, "x1": 106.74, "bottom": 119.1}},
            {"id": "adobe.2", "page": 1, "style_id": "style_h", "structural_role": "heading_candidate",
             "text_sample": "Skills", "entry_path": None, "bbox_pt": {"x0": 36.0, "top": 565.4, "x1": 68.52, "bottom": 605.1}},
            {"id": "adobe.3", "page": 1, "style_id": "style_b", "structural_role": "body",
             "text_sample": "Microsoft", "entry_path": "//Document/Sect[2]/Sect",
             "bbox_pt": {"x0": 46.909, "top": 123.68, "x1": 96.98, "bottom": 134.59}},
            {"id": "adobe.4", "page": 1, "text_sample": "—", "bbox_pt": {"x0": 160.0, "top": 45.0, "x1": 172.0, "bottom": 50.0}},
            {"id": "adobe.5", "page": 1, "text_sample": "—", "bbox_pt": {"x0": 300.0, "top": 45.0, "x1": 312.0, "bottom": 50.0}},
        ],
        "rules": [
            {"page_number": 1, "bbox": {"top": 92.899 / 792}, "stroke_width_pt": 0.398,
             "gap_above_pt": 11.118, "gap_below_pt": 6.092, "color_hex": "#000000"},
            {"page_number": 1, "bbox": {"top": 578.885 / 792}, "stroke_width_pt": 0.398,
             "gap_above_pt": 12.2805, "gap_below_pt": 6.0933, "color_hex": "#000000"},
        ],
    }
    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([_FakePage(words)]))

    scaffold = derive_body_scaffold(Path("target.pdf"), summary)

    assert [heading.verbatim for heading in scaffold.headings] == ["Experience", "Skills"]
    first, second = scaffold.headings
    assert first.top_pt == 98.99 and first.x0_pt == 36.0
    assert first.font_height_pt == pytest.approx(14.35)
    assert first.font_size_pt == 14.346 and first.line_height_pt == 17.25 and first.bold
    assert first.rule_top_pt == 92.899
    assert first.rule_gap_above_pt == 11.118  # header -> rule
    assert first.rule_gap_below_pt == 6.092  # rule -> heading text
    assert first.content_gap_below_pt == pytest.approx(10.34)  # heading text -> first content row
    assert second.rule_gap_above_pt == 12.2805  # section-to-section rules differ from the header rule
    assert scaffold.entry.left_x0_pt == 46.909
    assert scaffold.entry.right_x1_pt == 576.0  # content boundary (owner ruling: page width − measured right margin)
    assert scaffold.entry.right_row_top_delta_pt == 0.0  # right cell shares the left cell's text row


def test_contact_separator_measured_from_standalone_target_elements(monkeypatch) -> None:
    # Owner ruling 2026-09-11 (E→F final rerun): the target may render the
    # contact separator as its own element between adjacent values with no
    # surrounding whitespace, invisible to the line-text heuristic. A
    # standalone non-word element on the contact row, repeated between value
    # elements, is the design's separator.
    page = _FakePage([
        {"text": "Experience", "top": 98.99, "bottom": 113.34, "x0": 36.0, "x1": 106.74},
        {"text": "555-123-4567|alex@email.com|github.com/x", "top": 56.4, "bottom": 73.99, "x0": 136.6, "x1": 475.4},
        {"text": "Microsoft", "top": 123.68, "bottom": 134.59, "x0": 46.909, "x1": 96.98},
    ])

    class _FakePdf:
        def __init__(self, pages):
            self.pages = pages

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    import pdfplumber

    summary = {
        "pages": [{"width_pt": 612.0, "height_pt": 792.0}],
        "margins_pt": {"default": {"left": 36.0, "right": 36.0}},
        "style_groups": {"style_h": {"font_family": "Roboto", "font_size_pt": 14.346, "line_height_pt": 17.25, "bold": True}},
        "elements": [
            {"id": "adobe.1", "page": 1, "style_id": "style_h", "structural_role": "heading_candidate",
             "text_sample": "Experience", "entry_path": None, "bbox_pt": {"x0": 36.0, "top": 79.4, "x1": 106.74, "bottom": 119.1}},
            {"id": "adobe.2", "page": 1, "style_id": "style_b", "structural_role": "body",
             "text_sample": "555-123-4567", "entry_path": None, "bbox_pt": {"x0": 136.6, "top": 57.0, "x1": 200.0, "bottom": 73.0}},
            {"id": "adobe.3", "page": 1, "style_id": "style_b", "structural_role": "body",
             "text_sample": "|", "entry_path": None, "bbox_pt": {"x0": 200.1, "top": 58.0, "x1": 205.0, "bottom": 73.0}},
            {"id": "adobe.4", "page": 1, "style_id": "style_b", "structural_role": "body",
             "text_sample": "alex@email.com", "entry_path": None, "bbox_pt": {"x0": 205.1, "top": 57.0, "x1": 260.0, "bottom": 73.0}},
            {"id": "adobe.5", "page": 1, "style_id": "style_b", "structural_role": "body",
             "text_sample": "|", "entry_path": None, "bbox_pt": {"x0": 260.1, "top": 58.0, "x1": 265.0, "bottom": 73.0}},
            {"id": "adobe.6", "page": 1, "style_id": "style_b", "structural_role": "body",
             "text_sample": "github.com/x", "entry_path": None, "bbox_pt": {"x0": 265.1, "top": 57.0, "x1": 320.0, "bottom": 73.0}},
            {"id": "adobe.7", "page": 1, "style_id": "style_b", "structural_role": "body",
             "text_sample": "Microsoft", "entry_path": "//Document/Sect[2]/Sect",
             "bbox_pt": {"x0": 46.909, "top": 123.68, "x1": 96.98, "bottom": 134.59}},
            {"id": "adobe.4", "page": 1, "text_sample": "—", "bbox_pt": {"x0": 160.0, "top": 45.0, "x1": 172.0, "bottom": 50.0}},
            {"id": "adobe.5", "page": 1, "text_sample": "—", "bbox_pt": {"x0": 300.0, "top": 45.0, "x1": 312.0, "bottom": 50.0}},
        ],
        "rules": [],
    }
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([page]))

    scaffold = derive_body_scaffold(Path("target.pdf"), summary)
    assert scaffold.contact_icons_present is False
    assert scaffold.contact_separator == " | "


def test_contact_separator_stays_none_when_standalone_element_appears_once(monkeypatch) -> None:
    # A one-off non-word element (e.g. a single icon glyph) cannot reach the
    # ≥2 repetition bar and must not be declared the design's separator.
    page = _FakePage([
        {"text": "Experience", "top": 98.99, "bottom": 113.34, "x0": 36.0, "x1": 106.74},
        {"text": "alex@email.com", "top": 56.4, "bottom": 73.99, "x0": 136.6, "x1": 300.0},
        {"text": "Microsoft", "top": 123.68, "bottom": 134.59, "x0": 46.909, "x1": 96.98},
    ])

    class _FakePdf:
        def __init__(self, pages):
            self.pages = pages

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    import pdfplumber

    summary = {
        "pages": [{"width_pt": 612.0, "height_pt": 792.0}],
        "margins_pt": {"default": {"left": 36.0, "right": 36.0}},
        "style_groups": {"style_h": {"font_family": "Lato", "font_size_pt": 14.346, "line_height_pt": 17.25, "bold": True}},
        "elements": [
            {"id": "adobe.1", "page": 1, "style_id": "style_h", "structural_role": "heading_candidate",
             "text_sample": "Experience", "entry_path": None, "bbox_pt": {"x0": 36.0, "top": 79.4, "x1": 106.74, "bottom": 119.1}},
            {"id": "adobe.2", "page": 1, "style_id": "style_b", "structural_role": "body",
             "text_sample": "alex@email.com", "entry_path": None, "bbox_pt": {"x0": 136.6, "top": 57.0, "x1": 300.0, "bottom": 73.0}},
            {"id": "adobe.3", "page": 1, "style_id": "style_b", "structural_role": "body",
             "text_sample": "\uf095", "entry_path": None, "bbox_pt": {"x0": 300.1, "top": 58.0, "x1": 305.0, "bottom": 73.0}},
            {"id": "adobe.4", "page": 1, "style_id": "style_b", "structural_role": "body",
             "text_sample": "Microsoft", "entry_path": "//Document/Sect[2]/Sect",
             "bbox_pt": {"x0": 46.909, "top": 123.68, "x1": 96.98, "bottom": 134.59}},
            {"id": "adobe.4", "page": 1, "text_sample": "—", "bbox_pt": {"x0": 160.0, "top": 45.0, "x1": 172.0, "bottom": 50.0}},
            {"id": "adobe.5", "page": 1, "text_sample": "—", "bbox_pt": {"x0": 300.0, "top": 45.0, "x1": 312.0, "bottom": 50.0}},
        ],
        "rules": [],
    }
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([page]))

    scaffold = derive_body_scaffold(Path("target.pdf"), summary)
    assert scaffold.contact_separator is None


























# --- E→F generalization freeze fix (owner ruling 2026-09-10) ---















# --- E→F fifth freeze fix (owner ruling): container inheritance normalization ---





































# --- E→F twelfth freeze: render-aware duplicate-bullet detection ---









# --- Stage-1 infrastructure round (owner work order 2026-09-10/11) ----------
































def test_header_scaffold_rows_carry_their_own_measured_style(monkeypatch) -> None:
    """E→D cold start (2026-09-11): each header row compiles from the style
    of its own measured line, not the positional group heuristic that gave
    the contact row the tagline's style when the corpus allowed it."""
    import pdfplumber
    words = [
        {"text": "J. Doe", "top": 19.1, "bottom": 48.85, "x0": 261.5, "x1": 332.4},
        {"text": "Senior Person", "top": 56.0, "bottom": 73.25, "x0": 250.0, "x1": 344.0},
        {"text": "(cid:131)example@example.com—github.com/USER", "top": 86.1, "bottom": 97.0, "x0": 68.1, "x1": 523.8},
        {"text": "EXPERIENCE", "top": 120.0, "bottom": 134.0, "x0": 36.0, "x1": 106.0},
    ]
    chars = []
    for word, size, fontname in zip(words, (24.787, 14.346, 8.966, 11.955), ("CharterBT-Bold", "CharterBT-Roman", "CharterBT-Roman", "CharterBT-Bold")):
        step = (word["x1"] - word["x0"]) / len(word["text"])
        for offset, character in enumerate(word["text"]):
            chars.append({
                "text": character, "top": word["top"], "bottom": word["bottom"],
                "x0": word["x0"] + offset * step, "x1": word["x0"] + (offset + 1) * step,
                "size": size, "fontname": fontname,
            })
    summary = {
        "pages": [{"width_pt": 612.0, "height_pt": 792.0}],
        "margins_pt": {"default": {"left": 36.0, "right": 36.0}},
        "style_groups": {
            "style_1": {"font_family": "Charter BT", "font_size_pt": 24.787, "line_height_pt": 29.75, "bold": True},
            "style_2": {"font_family": "Charter BT", "font_size_pt": 14.346, "line_height_pt": 17.25, "bold": False},
            "style_3": {"font_family": "Charter BT", "font_size_pt": 8.966, "line_height_pt": 10.75, "bold": False},
        },
        "elements": [
            {"id": "adobe.1", "page": 1, "section_path": "//Document/Sect[1]/Sect/Header",
             "text_sample": "J. Doe", "bbox_pt": {"x0": 261.5, "top": 19.1, "x1": 332.4, "bottom": 48.85}},
            {"id": "adobe.2", "page": 1, "style_id": "style_h", "structural_role": "heading_candidate",
             "text_sample": "EXPERIENCE", "entry_path": None, "bbox_pt": {"x0": 36.0, "top": 120.0, "x1": 106.0, "bottom": 134.0}},
        ],
    }
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([_FakePage(words, chars=chars)]))

    scaffold = derive_header_scaffold(Path("target.pdf"), summary)
    by_role = {row.role: row for row in scaffold}
    assert by_role["name"].font_size_pt == 24.787 and by_role["name"].bold
    assert by_role["contact"].font_size_pt == 8.966  # the row's own style, not the tagline's 14.346
    assert not by_role["contact"].bold


def test_contact_row_is_measured_structurally_not_by_at_sign(monkeypatch) -> None:
    """E→D cold start (2026-09-11): the contact row is the header's
    bottom-most text row; the '@'-in-text heuristic fails when the target's
    own email is redacted (D)."""
    import pdfplumber
    words = [
        {"text": "previous", "top": 555.70, "bottom": 566.61, "x0": 60.0, "x1": 100.0},
        {"text": "(cid:131)phone—github.com/USER", "top": 43.6, "bottom": 52.0, "x0": 68.1, "x1": 523.8},
        {"text": "Skills", "top": 584.98, "bottom": 599.33, "x0": 36.0, "x1": 68.52},
        {"text": "Experience", "top": 98.99, "bottom": 113.34, "x0": 36.0, "x1": 106.74},
        {"text": "Microsoft", "top": 123.68, "bottom": 134.59, "x0": 46.909, "x1": 96.98},
    ]
    summary = {
        "pages": [{"width_pt": 612.0, "height_pt": 792.0}],
        "margins_pt": {"default": {"left": 36.0, "right": 36.0}},
        "style_groups": {"style_h": {"font_family": "Lato", "font_size_pt": 14.346, "line_height_pt": 17.25, "bold": True}},
        "elements": [
            {"id": "adobe.1", "page": 1, "style_id": "style_h", "structural_role": "heading_candidate",
             "text_sample": "Experience", "entry_path": None, "bbox_pt": {"x0": 36.0, "top": 98.99, "x1": 106.74, "bottom": 119.1}},
            {"id": "adobe.2", "page": 1, "style_id": "style_h", "structural_role": "heading_candidate",
             "text_sample": "Skills", "entry_path": None, "bbox_pt": {"x0": 36.0, "top": 565.4, "x1": 68.52, "bottom": 605.1}},
            {"id": "adobe.3", "page": 1, "style_id": "style_b", "structural_role": "body",
             "text_sample": "Microsoft", "entry_path": "//Document/Sect[2]/Sect",
             "bbox_pt": {"x0": 46.909, "top": 123.68, "x1": 96.98, "bottom": 134.59}},
            {"id": "adobe.4", "page": 1, "text_sample": "—", "bbox_pt": {"x0": 160.0, "top": 45.0, "x1": 172.0, "bottom": 50.0}},
            {"id": "adobe.5", "page": 1, "text_sample": "—", "bbox_pt": {"x0": 300.0, "top": 45.0, "x1": 312.0, "bottom": 50.0}},
        ],
        "rules": [
            {"page_number": 1, "bbox": {"top": 92.899 / 792}, "stroke_width_pt": 0.398,
             "gap_above_pt": 11.118, "gap_below_pt": 6.092, "color_hex": "#000000"},
        ],
    }
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([_FakePage(words)]))

    scaffold = derive_body_scaffold(Path("target.pdf"), summary)
    assert scaffold.contact_icons_present  # (cid:) on the header's bottom row
    assert scaffold.contact_separator == " — "  # repeated ≥2 between values






def test_header_tagline_row_stays_header_and_roles_follow_content(monkeypatch) -> None:
    """E→D cold start (2026-09-11): D's tagline row sits BELOW the contact
    row; the header block is bounded by the first body-margin-aligned
    heading, the contact row is found by content (not position), and the
    tagline line becomes a measured tagline row (owner option B, 2026-09-11:
    the header topology is name/contact/tagline; the pre-ruling
    location-role assertion is superseded by the owner ruling)."""
    import pdfplumber
    words = [
        {"text": "J. Doe", "top": 19.1, "bottom": 48.85, "x0": 261.5, "x1": 332.4},
        {"text": "(cid:131)phone—github.com/USER", "top": 46.1, "bottom": 55.0, "x0": 68.1, "x1": 523.8},
        {"text": "Senior Person", "top": 65.0, "bottom": 82.25, "x0": 225.4, "x1": 368.4},
        {"text": "HIGHLIGHTS", "top": 127.9, "bottom": 141.9, "x0": 21.6, "x1": 94.8},
    ]
    chars = []
    for word, size, fontname in zip(words, (24.787, 8.966, 14.346, 11.955), ("CharterBT-Bold", "CharterBT-Roman", "CharterBT-Roman", "CharterBT-Bold")):
        step = (word["x1"] - word["x0"]) / len(word["text"])
        for offset, character in enumerate(word["text"]):
            chars.append({
                "text": character, "top": word["top"], "bottom": word["bottom"],
                "x0": word["x0"] + offset * step, "x1": word["x0"] + (offset + 1) * step,
                "size": size, "fontname": fontname,
            })
    summary = {
        "pages": [{"width_pt": 612.0, "height_pt": 792.0}],
        "margins_pt": {"default": {"left": 21.6, "right": 21.6}},
        "style_groups": {
            "style_1": {"font_family": "Charter BT", "font_size_pt": 24.787, "line_height_pt": 29.75, "bold": True},
            "style_3": {"font_family": "Charter BT", "font_size_pt": 8.966, "line_height_pt": 10.75, "bold": False},
            "style_2": {"font_family": "Charter BT", "font_size_pt": 14.346, "line_height_pt": 17.25, "bold": False},
        },
        "elements": [
            {"id": "adobe.1", "page": 1, "section_path": "//Document/header",
             "text_sample": "J. Doe", "bbox_pt": {"x0": 261.5, "top": 19.1, "x1": 332.4, "bottom": 48.85}},
            {"id": "adobe.2", "page": 1, "style_id": "style_h", "structural_role": "heading_candidate",
             "text_sample": "HIGHLIGHTS", "entry_path": None, "bbox_pt": {"x0": 21.6, "top": 127.9, "x1": 94.8, "bottom": 141.9}},
        ],
    }
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([_FakePage(words, chars=chars)]))

    scaffold = derive_header_scaffold(Path("target.pdf"), summary)
    roles = [(row.role, row.top_pt, row.alignment) for row in scaffold]
    assert roles == [("name", 19.1, "center"), ("contact", 46.1, "center"), ("tagline", 65.0, "center")]
    assert scaffold[2].font_size_pt == 14.346  # the tagline row carries its own measured style
    assert scaffold[2].slots == ["tagline"]


def test_header_multi_item_bar_is_excluded_as_bar_section(monkeypatch) -> None:
    """Owner option B (E→D schema boundary, 2026-09-11): a header row whose
    text is ≥2 non-empty '|'-separated segments is a multi-item bar row
    (interests-bar class) — excluded from the header topology and carried as
    compiler-owned bar_section section geometry."""
    import pdfplumber
    words = [
        {"text": "J. Doe", "top": 19.1, "bottom": 48.85, "x0": 261.5, "x1": 332.4},
        {"text": "(cid:131)phone—github.com/USER", "top": 46.1, "bottom": 55.0, "x0": 68.1, "x1": 523.8},
        {"text": "Business | Hobbies | Awesomeness", "top": 86.0, "bottom": 96.0, "x0": 215.5, "x1": 378.9},
        {"text": "HIGHLIGHTS", "top": 127.9, "bottom": 141.9, "x0": 21.6, "x1": 94.8},
    ]
    chars = []
    for word, size, fontname in zip(words, (24.787, 8.966, 8.966, 11.955), ("CharterBT-Bold", "CharterBT-Roman", "CharterBT-Roman", "CharterBT-Bold")):
        step = (word["x1"] - word["x0"]) / len(word["text"])
        for offset, character in enumerate(word["text"]):
            chars.append({
                "text": character, "top": word["top"], "bottom": word["bottom"],
                "x0": word["x0"] + offset * step, "x1": word["x0"] + (offset + 1) * step,
                "size": size, "fontname": fontname,
            })
    summary = {
        "pages": [{"width_pt": 612.0, "height_pt": 792.0}],
        "margins_pt": {"default": {"left": 21.6, "right": 21.6}},
        "style_groups": {
            "style_1": {"font_family": "Charter BT", "font_size_pt": 24.787, "line_height_pt": 29.75, "bold": True},
            "style_3": {"font_family": "Charter BT", "font_size_pt": 8.966, "line_height_pt": 10.75, "bold": False},
        },
        "elements": [
            {"id": "adobe.1", "page": 1, "section_path": "//Document/header",
             "text_sample": "J. Doe", "bbox_pt": {"x0": 261.5, "top": 19.1, "x1": 332.4, "bottom": 48.85}},
            {"id": "adobe.2", "page": 1, "style_id": "style_h", "structural_role": "heading_candidate",
             "text_sample": "HIGHLIGHTS", "entry_path": None, "bbox_pt": {"x0": 21.6, "top": 127.9, "x1": 94.8, "bottom": 141.9}},
        ],
    }
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([_FakePage(words, chars=chars)]))

    scaffold = derive_header_scaffold(Path("target.pdf"), summary, required_slots=["name", "location", "phone"])
    roles = [(row.role, round(row.top_pt, 1)) for row in scaffold]
    assert roles == [("name", 19.1), ("contact", 46.1), ("bar_section", 86.0)]
    bar = scaffold[2]
    assert bar.slots == []  # bar rows carry no slot
    # source requires location and no measured location row exists → the
    # location slot lands on the contact row (presentation follows target)
    assert "location" in scaffold[1].slots






# --- surviving cross-generation contracts ------------------------------------
# These two models outlived the A/B pipelines that introduced them: FinalReview is
# the reviewer response schema C1 sends to the provider, and GateFailure is what
# run_hard_gates (frozen a_pipeline code that C1 still calls) emits. Their only
# coverage used to live in the B-loop suite, which drove a retired workflow.
