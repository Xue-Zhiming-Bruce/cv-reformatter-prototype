"""Synthetic measured scaffolds shared by layout and renderer regressions."""
from __future__ import annotations
import hashlib
import json
from app.pipeline.measurement import BodyEntryScaffold, BodyHeadingScaffold, BodyScaffold, HeaderScaffold
from app.pipeline.layout_state import C2LayoutState
from app.pipeline.layout_compiler import state_from_scaffolds

TARGET_SHA = hashlib.sha256(b"c2-0a-synthetic-target").hexdigest()


SUMMARY: dict = {
    "pages": [{"width_pt": 612.0, "height_pt": 792.0}],
    "margins_pt": {
        "default": {"left": 36.0, "top": 34.6, "right": 36.0, "bottom": 34.6},
        "provenance": {},
    },
    "style_groups": {
        "style_1": {
            "font_family": "Lato",
            "font_size_pt": 17.2,
            "line_height_pt": 20.6,
            "bold": True,
            "provenance": ["provider"],
        },
        "style_2": {
            "font_family": "Lato",
            "font_size_pt": 10.9,
            "line_height_pt": 15.0,
            "bold": False,
            "color_hex": "#111827",
            "provenance": ["provider"],
        },
    },
    # Text volume drives the accepted body-style rule: style_2 carries long
    # body text, style_1 only a short name. TARGETFACT markers stand in for
    # target-sample candidate facts.
    "elements": [
        {"style_id": "style_2", "text_sample": "TARGETFACT body line " * 10},
        {"style_id": "style_2", "text_sample": "TARGETFACT body line " * 10},
        {"style_id": "style_2", "text_sample": "TARGETFACT body line " * 10},
        {"style_id": "style_1", "text_sample": "TARGETNAME"},
    ],
    "rules": [],
    "badges": [],
    "table_count": 0,
    "figure_count": 0,
    "graphic_count": 0,
    "warnings": [],
}


HEADER = [
    HeaderScaffold(
        role="name",
        top_pt=34.6,
        x0_pt=250.0,
        x1_pt=362.0,
        evidence_ids=["e.name"],
        slots=["name"],
        alignment="center",
        font_family="Lato",
        font_size_pt=17.2,
        line_height_pt=20.6,
        bold=True,
    ),
    HeaderScaffold(
        role="location",
        top_pt=53.5,
        x0_pt=255.0,
        x1_pt=357.0,
        evidence_ids=["e.location"],
        slots=["location"],
        alignment="center",
        font_family="Lato",
        font_size_pt=10.9,
        line_height_pt=15.0,
        bold=False,
    ),
    HeaderScaffold(
        role="contact",
        top_pt=67.4,
        x0_pt=36.0,
        x1_pt=576.0,
        evidence_ids=["e.contact"],
        slots=["phone", "envelope", "github", "linkedin"],
        alignment="left",
        font_family="Lato",
        font_size_pt=10.9,
        line_height_pt=15.0,
        bold=False,
    ),
]


def _heading(label: str, top: float, evidence_id: str) -> BodyHeadingScaffold:
    return BodyHeadingScaffold(
        verbatim=label,
        page=1,
        top_pt=top,
        x0_pt=36.0,
        x1_pt=36.0 + 9 * len(label),
        font_height_pt=14.3,
        font_size_pt=14.3,
        line_height_pt=17.2,
        bold=True,
        font_family="Lato",
        rule_top_pt=top - 7.0,
        rule_gap_above_pt=6.0,
        rule_gap_below_pt=4.0,
        rule_stroke_pt=0.75,
        rule_color_hex="#111827",
        content_gap_below_pt=8.0,
        evidence_ids=[evidence_id],
    )


FULL_COVERAGE_LABELS = [
    "SUMMARY",
    "TECHNICAL SKILLS",
    "LANGUAGES",
    "WORK EXPERIENCE",
    "EDUCATION",
    "CERTIFICATIONS",
    "PROJECTS",
]


def _body(labels: list[str]) -> BodyScaffold:
    return BodyScaffold(
        headings=[
            _heading(label, 140.0 + 60.0 * index, f"e.heading.{index + 1}")
            for index, label in enumerate(labels)
        ],
        entry=BodyEntryScaffold(
            left_x0_pt=46.9,
            right_x1_pt=576.0,
            right_row_top_delta_pt=0.0,
            evidence_ids=["e.entry.1"],
        ),
        contact_icons_present=False,
        contact_separator=" | ",
    )


BODY = _body(FULL_COVERAGE_LABELS)


BULLET_TIERS = {"l1": 46.9, "bullet_dot": 60.0, "bullet_text": 64.0}


def compile_synthetic(labels: list[str] | None = None, **overrides: object) -> C2LayoutState:
    summary = json.loads(json.dumps(SUMMARY))
    summary.update(overrides)
    return state_from_scaffolds(
        TARGET_SHA,
        [row.model_copy() for row in HEADER],
        _body(labels or FULL_COVERAGE_LABELS),
        dict(BULLET_TIERS),
        summary,
    )


def _state_with_declared_subgroup_tier(labels: list[str] | None = None) -> C2LayoutState:
    state = compile_synthetic(labels)
    nodes = [
        node.model_copy(update={"subgroup_title_style_id": "style.body"})
        if node.node_id.endswith(".entry")
        else node
        for node in state.nodes
    ]
    return state.model_copy(update={"nodes": nodes})
