from __future__ import annotations

import re
from pathlib import Path

import pytest

import tests.experiments.c_pipeline as c_pipeline_module
from tests.experiments.c_pipeline import (
    BodyEntryScaffold,
    BodyGeometry,
    BodyHeadingObservation,
    BodyHeadingScaffold,
    BodyHeadingSlot,
    BodyLine,
    BodyScaffold,
    FitParameters,
    GeometryObservation,
    HeaderRow,
    HeaderScaffold,
    HeaderTopology,
    compile_header_template,
    apply_measured_alignment,
    apply_section_indents,
    c1_filler_prompt,
    c1_provenance_contract_failures,
    _pdf_font_name_matches,
    derive_body_scaffold,
    derive_header_scaffold,
    derive_body_topology_candidates,
    derive_body_tier_targets,
    fit_body_line_geometry,
    fit_geometry,
    final_reviewer_prompt,
    merge_unprovenanced_sibling_headings,
    inject_missing_source_headings,
    measure_body_lines,
    normalize_source_block_owners,
    normalize_source_section_semantics,
    relocate_misplaced_slots,
    remove_unfilled_header_slots,
    remove_unprovenanced_punctuation_nodes,
    remove_unused_placeholders,
    remove_entry_leading_dashes,
    render_body_x0_acceptance,
    restore_compiler_header_attributes,
    topology_fingerprint,
    validate_body_topology,
    validate_topology,
)
from tests.experiments.refinement import GateFailure, HardGateResult

SCAFFOLD = [
    HeaderScaffold(role="name", top_pt=34.6, x0_pt=250, x1_pt=360, evidence_ids=["e1"], slots=["name"], alignment="center"),
    HeaderScaffold(role="location", top_pt=53.5, x0_pt=255, x1_pt=355, evidence_ids=["e2"], slots=["location"], alignment="center"),
    HeaderScaffold(role="contact", top_pt=67.4, x0_pt=36, x1_pt=576, evidence_ids=["e3"], slots=["phone", "envelope", "github", "linkedin"], alignment="left"),
]
TOPOLOGY = HeaderTopology(candidate_id="one", rows=[
    HeaderRow(target_role="name", slots=["name"], alignment="center"),
    HeaderRow(target_role="location", slots=["location"], alignment="center"),
    HeaderRow(target_role="contact", slots=["phone", "envelope", "github", "linkedin"], alignment="left"),
    HeaderRow(target_role="extension", slots=["title", "tagline"], alignment="center"),
])
SUMMARY = {
    "style_groups": {
        "name": {"font_family": "Lato", "font_size_pt": 17.2, "line_height_pt": 20.6, "bold": True},
        "body": {"font_family": "Lato", "font_size_pt": 10.9, "line_height_pt": 15, "bold": False},
    }
}
BASE = "<html><head><style>body{margin:0}</style></head><body><header>[OLD]</header><main>[BODY]</main></body></html>"


def test_topology_requires_target_rows_and_candidate_slots() -> None:
    assert validate_topology(TOPOLOGY, SCAFFOLD, ["name", "title", "tagline", "github", "linkedin"]) == []
    broken = TOPOLOGY.model_copy(update={"rows": TOPOLOGY.rows[:-1]})
    failures = validate_topology(broken, SCAFFOLD, ["name", "title", "tagline"])
    assert failures == ["missing required slots: ['title', 'tagline']"]


def test_misplaced_source_only_slots_are_relocated_into_extension_row() -> None:
    stray = HeaderTopology(candidate_id="stray", rows=[
        HeaderRow(target_role="name", slots=["name", "title", "tagline"], alignment="center"),
        HeaderRow(target_role="location", slots=["location"], alignment="center"),
        HeaderRow(target_role="contact", slots=["phone", "envelope", "github", "linkedin"], alignment="left"),
        HeaderRow(target_role="extension", slots=["tagline"], alignment="left"),
    ])

    normalized, actions = relocate_misplaced_slots(stray, SCAFFOLD)

    assert [row.slots for row in normalized.rows] == [
        ["name"], ["location"], ["phone", "envelope", "github", "linkedin"], ["tagline", "title"],
    ]
    assert actions == [
        {"action": "relocate_slot", "slot": "title", "from_role": "name", "to_role": "extension"},
        {"action": "drop_duplicate_slot", "slot": "tagline", "from_role": "name", "to_role": "extension"},
    ]
    assert validate_topology(normalized, SCAFFOLD, ["name", "title", "tagline", "phone", "envelope", "github", "linkedin"]) == []


def test_relocation_without_extension_row_keeps_rejection_and_invents_no_row() -> None:
    stray = HeaderTopology(candidate_id="stray", rows=[
        HeaderRow(target_role="name", slots=["name", "title"], alignment="center"),
        HeaderRow(target_role="location", slots=["location"], alignment="center"),
        HeaderRow(target_role="contact", slots=["phone", "envelope", "github", "linkedin"], alignment="left"),
    ])

    normalized, actions = relocate_misplaced_slots(stray, SCAFFOLD)

    assert actions == []
    assert [row.slots for row in normalized.rows] == [row.slots for row in stray.rows]
    assert validate_topology(normalized, SCAFFOLD, ["name", "title"]) != []


def test_misplaced_contact_slots_are_never_relocated() -> None:
    stray = HeaderTopology(candidate_id="stray", rows=[
        HeaderRow(target_role="name", slots=["name", "github"], alignment="center"),
        HeaderRow(target_role="location", slots=["location"], alignment="center"),
        HeaderRow(target_role="contact", slots=["phone", "envelope", "github", "linkedin"], alignment="left"),
        HeaderRow(target_role="extension", slots=["title"], alignment="left"),
    ])

    normalized, actions = relocate_misplaced_slots(stray, SCAFFOLD)

    assert actions == []
    assert normalized.rows[0].slots == ["name", "github"]
    assert any("contact" in failure or "name row" in failure for failure in validate_topology(normalized, SCAFFOLD, ["name", "title"]))


def test_relocation_targets_slot_home_target_row_and_deduplicates() -> None:
    stray = HeaderTopology(candidate_id="stray", rows=[
        HeaderRow(target_role="name", slots=["name"], alignment="center"),
        HeaderRow(target_role="location", slots=["location", "name"], alignment="center"),
        HeaderRow(target_role="contact", slots=["phone", "envelope", "github", "linkedin"], alignment="left"),
    ])

    normalized, actions = relocate_misplaced_slots(stray, SCAFFOLD)

    assert [row.slots for row in normalized.rows] == [
        ["name"], ["location"], ["phone", "envelope", "github", "linkedin"],
    ]
    assert actions == [{"action": "drop_duplicate_slot", "slot": "name", "from_role": "location", "to_role": "name"}]
    assert validate_topology(normalized, SCAFFOLD, ["name"]) == []


def test_compiler_owns_header_dom_icon_contract_and_dna() -> None:
    html = compile_header_template(BASE, TOPOLOGY, SCAFFOLD, SUMMARY, FitParameters(gaps_pt=[1, 2, 3]))
    assert "[OLD]" not in html
    assert 'data-c1-compiler="header/1"' in html
    assert 'data-slot="title"' in html
    assert "fa-brands fa-github" in html
    assert "[data-slot]+[data-slot]{margin-left:6pt}" in html
    assert ".section-heading{text-transform:lowercase!important}" in html
    assert ".section-heading::first-letter{text-transform:uppercase}" in html
    assert "border-bottom" not in html  # no measured rule in this fixture
    assert topology_fingerprint(TOPOLOGY) in html


def test_compiler_uses_measured_indent_once_on_each_section_body_child() -> None:
    summary = {
        **SUMMARY,
        "margins_pt": {"default": {"left": 36}},
        "elements": [{"entry_path": "//Document/Sect/Entry", "bbox_pt": {"x0": 46.909}}],
    }

    html = compile_header_template(BASE, TOPOLOGY, SCAFFOLD, summary, FitParameters(gaps_pt=[1, 2, 3]))

    # vocabulary-free structural selector (known-debt family, 2026-09-11):
    # section containers may be <section> tags or <div class="section">.
    assert "[data-source-block^='block:']>:not(.section-heading){padding-left:var(--c1-section-indent,10.909pt)}" in html
    assert "[data-repeatable='work-entry']>.entry-wrapper" not in html
    assert "[data-repeatable='education-entry']{padding-left" not in html


def test_measured_alignment_overrides_architect_guess_for_target_rows() -> None:
    guessed = TOPOLOGY.model_copy(update={
        "rows": [row.model_copy(update={"alignment": "right"}) for row in TOPOLOGY.rows]
    })

    aligned = apply_measured_alignment(guessed, SCAFFOLD)

    assert [row.alignment for row in aligned.rows] == ["center", "center", "left", "center"]


def test_geometry_fit_converges_and_runs_calibration_probes() -> None:
    targets = {row.role: row.top_pt for row in SCAFFOLD}
    probes: list[str] = []

    def evaluate(params: FitParameters, probe: str) -> GeometryObservation:
        probes.append(probe)
        tops = {
            "name": 30 + params.offset_pt,
            "location": 48 + params.offset_pt + params.gaps_pt[0],
            "contact": 60 + params.offset_pt + params.gaps_pt[0] + params.gaps_pt[1],
        }
        return GeometryObservation(tops_pt=tops, row_count=4)

    result = fit_geometry(SCAFFOLD, 4, evaluate)

    assert result.passed
    assert result.max_delta_pt <= 1
    assert probes[-2:] == ["short", "long"]
    assert result.renders <= 12
    assert all(abs(targets[role] - result.observations[-1].tops_pt[role]) <= 1 for role in targets)


def test_geometry_fit_rejects_wrapping_probe() -> None:
    def evaluate(params: FitParameters, probe: str) -> GeometryObservation:
        return GeometryObservation(
            tops_pt={row.role: row.top_pt for row in SCAFFOLD},
            row_count=4,
            wrapped_roles=("contact",) if probe == "long" else (),
        )

    result = fit_geometry(SCAFFOLD, 4, evaluate)

    assert not result.passed
    assert "long probe failed" in result.failures


def test_geometry_fit_rejects_overlapping_role_bounds_with_coordinates() -> None:
    overlap = "row overlap: name=(250.0, 34.0, 360.0, 50.0); extension=(180.0, 37.0, 430.0, 52.0)"

    result = fit_geometry(
        SCAFFOLD,
        4,
        lambda params, probe: GeometryObservation(
            {row.role: row.top_pt for row in SCAFFOLD}, 4, overlaps=(overlap,)
        ),
    )

    assert not result.passed
    assert overlap in result.failures


TIER_TARGETS = {"l1": 46.909, "bullet_dot": 60.0, "bullet_text": 70.909}


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



def _linear_body_measure(box_loss_pt: float):
    """Simulate a renderer: section indent + list pad move lines linearly, with a box-model loss."""

    def measure(html: str, pdf: Path, **kwargs) -> BodyGeometry:
        indents = {
            block: float(value)
            for block, value in re.findall(r"data-source-block='([^']+)'\]\{--c1-section-indent:([0-9.eE+-]+)pt", html)
        }
        pads = {
            unit: float(value)
            for unit, value in re.findall(r"\[data-c1-bullet-unit='([^']+)'\]\{padding-left:([0-9.eE+-]+)pt", html)
        }
        _, anchors, _ = c_pipeline_module._prepare_body_line_fit(html)
        anchor_x0: dict[str, float] = {}
        dots = []
        for anchor in anchors:
            if anchor.tier == "metadata":
                continue
            indent = indents.get(anchor.block_id, 0.0)
            if anchor.tier == "bullet_text":
                x0 = 36 + indent + 10.909 + pads.get(anchor.unit_id or "", 0.0)
                dots.append(c_pipeline_module.BodyDot(anchor.block_id, anchor.label, anchor.unit_id or "", x0 - 10.909 - 0.34))
            else:
                x0 = 36 + indent - box_loss_pt
            anchor_x0[anchor.key] = x0
        return BodyGeometry(anchor_x0, (), tuple(dots), ())

    return measure


def test_body_line_fit_closes_both_tiers_within_budget(monkeypatch) -> None:
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0001"><h2 class="section-heading" data-source-line="L0001">ONE</h2><p data-source-line="L0002">Alpha</p></section>
      <section data-source-block="block:L0003"><h2 class="section-heading" data-source-line="L0003">TWO</h2><ul class="entry-list"><li class="entry-item" data-source-line="L0004">Beta item</li></ul></section>
    </body></html>"""
    monkeypatch.setattr(c_pipeline_module, "measure_body_lines", _linear_body_measure(1.174))

    result = fit_body_line_geometry(candidate, TIER_TARGETS, 10.909, lambda html, number: Path("probe.pdf"))

    assert result.passed
    assert result.renders <= 4
    _, anchors, _ = c_pipeline_module._prepare_body_line_fit(result.html)
    for anchor in anchors:
        if anchor.tier in TIER_TARGETS:
            assert abs(result.geometry.anchor_x0[anchor.key] - TIER_TARGETS[anchor.tier]) <= 1
    for dot in result.geometry.dots:
        assert abs(dot.x0 - TIER_TARGETS["bullet_dot"]) <= 1


def test_body_line_fit_reports_dom_when_padding_cannot_move_x0(monkeypatch) -> None:
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0001"><h2 class="section-heading" data-source-line="L0001">ONE</h2><p data-source-line="L0002">Alpha</p></section>
    </body></html>"""

    def measure(html: str, pdf: Path, **kwargs) -> BodyGeometry:
        del html  # knobs never move the line
        return BodyGeometry({"a0": 36.0}, (), (), ())

    monkeypatch.setattr(c_pipeline_module, "measure_body_lines", measure)

    result = fit_body_line_geometry(candidate, TIER_TARGETS, 10.909, lambda html, number: Path("probe.pdf"))

    assert not result.passed
    assert any("padding adjustment did not move line x0" in failure and "<section" in failure for failure in result.failures)


def test_body_line_fit_freezes_when_render_budget_is_exhausted(monkeypatch) -> None:
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0001"><h2 class="section-heading" data-source-line="L0001">ONE</h2><p data-source-line="L0002">Alpha</p></section>
    </body></html>"""
    calls = {"count": 0}

    def measure(html: str, pdf: Path, **kwargs) -> BodyGeometry:
        calls["count"] += 1
        # creeps toward the target but never reaches it within budget
        return BodyGeometry({"a0": 46.909 - 3 + 0.4 * calls["count"]}, (), (), ())

    monkeypatch.setattr(c_pipeline_module, "measure_body_lines", measure)

    result = fit_body_line_geometry(candidate, TIER_TARGETS, 10.909, lambda html, number: Path("probe.pdf"), render_budget=4)

    assert not result.passed
    assert result.renders == 4
    assert any("exhausted 4 renders" in failure for failure in result.failures)


def test_body_line_measurement_reports_real_first_word_x0(monkeypatch) -> None:
    r"""Regression: the measured x0 is the line's first word, never a mid-line token.

    Owner-confirmed root cause: a leading separator glyph made the old matcher
    report the token AFTER the dash (46.92) while the real line sat at 38.61.
    """
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0001"><h2 class="section-heading" data-source-line="L0001">ONE</h2><h3 class="entry-company" data-source-line="L0002">\u2013 More text here</h3></section>
    </body></html>"""

    words = [
        {"text": "ONE", "top": 40.0, "bottom": 50.0, "x0": 36.0, "x1": 60.0},
        {"text": "\u2013", "top": 50.0, "bottom": 60.0, "x0": 38.61, "x1": 44.0},
        {"text": "More", "top": 50.0, "bottom": 60.0, "x0": 46.92, "x1": 70.0},
        {"text": "text", "top": 50.0, "bottom": 60.0, "x0": 72.0, "x1": 90.0},
        {"text": "here", "top": 50.0, "bottom": 60.0, "x0": 92.0, "x1": 110.0},
    ]

    class FakePage:
        def extract_words(self):
            return words

        chars = _chars_of(words)
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

    geometry = measure_body_lines(candidate, "ignored.pdf")

    assert geometry.failures == ()
    assert all(abs(value - 38.61) <= 0.01 for value in geometry.anchor_x0.values())


def test_body_line_measurement_measures_bullet_text_and_dot_by_tier(monkeypatch) -> None:
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0001"><h2 class="section-heading" data-source-line="L0001">ONE</h2>
        <h3 class="entry-company entry-item" data-source-line="L0002">Bullet item text</h3>
        <p class="body-text" data-source-line="L0003">Plain body line</p>
      </section>
    </body></html>"""

    words = [
        {"text": "ONE", "top": 40.0, "bottom": 50.0, "x0": 36.0, "x1": 60.0},
        {"text": "Bullet", "top": 50.0, "bottom": 65.0, "x0": 70.909, "x1": 100.0},
        {"text": "item", "top": 50.0, "bottom": 65.0, "x0": 102.0, "x1": 120.0},
        {"text": "text", "top": 50.0, "bottom": 65.0, "x0": 122.0, "x1": 140.0},
        {"text": "Plain", "top": 70.0, "bottom": 85.0, "x0": 46.909, "x1": 80.0},
        {"text": "body", "top": 70.0, "bottom": 85.0, "x0": 82.0, "x1": 110.0},
        {"text": "line", "top": 70.0, "bottom": 85.0, "x0": 112.0, "x1": 130.0},
    ]

    class FakePage:
        def extract_words(self):
            return words

        chars = _chars_of(words)
        curves = [
            {"page": 1, "top": 54.0, "bottom": 59.0, "x0": 60.0, "x1": 65.0, "non_stroking_color": (0.0, 0.0, 0.0)},
        ]
        rects = []

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda path: FakePdf())

    geometry = measure_body_lines(candidate, "ignored.pdf")

    assert geometry.failures == ()
    _, anchors, _ = c_pipeline_module._prepare_body_line_fit(candidate)
    by_tier: dict[str, list[float]] = {}
    for anchor in anchors:
        by_tier.setdefault(anchor.tier, []).append(geometry.anchor_x0[anchor.key])
    assert all(abs(value - 70.909) <= 0.01 for value in by_tier["bullet_text"])
    assert all(abs(value - 46.909) <= 0.01 for value in by_tier["l1"])
    assert [dot.x0 for dot in geometry.dots] == [60.0]


def test_body_line_measurement_survives_non_word_extractable_glyphs(monkeypatch) -> None:
    """A glyph-prefixed line whose glyph is missing from extract_words still
    measures from the glyph: the real line x0 (70.909), not the first
    extractable word (82.448)."""
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0001"><h2 class="section-heading" data-source-line="L0001">ONE</h2>
        <h3 class="entry-company entry-item" data-source-line="L0002">\u2323 Sales: Implemented Sales Strategies</h3>
      </section>
    </body></html>"""
    words = [
        {"text": "ONE", "top": 40.0, "bottom": 50.0, "x0": 36.0, "x1": 60.0},
        {"text": "Sales:", "top": 50.0, "bottom": 65.0, "x0": 82.448, "x1": 110.0},
        {"text": "Implemented", "top": 50.0, "bottom": 65.0, "x0": 112.0, "x1": 160.0},
        {"text": "Sales", "top": 50.0, "bottom": 65.0, "x0": 162.0, "x1": 190.0},
        {"text": "Strategies", "top": 50.0, "bottom": 65.0, "x0": 192.0, "x1": 240.0},
    ]
    glyph_chars = [{"text": "\u2323", "top": 50.0, "bottom": 65.0, "x0": 70.909, "x1": 76.0}, *_chars_of(words)]

    class FakePage:
        def extract_words(self):
            return words

        chars = glyph_chars
        rects = []
        curves = [
            {"page": 1, "top": 54.0, "bottom": 59.0, "x0": 60.0, "x1": 65.0, "non_stroking_color": (0.0, 0.0, 0.0)},
        ]

    class FakePdf:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda path: FakePdf())

    geometry = measure_body_lines(candidate, "ignored.pdf")

    assert geometry.failures == ()
    assert all(abs(value - 70.909) <= 0.01 for value in geometry.anchor_x0.values())
    assert [dot.x0 for dot in geometry.dots] == [60.0]


def test_grid_column_anchors_are_frozen_not_fitted() -> None:
    candidate = """<html><head><style>.skills-grid{display: grid;grid-template-columns: repeat(auto-fill, minmax(120pt, 1fr));}</style></head><body>
      <section data-source-block="block:L0001"><h2 class="section-heading" data-source-line="L0001">ONE</h2>
        <div class="skills-grid"><h3 class="skill-heading" data-source-line="L0002">Business</h3></div>
        <p data-source-line="L0003">Plain body line</p>
      </section>
    </body></html>"""

    _, anchors, _ = c_pipeline_module._prepare_body_line_fit(candidate)

    tiers = {anchor.text: anchor.tier for anchor in anchors}
    assert tiers["Business"] == "frozen_cell"
    assert tiers["Plain body line"] == "l1"


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


def test_acceptance_table_groups_all_body_lines_by_tier() -> None:
    geometry = BodyGeometry(
        {},
        (
            BodyLine(1, 50.0, 46.910, "Plain body", "b1", "ONE", "l1"),
            BodyLine(1, 70.0, 70.910, "Bullet text", "b1", "ONE", "bullet_text"),
            BodyLine(1, 90.0, 507.363, "2024 \u2013 Present", "b1", "ONE", "metadata"),
            BodyLine(1, 110.0, 250.805, "Grid cell", "b1", "ONE", "frozen_cell"),
        ),
        (c_pipeline_module.BodyDot("b1", "ONE", "1", 60.25),),
        (),
    )

    table = render_body_x0_acceptance(geometry, TIER_TARGETS)

    assert "Level-1 body text (target 46.909 pt)" in table
    assert "Bullet dots (target 60.000 pt)" in table
    assert "Level-2 body / bullet text / wrapped lines (target 70.909 pt)" in table
    assert "| ONE | Plain body | 46.910 | 0.001 | PASS |" in table
    assert "| ONE | Bullet text | 70.910 | 0.001 | PASS |" in table
    assert "| ONE | ::before marker | 60.250 | 0.250 | PASS |" in table
    assert "507.363" in table and "metadata" in table
    assert "Grid-column lines" in table and "250.805" in table


def test_c1_prompt_requires_semantic_and_provenance_self_check() -> None:
    prompt = c1_filler_prompt("base")

    assert "data-source-block=\"document\"" in prompt
    assert "never split one source block" in prompt
    assert "Every visible text node is carried by an element with data-source-line, or by a container that inherited one source line" in prompt
    assert "may span several elements carrying the same data-source-line" in prompt
    assert "Same-line fragments must appear in the source line's reading order" in prompt
    assert "At most one bounded retry" in prompt
    assert "complete deterministic gate failures" in prompt


def test_c1_provenance_contract_allows_ordered_annotated_line_splits() -> None:
    source = "Alpha Beta"
    valid = '<html><body><span data-source-line="L0001">Alpha</span><span data-source-line="L0001">Beta</span></body></html>'
    reversed_fragments = '<html><body><span data-source-line="L0001">Beta</span><span data-source-line="L0001">Alpha</span></body></html>'
    unprovenanced = '<html><body><span data-source-line="L0001">Alpha</span><span>Beta</span></body></html>'

    assert c1_provenance_contract_failures(valid, source) == []
    assert any("source_reading_order" in failure for failure in c1_provenance_contract_failures(reversed_fragments, source))
    assert any("unprovenanced_text" in failure for failure in c1_provenance_contract_failures(unprovenanced, source))


def test_source_block_owner_is_derived_for_unowned_fragment() -> None:
    candidate = '<html><body><section><p data-source-line="L0002">Profile text</p></section></body></html>'

    normalized, actions = normalize_source_block_owners(candidate, "SUMMARY\nProfile text")

    assert 'data-source-block="block:L0001"' in normalized
    assert 'data-filler-source-block="missing"' in normalized
    assert actions == [{"action": "set_source_block", "source_line_id": "L0002", "from": "missing", "to": "block:L0001"}]


def test_missing_source_heading_is_injected_and_logged() -> None:
    candidate = '<html><body><section data-section="summary" data-source-block="block:L0001" data-source-heading="L0001"><p data-source-line="L0002">Profile text</p></section></body></html>'

    normalized, actions = inject_missing_source_headings(candidate, BASE, "SUMMARY\nProfile text")

    assert '<h2 data-source-line="L0001" data-slot="heading">SUMMARY</h2>' in normalized
    assert actions == [{"action": "inject_source_heading", "source_line_id": "L0001", "text": "SUMMARY"}]


def test_source_heading_deterministically_overrides_filler_section_semantic() -> None:
    candidate = '<html><body><section data-section="experience" data-source-block="block:L0002" data-source-heading="L0002"><h2 data-source-line="L0002">SUMMARY</h2></section></body></html>'

    normalized = normalize_source_section_semantics(candidate, "J. Doe\nSUMMARY\nProfile text")

    assert 'data-section="summary"' in normalized
    assert 'data-filler-section="experience"' in normalized


def test_unprovenanced_sibling_heading_is_merged_into_its_source_line_leaf() -> None:
    source = "SKILLS POOL\nManagement People, Systems, Operations, Projects"
    candidate = '<html><body><section><div><h3>Management</h3><p data-source-line="L0002">People, Systems, Operations, Projects</p></div></section></body></html>'

    normalized = merge_unprovenanced_sibling_headings(candidate, source)

    assert "<h3>Management</h3>" not in normalized
    assert '<p data-source-line="L0002">Management People, Systems, Operations, Projects</p>' in normalized
    assert normalized.count('data-source-line="L0002"') == 1


def test_unprovenanced_punctuation_leaf_is_removed_and_word_bearing_text_is_not() -> None:
    candidate = (
        '<html><body>'
        '<div class="summary-line"><span class="label" data-source-line="L0001">SUMMARY</span>'
        '<span class="dash">—</span><span data-source-line="L0002">Body text</span></div>'
        '<div><span class="dash">—</span><span>Invented words</span></div>'
        '</body></html>'
    )

    normalized, actions = remove_unprovenanced_punctuation_nodes(candidate)

    assert '<span class="dash">—</span>' not in normalized
    assert '<span class="label" data-source-line="L0001">SUMMARY</span>' in normalized
    assert "Invented words" in normalized
    assert actions == [
        {"action": "remove_unprovenanced_punctuation", "node": "/html/body/div[1]/span[2]", "text": "—"},
        {"action": "remove_unprovenanced_punctuation", "node": "/html/body/div[2]/span[1]", "text": "—"},
    ]
    normalized_again, actions_again = remove_unprovenanced_punctuation_nodes(normalized)
    assert normalized_again == normalized
    assert actions_again == []


def test_punctuation_tail_inside_annotated_container_is_untouched() -> None:
    candidate = '<html><body><p data-source-line="L0001">Line text<span> —</span></p></body></html>'

    normalized, actions = remove_unprovenanced_punctuation_nodes(candidate)

    assert normalized == candidate
    assert actions == []

def test_final_reviewer_prompt_states_source_absent_fields_and_heading_scope() -> None:
    prompt = final_reviewer_prompt("""J. Doe
Senior Business Person
Business | Hobbies | Awesomeness
github.com/USER
linkedin.com/in/USER
""")

    assert "do not exist in SOURCE: location, phone, email" in prompt
    assert "must not appear in diagnosis" in prompt
    assert "Section headings are verbatim SOURCE content" in prompt
    assert "wording, naming, and capitalization are not defects" in prompt


def test_optional_target_slots_are_removed_instead_of_invented() -> None:
    candidate = """<html><body><header class="c1-header">
      <div data-c1-role="location"><span data-slot="location">No location provided</span></div>
      <div data-c1-role="contact">
        <span class="contact-item"><span data-slot="phone">No phone</span></span>
        <span class="contact-item"><span data-slot="github">github.com/USER</span></span>
      </div></header></body></html>"""

    cleaned = remove_unfilled_header_slots(candidate, ["github"])

    assert "No location" not in cleaned
    assert "No phone" not in cleaned
    assert 'data-c1-role="location"' in cleaned
    assert 'data-slot="location"' not in cleaned
    assert "github.com/USER" in cleaned


def test_unused_generic_placeholder_leaves_are_removed() -> None:
    candidate = """<html><body><div>
      <li>[SOURCE TEXT – ACHIEVEMENT OR RESPONSIBILITY]</li>
      <span>[START DATE] – [END DATE]</span>
      <p data-source-line="L1">[AUTHORIZED SOURCE VALUE]</p>
    </div></body></html>"""

    cleaned = remove_unused_placeholders(candidate)

    assert "SOURCE TEXT" not in cleaned
    assert "START DATE" not in cleaned
    assert "AUTHORIZED SOURCE VALUE" in cleaned


REFINED_TEMPLATE = """<html><head><style>.entry-item::before{content:""}</style></head><body>
  <section class="additional-section"><ul class="entry-list"><li>[X]</li></ul></section>
  <section class="education-section">[EDU]</section>
</body></html>"""


def test_entry_separator_dashes_are_removed_without_removing_text() -> None:
    candidate = """<html><body>
      <section class="additional-section">
        <p class="entry-role" data-source-line="L1">– Senior Engineer</p>
        <p class="entry-program" data-source-line="L2">- Bachelor of Science</p>
        <h2 class="section-heading" data-source-line="L3">SUMMARY —</h2>
        <li class="entry-item" data-source-line="L4">– Managed delivery</li>
        <p class="body-text" data-source-line="L5">– Keep body bullet</p>
        <h3 class="entry-company" data-source-line="L6">Business Mentors – Mentor</h3>
        <h3 class="entry-company" data-source-line="L7">– More text here</h3>
      </section>
      <section class="education-section">
        <p class="body-text" data-source-line="L8">– Education note without target list slot</p>
      </section>
    </body></html>"""

    cleaned = remove_entry_leading_dashes(candidate, REFINED_TEMPLATE)

    assert ">Senior Engineer<" in cleaned
    assert ">Bachelor of Science<" in cleaned
    assert ">SUMMARY<" in cleaned
    assert ">Managed delivery<" in cleaned
    # list-slot section: leading list-marker dash becomes a CSS bullet, never a literal character
    assert ">Keep body bullet<" in cleaned
    assert 'class="body-text entry-item" data-source-line="L5"' in cleaned
    assert 'class="entry-company" data-source-line="L6"' in cleaned
    assert ">Business Mentors – Mentor<" in cleaned
    assert ">More text here<" in cleaned
    assert 'class="entry-company entry-item" data-source-line="L7"' in cleaned
    # no target list slot: the dash disappears, presentation follows the target, no bullet marker
    assert ">Education note without target list slot<" in cleaned
    assert 'class="body-text" data-source-line="L8"' in cleaned


def test_leading_bullet_glyph_converts_when_target_declares_bullet_design() -> None:
    """F→E cold start (2026-09-11): the CSS marker supplies the presentation
    when the target has a measured bullet design; a literal leading glyph
    must not survive (dash-rule predicate, structure-determined)."""
    candidate = """<html><body>
      <section class="experience-section">
        <ul class="entry-list">
          <li class="entry-item" data-source-line="L1">• Conducted research on robotics</li>
          <li class="entry-item" data-source-line="L2">◦ Nested glyph style</li>
        </ul>
      </section>
    </body></html>"""

    converted = remove_entry_leading_dashes(candidate, REFINED_TEMPLATE, convert_bullets=True)
    assert ">Conducted research on robotics<" in converted
    assert ">Nested glyph style<" in converted
    assert "•" not in converted
    # zero-bullet target (F ruling): the verbatim glyph IS the marker
    retained = remove_entry_leading_dashes(candidate, REFINED_TEMPLATE, convert_bullets=False)
    assert "• Conducted research on robotics" in retained
    assert "◦ Nested glyph style" in retained
    # mid-line glyphs and non-line-start tails are untouched
    inline = """<html><body>
      <section class="experience-section">
        <p data-source-line="L1">A • B</p>
        <ul><li data-source-line="L2"><strong>Role</strong> • trailing glyph</li></ul>
      </section>
    </body></html>"""
    untouched = remove_entry_leading_dashes(inline, REFINED_TEMPLATE, convert_bullets=True)
    assert "A • B" in untouched
    assert "<strong>Role</strong> • trailing glyph" in untouched


def test_compiler_owned_header_alignment_is_restored_after_fill() -> None:
    template = compile_header_template(BASE, TOPOLOGY, SCAFFOLD, SUMMARY, FitParameters(gaps_pt=[1, 2, 3]))
    candidate = template.replace('data-align="center"', 'data-align="left"')

    restored = restore_compiler_header_attributes(template, candidate)

    assert restored.count('data-align="center"') == 3
    assert restored.count('data-align="left"') == 1


# --- C1 body rollout: section headings (Phase A) + entry two-column rows (Phase B) ---

BODY_SCAFFOLD = BodyScaffold(
    headings=[
        BodyHeadingScaffold(
            verbatim="Experience", page=1, top_pt=98.99, x0_pt=36.0, x1_pt=106.74,
            font_height_pt=14.35, font_size_pt=14.346, line_height_pt=17.25, bold=True,
            font_family="Lato", rule_top_pt=92.899, rule_gap_above_pt=11.118,
            rule_gap_below_pt=6.092, rule_stroke_pt=0.398, rule_color_hex="#000000",
            content_gap_below_pt=10.34, evidence_ids=["adobe.1"],
        ),
        BodyHeadingScaffold(
            verbatim="Skills", page=1, top_pt=584.98, x0_pt=36.0, x1_pt=68.52,
            font_height_pt=14.35, font_size_pt=14.346, line_height_pt=17.25, bold=True,
            font_family="Lato", rule_top_pt=578.885, rule_gap_above_pt=12.2805,
            rule_gap_below_pt=6.0933, rule_stroke_pt=0.398, rule_color_hex="#000000",
            content_gap_below_pt=10.35, evidence_ids=["adobe.2"],
        ),
    ],
    entry=BodyEntryScaffold(left_x0_pt=46.909, right_x1_pt=576.0, right_row_top_delta_pt=0.0, evidence_ids=["adobe.3"]),
)
HEADING_TARGETS = {
    "x0_pt": 36.0,
    "font_height_pt": 14.35,
    "rule_gap_below_pt": 6.092,
    "content_gap_below_pt": 10.34,
    "header_rule_gap_above_pt": 11.118,
    "section_rule_gap_above_pt": 12.2805,
}


class _FakePage:
    def __init__(self, words, chars=None, rects=(), curves=(), lines=(), width=612.0):
        self._words = words
        self.chars = chars if chars is not None else _chars_of(words)
        self.rects = list(rects)
        self.curves = list(curves)
        self.lines = list(lines)
        self.width = width

    def extract_words(self):
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


def test_body_topology_candidates_validate_against_measurement() -> None:
    candidates = derive_body_topology_candidates(BODY_SCAFFOLD, 36.0, 612.0)

    assert candidates.candidates[0].headings == [
        BodyHeadingSlot(order=0, alignment="left", rule=True),
        BodyHeadingSlot(order=1, alignment="left", rule=True),
    ]
    assert validate_body_topology(candidates.candidates[0], BODY_SCAFFOLD, 36.0, 612.0) == []

    drifted = candidates.candidates[0].model_copy(update={
        "headings": [BodyHeadingSlot(order=1, alignment="center", rule=False), BodyHeadingSlot(order=1, alignment="left", rule=True)]
    })
    failures = validate_body_topology(drifted, BODY_SCAFFOLD, 36.0, 612.0)
    assert any("order" in failure for failure in failures)
    assert any("alignment" in failure for failure in failures)
    assert any("rule presence" in failure for failure in failures)
    empty = candidates.candidates[0].model_copy(update={"headings": []})
    assert validate_body_topology(empty, BODY_SCAFFOLD, 36.0, 612.0) == [
        "heading slot count 0 must equal 2 measured target headings"
    ]


def test_compiler_emits_measured_heading_css_and_entry_offset() -> None:
    html = compile_header_template(
        BASE, TOPOLOGY, SCAFFOLD, SUMMARY, FitParameters(gaps_pt=[1, 2, 3]),
        body_scaffold=BODY_SCAFFOLD,
    )

    assert 'data-c1-compiler="body/1"' in html
    assert ".section-heading{font-family:Lato,Arial,sans-serif;font-size:14.346pt;line-height:17.250pt;font-weight:700;" in html
    # rule -> heading gap rides padding-top so sibling margins cannot collapse it
    assert "padding-top:calc(6.092pt + var(--c1-heading-rule-gap,0pt))" in html
    assert "margin-bottom:calc(10.340pt + var(--c1-heading-content-gap,0pt))" in html
    assert "padding-bottom:calc(11.118pt + var(--c1-rule-gap-above,0pt))" in html
    assert "border-bottom:0.398pt solid #000000" in html
    assert f"padding-bottom:calc({BODY_SCAFFOLD.headings[1].rule_gap_above_pt:.3f}pt + var(--c1-section-rule-gap,0pt))" in html
    assert "[data-source-block^='block:']:last-of-type{border-bottom:none;padding-bottom:0}" in html
    # owner ruling 2026-09-10: block-stacked right column, defensive min-width,
    # and headings wrap instead of defining the document min-content
    assert ".entry-right{min-width:0;flex-shrink:0;margin-right:calc(0pt - var(--c1-entry-right-offset,0pt))}" in html
    assert ".entry-right>*{display:block}" in html
    assert ".section-heading{white-space:normal}" in html
    # dates are never wrapped or truncated: nowrap stays template-owned
    entry_right_rule = re.search(r'\.entry-right\{([^}]*)\}', html).group(1)
    assert "white-space" not in entry_right_rule
    # no body scaffold -> no body compile tag (frozen header behavior unchanged)
    plain = compile_header_template(BASE, TOPOLOGY, SCAFFOLD, SUMMARY, FitParameters(gaps_pt=[1, 2, 3]))
    assert 'data-c1-compiler="body/1"' not in plain


MEASURE_CANDIDATE = """<html><head></head><body>
  <section data-source-block="block:L0001"><h2 class="section-heading" data-source-line="L0001">ONE</h2><p data-source-line="L0002">Alpha text</p><span class="entry-date" data-source-line="L0003">2024 \u2013 Present</span></section>
</body></html>"""


def test_measure_body_lines_reports_heading_geometry_and_entry_right_edge(monkeypatch) -> None:
    words = [
        {"text": "Header", "top": 67.0, "bottom": 78.0, "x0": 36.0, "x1": 70.0},
        {"text": "ONE", "top": 100.0, "bottom": 114.35, "x0": 36.0, "x1": 80.0},
        {"text": "Alpha", "top": 124.68, "bottom": 135.59, "x0": 46.909, "x1": 80.0},
        {"text": "text", "top": 124.68, "bottom": 135.59, "x0": 82.0, "x1": 110.0},
        {"text": "2024", "top": 140.0, "bottom": 150.91, "x0": 515.59, "x1": 540.0},
        {"text": "\u2013", "top": 140.0, "bottom": 150.91, "x0": 542.0, "x1": 550.0},
        {"text": "Present", "top": 140.0, "bottom": 150.91, "x0": 552.0, "x1": 575.99},
    ]
    rects = [{"top": 95.25, "bottom": 96.0, "x0": 36.0, "x1": 576.0}]

    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([_FakePage(words, rects=rects)]))

    geometry = measure_body_lines(MEASURE_CANDIDATE, "ignored.pdf")

    assert geometry.failures == ()
    assert len(geometry.headings) == 1
    heading = geometry.headings[0]
    assert heading.label == "ONE"
    assert heading.x0_pt == 36.0
    assert heading.font_height_pt == pytest.approx(14.35)
    assert heading.rule_gap_below_pt == 100.0 - 95.25
    assert heading.rule_gap_above_pt == 95.25 - 78.0
    assert heading.content_gap_below_pt == pytest.approx(10.33)
    metadata = [line for line in geometry.lines if line.tier == "metadata"]
    assert [line.x1 for line in metadata] == [575.99]


def _fit_knobs(html: str) -> dict[str, float]:
    style = re.search(r"<style[^>]*data-c1-section-fit[^>]*>(.*?)</style>", html, re.S).group(1)

    def var(name: str) -> float:
        match = re.search(rf"--c1-{name}:(-?[0-9.]+)pt", style)
        return float(match.group(1)) if match else 0.0

    return {
        "rule_gap_above": var("rule-gap-above"),
        "heading_rule_gap": var("heading-rule-gap"),
        "heading_content_gap": var("heading-content-gap"),
        "entry_right": var("entry-right-offset"),
    }


def test_body_fit_closes_heading_gaps_and_entry_edge_within_budget(monkeypatch) -> None:
    def measure(html: str, pdf: Path, **kwargs) -> BodyGeometry:
        del pdf
        knobs = _fit_knobs(html)
        # renderer residual of +3pt on every measured heading gap; knobs respond linearly
        return BodyGeometry(
            {"a0": 46.909},
            (BodyLine(1, 140.0, 515.59, "2024 \u2013 Present", "block:L0001", "ONE", "metadata",
                      574.51 + knobs["entry_right"]),),
            (), (),
            headings=(BodyHeadingObservation(
                "ONE", 100.0, 36.0, 14.35,
                rule_gap_above_pt=HEADING_TARGETS["header_rule_gap_above_pt"] + 3.0 + knobs["rule_gap_above"],
                rule_gap_below_pt=HEADING_TARGETS["rule_gap_below_pt"] + 3.0 + knobs["heading_rule_gap"],
                content_gap_below_pt=HEADING_TARGETS["content_gap_below_pt"] + 3.0 + knobs["heading_content_gap"],
                block_id="block:L0001", prev_block_id=None,
            ),),
        )

    monkeypatch.setattr(c_pipeline_module, "measure_body_lines", measure)

    result = fit_body_line_geometry(
        MEASURE_CANDIDATE, TIER_TARGETS, 10.909, lambda html, number: Path("probe.pdf"),
        heading_targets=HEADING_TARGETS, entry_right_x1_pt=576.01,
    )

    assert result.passed
    assert result.renders <= 4
    for key, expected in (
        ("heading-rule-gap:block:L0001", -3.0),
        ("heading-content-gap:block:L0001", -3.0),
        ("entry_right", 1.5),
    ):
        assert abs(result.heading_knobs[key] - expected) <= 0.01


def test_heading_case_table_reports_unmatched_headings_transparently() -> None:
    # Owner ruling 2026-09-11 (E→F final rerun): a rendered heading whose
    # source wording differs from every target heading (no verbatim
    # same-name contract) must still appear in the Heading case table,
    # judged against the compile-level case decision over the target
    # heading set — never silently skipped.
    scaffold = BodyScaffold(
        headings=(
            BodyHeadingScaffold(verbatim="TECHNICAL SKILLS", page=1, top_pt=98.99, x0_pt=36.0, x1_pt=106.74,
                                font_height_pt=14.35, font_size_pt=14.346, line_height_pt=17.25, bold=True,
                                font_family="Lato",
                                rule_top_pt=92.899, rule_gap_above_pt=11.118, rule_gap_below_pt=6.092,
                                rule_stroke_pt=0.398, rule_color_hex="#000000", content_gap_below_pt=10.34,
                                evidence_ids=["adobe.1"]),
        ),
        entry=BodyEntryScaffold(left_x0_pt=46.909, right_x1_pt=576.0, right_row_top_delta_pt=None, evidence_ids=[]),
    )
    geometry = BodyGeometry(
        {}, (), (), (),
        headings=(BodyHeadingObservation("SKILLS", 98.99, 36.0, 14.35, None, None, None, rendered_text="SKILLS"),),
    )

    table = render_body_x0_acceptance(geometry, TIER_TARGETS, HEADING_TARGETS, 576.0, None, None, scaffold)

    assert "Heading case" in table
    assert "SKILLS" in table.split("## Heading case")[1]
    # compile-level decision: the all-uppercase target heading set → uppercase
    assert "target uppercase" in table.split("## Heading case")[1]
    assert "PASS" in table.split("## Heading case")[1]


def test_body_fit_freezes_when_compiled_heading_geometry_deviates(monkeypatch) -> None:
    def measure(html: str, pdf: Path, **kwargs) -> BodyGeometry:
        del html, pdf  # heading x0/font are compiled values; no knob can move them
        return BodyGeometry(
            {"a0": 46.909}, (), (), (),
            headings=(BodyHeadingObservation("ONE", 100.0, 38.0, 16.0, None, None, None),),
        )

    monkeypatch.setattr(c_pipeline_module, "measure_body_lines", measure)

    result = fit_body_line_geometry(
        MEASURE_CANDIDATE, TIER_TARGETS, 10.909, lambda html, number: Path("probe.pdf"),
        heading_targets=HEADING_TARGETS,
    )

    assert not result.passed
    assert any("x0" in failure and "compiled, not fitted" in failure for failure in result.failures)
    assert any("glyph height" in failure and "compiled, not fitted" in failure for failure in result.failures)


def test_acceptance_table_renders_heading_and_entry_tiers() -> None:
    geometry = BodyGeometry(
        {},
        (
            BodyLine(1, 140.0, 515.59, "2024 \u2013 Present", "b1", "ONE", "metadata", 576.01),
            BodyLine(1, 160.0, 515.59, "1999 \u2013 2005", "b1", "ONE", "metadata", 574.5),
        ),
        (), (),
        headings=(
            BodyHeadingObservation("ONE", 98.99, 36.0, 14.35, 11.118, 6.092, 10.34),
            BodyHeadingObservation("TWO", 300.0, 38.0, 14.35, 12.2805, 6.092, 10.34),
        ),
    )

    table = render_body_x0_acceptance(geometry, TIER_TARGETS, HEADING_TARGETS, 576.01)

    assert "Section heading rows" in table
    assert (
        "| ONE | 36.000 (\u03940.000) | 14.350 (\u03940.000) | 11.118 (\u03940.000) "
        "| 6.092 (\u03940.000) | 10.340 (\u03940.000) | 98.990 | PASS |" in table
    )
    assert "| TWO | 38.000 (\u03942.000)" in table and "FAIL" in table
    assert "Entry two-column rows" in table
    # Boundary semantics (owner ruling 2026-09-10, E→F thirteenth freeze): rows
    # within the measured content boundary PASS (right-alignment is not gated);
    # only overshoot beyond the boundary fails.
    assert "| ONE | 2024 \u2013 Present | 576.010 | 0.000 | PASS |" in table
    assert "| ONE | 1999 \u2013 2005 | 574.500 | 0.000 | PASS |" in table


def test_entry_table_fails_only_on_boundary_overshoot() -> None:
    geometry = BodyGeometry(
        {},
        (
            BodyLine(1, 140.0, 515.59, "2024 \u2013 Present", "b1", "ONE", "metadata", 578.5),
        ),
        (), (),
        headings=(),
    )
    table = render_body_x0_acceptance(geometry, TIER_TARGETS, HEADING_TARGETS, 576.01)
    assert "| ONE | 2024 \u2013 Present | 578.500 | 2.490 | FAIL |" in table


def test_right_column_dates_are_metadata_by_structure_not_vocabulary() -> None:
    """Structural metadata discovery (orchestrator ruling 2026-09-11, #4/#11
    precedent family): the date column is the .entry-right cell the compiler
    owns — F names it .date, E .entry-date; both are claimed as metadata
    (frozen presentation, out of the l1 inventory) regardless of class."""
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0007">
        <div class="entry-title-row"><span class="entry-title" data-source-line="L0010">Engineer II</span><div class="entry-right"><span class="date" data-source-line="L0011">April 2019 \u2013 Present</span></div></div>
      </section>
      <section data-source-block="block:L0020">
        <div class="entry-title-row"><span class="entry-title" data-source-line="L0021">Manager</span><div class="entry-right"><span class="entry-date" data-source-line="L0022">2017 \u2013 2024</span></div></div>
      </section>
    </body></html>"""
    _, anchors, _ = c_pipeline_module._prepare_body_line_fit(candidate)
    by_text = {anchor.text: anchor for anchor in anchors}
    assert by_text["April 2019 \u2013 Present"].tier == "metadata"  # F vocabulary (.date)
    assert by_text["2017 \u2013 2024"].tier == "metadata"  # E vocabulary (.entry-date)
    assert by_text["Engineer II"].tier == "l1" and by_text["Manager"].tier == "l1"
    prepared, _, _ = c_pipeline_module._prepare_body_line_fit(candidate)
    # metadata anchors stay out of the per-line correction knob addressing
    for text in ("April 2019 \u2013 Present", "2017 \u2013 2024"):
        key = by_text[text].key
        assert f'data-c1-line-fit="{key}"' not in prepared


def test_measure_body_lines_absorbs_wrapped_heading_continuations(monkeypatch) -> None:
    """A verbatim heading longer than the content width wraps (compiled
    white-space:normal); its continuation lines are heading presentation, not
    unattributed body lines, and the heading→content gap reaches past them."""
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0011"><h2 class="section-heading" data-source-line="L0011">SUMMARY \u2014 This is an overly-packed and busy example line</h2><p data-source-line="L0012">Alpha text</p></section>
    </body></html>"""
    words = [
        {"text": "SUMMARY", "top": 100.0, "bottom": 114.35, "x0": 36.0, "x1": 90.0},
        {"text": "\u2014", "top": 100.0, "bottom": 114.35, "x0": 92.0, "x1": 104.0},
        {"text": "This", "top": 100.0, "bottom": 114.35, "x0": 106.0, "x1": 130.0},
        {"text": "is", "top": 100.0, "bottom": 114.35, "x0": 132.0, "x1": 145.0},
        {"text": "an", "top": 100.0, "bottom": 114.35, "x0": 147.0, "x1": 162.0},
        {"text": "overly-packed", "top": 117.25, "bottom": 131.6, "x0": 36.0, "x1": 110.0},
        {"text": "and", "top": 117.25, "bottom": 131.6, "x0": 112.0, "x1": 130.0},
        {"text": "busy", "top": 117.25, "bottom": 131.6, "x0": 132.0, "x1": 155.0},
        {"text": "example", "top": 117.25, "bottom": 131.6, "x0": 157.0, "x1": 205.0},
        {"text": "line", "top": 117.25, "bottom": 131.6, "x0": 207.0, "x1": 230.0},
        {"text": "Alpha", "top": 141.94, "bottom": 152.85, "x0": 46.909, "x1": 80.0},
        {"text": "text", "top": 141.94, "bottom": 152.85, "x0": 82.0, "x1": 110.0},
    ]

    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([_FakePage(words)]))

    geometry = measure_body_lines(candidate, "ignored.pdf")

    assert geometry.failures == ()
    heading_rows = [line for line in geometry.lines if line.tier == "heading"]
    assert len(heading_rows) == 1  # the wrapped continuation line
    assert [line.top for line in heading_rows] == [117.25]
    observation = geometry.headings[0]
    assert observation.top_pt == 100.0
    # the heading -> content gap spans past the wrapped heading line
    assert observation.content_gap_below_pt == pytest.approx(141.94 - 131.6)


# --- E→F generalization freeze fix (owner ruling 2026-09-10) ---

F_SCAFFOLD = [
    HeaderScaffold(role="name", top_pt=29.7, x0_pt=247, x1_pt=365, evidence_ids=["f1"], slots=["name"], alignment="center"),
    HeaderScaffold(role="contact", top_pt=56.4, x0_pt=136, x1_pt=475, evidence_ids=["f2"], slots=["phone", "envelope", "github", "linkedin"], alignment="center"),
]
REQUIRED_WITH_LOCATION = ["name", "location", "phone", "envelope", "github", "linkedin"]


def test_invented_target_row_is_normalized_and_rejected_without_crash() -> None:
    """The E→F freeze: all Architect candidates invented a measured-scaffold-
    foreign location row; the old order (align → relocate → validate) crashed
    with KeyError instead of recording rejections."""
    invented = HeaderTopology(candidate_id="invented", rows=[
        HeaderRow(target_role="name", slots=["name"], alignment="center"),
        HeaderRow(target_role="contact", slots=["phone", "envelope"], alignment="center"),
        HeaderRow(target_role="location", slots=["location"], alignment="center"),
    ])

    normalized, actions = relocate_misplaced_slots(invented, F_SCAFFOLD)
    aligned = apply_measured_alignment(normalized, F_SCAFFOLD)  # must not raise
    failures = validate_topology(aligned, F_SCAFFOLD, REQUIRED_WITH_LOCATION)

    assert actions == []  # no extension row to receive the slot: nothing relocated
    assert aligned.rows[2].alignment == "center"  # unknown role left untouched, not crashed
    assert failures, "the invented row must be rejected with details"
    assert any("'name', 'contact'" in failure and "'name', 'contact', 'location'" in failure for failure in failures)


def test_invented_row_slot_is_relocated_into_extension_row_then_rejected() -> None:
    invented = HeaderTopology(candidate_id="invented", rows=[
        HeaderRow(target_role="name", slots=["name"], alignment="center"),
        HeaderRow(target_role="contact", slots=["phone", "envelope"], alignment="center"),
        HeaderRow(target_role="location", slots=["location"], alignment="center"),
        HeaderRow(target_role="extension", slots=["tagline"], alignment="center"),
    ])

    normalized, actions = relocate_misplaced_slots(invented, F_SCAFFOLD)
    aligned = apply_measured_alignment(normalized, F_SCAFFOLD)  # must not raise
    failures = validate_topology(aligned, F_SCAFFOLD, REQUIRED_WITH_LOCATION)

    assert actions == [{"action": "relocate_slot", "slot": "location", "from_role": "location", "to_role": "extension"}]
    assert normalized.rows[3].slots == ["tagline", "location"]
    assert aligned.rows[3].alignment == "center"  # extension gets the measured identity alignment
    # the invented row itself remains and is rejected with full details (retry context)
    assert any("target rows must be" in failure for failure in failures)
    assert any("'name', 'contact', 'location'" in failure for failure in failures)


def test_architect_prompt_injects_measured_slot_home_facts() -> None:
    """Owner ruling 2026-09-10 (E→F second freeze): the slot→home mapping is
    injected as deterministic, candidate-blind facts derived from the
    measured scaffold, so target-declared slots cannot be dropped."""
    prompt = c_pipeline_module.architect_prompt(F_SCAFFOLD, ["name", "location", "phone", "envelope"])

    assert '"target_row_slots"' in prompt
    assert '"contact": ["phone", "envelope", "github", "linkedin"]' in prompt
    assert '"slots_without_target_row": ["location"]' in prompt
    assert "even when required_candidate_slots does not mention it" in prompt
    assert "place each of them in an extension row" in prompt


def test_plain_ul_li_annotated_is_discovered_as_bullet_unit() -> None:
    """E→F fourth freeze (owner ruling): bullet-unit discovery is structural —
    a plain <ul><li> with provenanced text is a bullet unit even when the
    template uses none of the entry-list/entry-item class vocabulary."""
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0007"><h2 class="section-heading" data-source-line="L0007">Experience</h2>
        <ul><li data-source-line="L0008">\u2022 Analyzed performance data</li><li data-source-line="L0009">\u2022 Designed dashboards</li></ul>
        <p data-source-line="L0010">Plain body line</p>
      </section>
    </body></html>"""

    prepared, anchors, unit_ids = c_pipeline_module._prepare_body_line_fit(candidate)

    tiers = {anchor.text: anchor.tier for anchor in anchors}
    assert tiers["\u2022 Analyzed performance data"] == "bullet_text"
    assert tiers["\u2022 Designed dashboards"] == "bullet_text"
    assert tiers["Plain body line"] == "l1"
    assert unit_ids  # exactly one discovered unit
    assert "data-c1-bullet-unit" in prepared


def test_prose_without_ul_is_not_mistiered_as_bullet_text() -> None:
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0001"><h2 class="section-heading" data-source-line="L0001">Summary</h2>
        <p data-source-line="L0002">Plain prose line without any list</p>
      </section>
    </body></html>"""

    _, anchors, unit_ids = c_pipeline_module._prepare_body_line_fit(candidate)

    assert unit_ids == []
    assert all(anchor.tier == "l1" for anchor in anchors)


def test_acceptance_table_lists_targetless_bullet_text_transparently() -> None:
    geometry = BodyGeometry(
        {},
        (BodyLine(1, 240.0, 61.32, "\u2022 Analyzed data", "b1", "ONE", "bullet_text", 500.0),),
        (), (),
    )
    table = render_body_x0_acceptance(geometry, {"l1": 46.8})  # no bullet_text target

    assert "\u65e0\u76ee\u6807\u4f9d\u636e" in table  # 无目标依据
    assert "FAIL" not in table.replace("FAIL |", "").replace("FAIL", "", 0) or "61.320 | — | 无目标依据 |" in table
    assert "| ONE | \u2022 Analyzed data | 61.320 | — | 无目标依据 |" in table


# --- E→F fifth freeze fix (owner ruling): container inheritance normalization ---

def _inherit(candidate: str, source: str):
    normalized, actions = c_pipeline_module.inherit_container_source_line(candidate, source)
    failures = c_pipeline_module.c1_provenance_contract_failures(normalized, source)
    return normalized, actions, failures


def test_strong_tail_span_idiom_inherits_container_source_line() -> None:
    source = "Languages: C#, HTML/CSS, Java\nProfile text"
    candidate = ('<html><body><p><strong data-source-line="L0001">Languages</strong>'
                 ': <span data-source-line="L0001">C#, HTML/CSS, Java</span></p>'
                 '<p data-source-line="L0002">Profile text</p></body></html>')

    normalized, actions, failures = _inherit(candidate, source)

    assert actions == [{"action": "inherit_source_line", "node": "/html/body/p[1]",
                        "source_line_id": "L0001", "marked_direct_children": []}]
    assert 'data-source-line="L0001"' in normalized.split("</p>")[0]
    assert failures == []


def test_concatenation_matching_no_source_line_stays_rejected() -> None:
    """Final invariant-① semantics: an annotated container carries its whole
    verified line, so a concatenation mismatch is falsified at the CONTENT
    level (coverage missing tokens), not by an ownership failure."""
    source = "Languages: C#, HTML/CSS, Java"
    candidate = ('<html><body><p><strong data-source-line="L0001">Languages</strong>'
                 ': <span data-source-line="L0001">C#, HTML/CSS</span></p></body></html>')

    normalized, actions, failures = _inherit(candidate, source)

    assert actions == []
    assert failures == []
    from tests.experiments.fill_plan import analyze_candidate_provenance
    analysis = analyze_candidate_provenance(normalized, source)
    assert any(finding.get("code") == "missing_source_content" for finding in analysis["findings"])


def test_ambiguous_match_two_source_lines_stays_rejected() -> None:
    source = "Languages: C#\nLanguages: C#"
    candidate = ('<html><body><p><strong data-source-line="L0001">Languages</strong>'
                 ': <span data-source-line="L0001">C#</span></p></body></html>')

    normalized, actions, failures = _inherit(candidate, source)

    assert actions == []  # ambiguity is not inheritance
    assert failures == []  # ownership is clean; the ambiguity gate is the rejection


def test_deeper_unannotated_node_stays_rejected() -> None:
    """Deeper unannotated nodes block INHERITANCE (conservative, preserved);
    the stray content itself is falsified by the invented-content gate, which
    is content-level and independent of the ownership walk."""
    source = "Languages: C#"
    candidate = ('<html><body><p><strong data-source-line="L0001">Languages</strong>'
                 ': <span data-source-line="L0001">C#, <em>extra</em></span></p></body></html>')

    normalized, actions, failures = _inherit(candidate, source)

    assert actions == []  # deeper unannotated node: no inheritance
    from tests.experiments.fill_plan import analyze_candidate_provenance
    analysis = analyze_candidate_provenance(normalized, source)
    assert any(finding.get("code") == "invented_content" for finding in analysis["findings"])


def test_container_annotated_label_carries_unannotated_children() -> None:
    """The E→F ninth-freeze shape: the Filler annotated the container <p> and
    left the label element unannotated — container-level ownership carries the
    whole verified line, so no unprovenanced finding is possible."""
    source = "Languages: C#, HTML/CSS, Java, JavaScript, LATEX, Python, SQL"
    candidate = ('<html><body><p data-source-line="L0052"><strong>Languages:</strong> '
                 'C#, HTML/CSS, Java, JavaScript, LATEX, Python, SQL</p></body></html>')

    normalized, actions, failures = _inherit(candidate, source)

    assert actions == []  # container already annotated: nothing to inherit
    assert failures == []


def test_d_to_e_regression_inheritance_is_noop_without_the_idiom() -> None:
    source = "SUMMARY\nProfile text"
    candidate = '<html><body><p data-source-line="L0001">SUMMARY</p><p data-source-line="L0002">Profile text</p></body></html>'

    normalized, actions, failures = _inherit(candidate, source)

    assert actions == []  # D→E seeds have no strong:tail:span idiom: strict no-op
    assert failures == []


def test_container_without_bare_tails_never_inherits() -> None:
    """Regression (run 103617Z): entry-row containers whose text lives entirely
    inside annotated children carry no unprovenanced text, must not inherit a
    line annotation (record-scoped containers break one_content_record), and
    the normalizer stays a no-op for them."""
    source = "Software Engineer II\nMicrosoft"
    candidate = ('<html><body><div class="entry-title-row">'
                 '<h3 data-source-line="L0001">Software Engineer II</h3></div>'
                 '<div class="entry-subtitle-row"><span data-source-line="L0002">Microsoft</span></div>'
                 '</body></html>')

    normalized, actions, failures = _inherit(candidate, source)

    assert actions == []
    assert failures == []


def test_unannotated_label_element_is_carried_by_container_inheritance() -> None:
    """E→F sixth freeze (owner ruling, option A): the Filler left the label
    element unannotated inside the template's strong:colon:value idiom; the
    container inherits the line and the unannotated direct text-leaf child is
    marked in the same stroke — every fragment carried, nothing overwritten."""
    source = "Languages: C#, HTML/CSS, Java"
    candidate = ('<html><body><p><strong>Languages</strong>'
                 ': <span data-source-line="L0001">C#, HTML/CSS, Java</span></p></body></html>')

    normalized, actions, failures = _inherit(candidate, source)

    assert len(actions) == 1 and actions[0]["source_line_id"] == "L0001"
    assert len(actions[0]["marked_direct_children"]) == 1  # the strong leaf
    assert '<strong data-source-line="L0001">Languages</strong>' in normalized
    assert failures == []


def test_conflicting_annotated_line_inside_container_is_rejected() -> None:
    source = "Languages: C#, HTML/CSS, Java"
    candidate = ('<html><body><p><strong>Languages</strong>'
                 ': <span data-source-line="L0002">C#, HTML/CSS, Java</span></p></body></html>')

    normalized, actions, failures = _inherit(candidate, source)

    assert actions == []  # annotated descendant carries a different line: conflict
    assert any("unprovenanced_text" in failure for failure in failures)


def test_small_template_markers_are_collected(monkeypatch) -> None:
    """E→F seventh freeze (owner ruling): F's 2.82pt template markers must be
    collected (window ≥1pt ≤8pt), not just D→E's 5pt dots."""
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0007"><h2 class="section-heading" data-source-line="L0007">Experience</h2>
        <ul><li class="entry-item" data-source-line="L0008">\u2022 Analyzed performance data</li></ul>
      </section>
    </body></html>"""
    words = [
        {"text": "Experience", "top": 100.0, "bottom": 114.35, "x0": 36.0, "x1": 90.0},
        {"text": "\u2022", "top": 120.0, "bottom": 128.4, "x0": 71.41, "x1": 74.37},
        {"text": "Analyzed", "top": 120.0, "bottom": 128.4, "x0": 76.0, "x1": 120.0},
    ]
    rects = [{"top": 121.4, "bottom": 124.22, "x0": 61.42, "x1": 64.24}]  # 2.82pt marker

    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([_FakePage(words, rects=rects)]))

    geometry = measure_body_lines(candidate, "ignored.pdf")

    assert geometry.failures == ()
    assert [dot.x0 for dot in geometry.dots] == [61.42]


def test_compiler_suppresses_template_markers_only_without_bullet_target() -> None:
    html = compile_header_template(
        BASE, TOPOLOGY, SCAFFOLD, SUMMARY, FitParameters(gaps_pt=[1, 2, 3]),
        body_scaffold=BODY_SCAFFOLD, bullet_marker_target=False,
    )
    assert "ul{list-style:none!important}" in html
    assert ".entry-item::before{content:none!important}" in html
    # with a measured bullet_dot tier the template markers are retained
    kept = compile_header_template(
        BASE, TOPOLOGY, SCAFFOLD, SUMMARY, FitParameters(gaps_pt=[1, 2, 3]),
        body_scaffold=BODY_SCAFFOLD, bullet_marker_target=True,
    )
    assert "list-style:none" not in kept


def test_inline_verbatim_bullet_glyph_is_the_marker(monkeypatch) -> None:
    """Owner rulings 2026-09-10 (• retention + marker suppression): with no
    target bullet design the verbatim source glyph is the bullet itself —
    a bullet_text line starting with the glyph carries its marker inline
    (dot x0 = glyph x0), no separate mark required."""
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0007"><h2 class="section-heading" data-source-line="L0007">Experience</h2>
        <ul><li data-source-line="L0008">\u2022 Analyzed performance data</li></ul>
      </section>
    </body></html>"""
    words = [
        {"text": "Experience", "top": 100.0, "bottom": 114.35, "x0": 36.0, "x1": 90.0},
        {"text": "\u2022", "top": 120.0, "bottom": 128.4, "x0": 71.41, "x1": 74.37},
        {"text": "Analyzed", "top": 120.0, "bottom": 128.4, "x0": 76.0, "x1": 120.0},
    ]

    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([_FakePage(words)]))

    geometry = measure_body_lines(candidate, "ignored.pdf")

    assert geometry.failures == ()
    assert [dot.x0 for dot in geometry.dots] == [71.41]


def test_mixed_indent_section_converges_with_line_level_knobs(monkeypatch) -> None:
    """E→F eighth freeze (owner ruling, option A): a section with mixed
    indentation (grouped lines carry the template's own margin; one bare line
    does not) converges via the section knob (in-group median) plus
    independent per-line corrections — no oscillation, no borrowed signal."""
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0051"><h2 class="section-heading" data-source-line="L0051">Skills</h2>
        <div class="skills-group"><p data-source-line="L0052">Software: Atlassian, AWS</p></div>
        <div class="skills-group"><p data-source-line="L0053">Microsoft: Azure, Django</p></div>
        <p data-source-line="L0054">Languages: C#, HTML/CSS, Java</p>
      </section>
    </body></html>"""
    TEMPLATE_OFFSET = 10.17  # the grouped lines' own template margin

    def measure(html: str, pdf: Path, **kwargs) -> BodyGeometry:
        del pdf
        style = re.search(r"<style[^>]*data-c1-section-fit[^>]*>(.*?)</style>", html, re.S).group(1)
        indent = float(re.search(r"block:L0051'\]\{--c1-section-indent:([0-9.]+)pt", style).group(1))
        line_pads = {m.group(1): float(m.group(2))
                     for m in re.finditer(r"\[data-c1-line-fit='(a\d+)'\]\{margin-left:([0-9.eE+-]+)pt\}", style)}
        prepared, anchors, _ = c_pipeline_module._prepare_body_line_fit(html)
        x0s, dots = {}, []
        for anchor in anchors:
            pad = line_pads.get(anchor.key, 0.0)
            x0s[anchor.key] = 36 + indent + pad + (TEMPLATE_OFFSET if anchor.text != "Languages: C#, HTML/CSS, Java" else 0)
            if anchor.tier == "bullet_text":
                dots.append(c_pipeline_module.BodyDot(anchor.block_id, anchor.label, anchor.unit_id or "", x0s[anchor.key]))
        return BodyGeometry(x0s, (), tuple(dots), (),
                            headings=(BodyHeadingObservation("Skills", 100.0, 36.0, 14.35, None, None, None,
                                                             block_id="block:L0051"),))

    monkeypatch.setattr(c_pipeline_module, "measure_body_lines", measure)

    result = fit_body_line_geometry(
        candidate, {"l1": 46.8}, 10.8, lambda html, number: Path("probe.pdf"),
        heading_targets={"x0_pt": 36.0, "font_height_pt": 14.35},
    )

    assert result.passed
    _, anchors, _ = c_pipeline_module._prepare_body_line_fit(candidate)
    for anchor in anchors:
        assert abs(result.geometry.anchor_x0[anchor.key] - 46.8) <= 1
    # the bare line got its own correction; the grouped rows did not
    bare_key = next(a.key for a in anchors if a.text == "Languages: C#, HTML/CSS, Java")
    assert result.line_knobs[f"line:{bare_key}"] > 5
    assert all(abs(v) <= 0.01 for k, v in result.line_knobs.items() if k != f"line:{bare_key}")


def test_homogeneous_section_never_triggers_line_knobs(monkeypatch) -> None:
    """D→E no-op regression: a homogeneous section converges with the section
    knob alone; line-level corrections stay empty."""
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0001"><h2 class="section-heading" data-source-line="L0001">Summary</h2>
        <p data-source-line="L0002">Alpha line</p><p data-source-line="L0003">Beta line</p>
      </section>
    </body></html>"""

    def measure(html: str, pdf: Path, **kwargs) -> BodyGeometry:
        del pdf
        style = re.search(r"<style[^>]*data-c1-section-fit[^>]*>(.*?)</style>", html, re.S).group(1)
        indent = float(re.search(r"block:L0001'\]\{--c1-section-indent:([0-9.]+)pt", style).group(1))
        prepared, anchors, _ = c_pipeline_module._prepare_body_line_fit(html)
        return BodyGeometry({a.key: 36 + indent for a in anchors}, (), (), ())

    monkeypatch.setattr(c_pipeline_module, "measure_body_lines", measure)

    result = fit_body_line_geometry(
        candidate, {"l1": 46.8}, 10.8, lambda html, number: Path("probe.pdf"),
    )

    assert result.passed
    assert result.line_knobs == {}


def test_right_edge_overshoot_gets_one_shot_structural_correction(monkeypatch) -> None:
    """E→F ninth freeze (owner ruling): rows whose right edge crosses the
    measured content boundary get a one-shot margin-right correction located
    through their anchors (no class vocabulary), then a verification render."""
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0007"><h2 class="section-heading" data-source-line="L0007">Experience</h2>
        <div class="entry-title-row"><h3 data-source-line="L0009">Software Engineer II</h3><span class="entry-date">April 2019 \u2013 Present</span></div>
      </section>
    </body></html>"""
    TARGET = 576.006
    state = {"renders": 0}

    def measure(html: str, pdf: Path, **kwargs) -> BodyGeometry:
        del pdf
        state["renders"] += 1
        # render 1: the row's right edge overshoots the boundary by 3.26pt;
        # after the correction (margin-right on the row container) it lands inside.
        section_mr = float(re.search(r"\[data-c1-right-fit='[^']*'\]\{margin-right:([0-9.]+)pt\}", html).group(1)) if "margin-right" in html else 0.0
        x1 = 579.27 - section_mr
        prepared, anchors, _ = c_pipeline_module._prepare_body_line_fit(html)
        anchor_x0 = {a.key: 46.19 for a in anchors}
        lines = (BodyLine(1, 120.0, 46.19, "Software Engineer II April 2019 \u2013 Present",
                          "block:L0007", "Experience", "l1", x1, next(a.key for a in anchors)),)
        return BodyGeometry(anchor_x0, lines, (), (),)

    monkeypatch.setattr(c_pipeline_module, "measure_body_lines", measure)

    result = fit_body_line_geometry(
        candidate, {"l1": 46.8}, 10.8, lambda html, number: Path("probe.pdf"),
        entry_right_x1_pt=TARGET,
    )

    assert result.passed
    assert result.right_row_corrections and abs(next(iter(result.right_row_corrections.values())) - 3.264) <= 0.01
    assert 'data-c1-right-fit' in result.html and "margin-right:3.264pt" in result.html
    assert state["renders"] == 2  # one-shot + verification, no iteration


def test_right_edge_within_boundary_never_triggers_correction(monkeypatch) -> None:
    """D→E regression: right edges already within the measured boundary never
    trigger the correction phase."""
    candidate = """<html><head></head><body>
      <section data-source-block="block:L0001"><h2 class="section-heading" data-source-line="L0001">Skills</h2>
        <p data-source-line="L0002">Languages: C#, HTML/CSS, Java</p>
      </section>
    </body></html>"""

    def measure(html: str, pdf: Path, **kwargs) -> BodyGeometry:
        del pdf
        prepared, anchors, _ = c_pipeline_module._prepare_body_line_fit(html)
        anchor_x0 = {a.key: 46.8 for a in anchors}
        lines = (BodyLine(1, 120.0, 46.8, "Languages: C#, HTML/CSS, Java",
                          "block:L0001", "Skills", "l1", 570.6, anchors[0].key),)
        return BodyGeometry(anchor_x0, lines, (), (),)

    monkeypatch.setattr(c_pipeline_module, "measure_body_lines", measure)

    result = fit_body_line_geometry(
        candidate, {"l1": 46.8}, 10.8, lambda html, number: Path("probe.pdf"),
        entry_right_x1_pt=576.006,
    )

    assert result.passed
    assert result.right_row_corrections == {}
    assert "data-c1-right-fit" not in result.html


def test_date_column_never_flex_shrinks() -> None:
    """E→F eleventh freeze (owner ruling): the right-aligned date column gets
    flex-shrink:0 — a long date ('April 2019 – Present' class) must keep its
    full nowrap width even beside a short left column; D→E's template already
    declared flex:0 0 auto, so this is redundant-but-harmless there."""
    html = compile_header_template(
        BASE, TOPOLOGY, SCAFFOLD, SUMMARY, FitParameters(gaps_pt=[1, 2, 3]),
        body_scaffold=BODY_SCAFFOLD, bullet_marker_target=False,
    )
    entry_rule = re.search(r"\.entry-right\{([^}]*)\}", html).group(1)
    assert "flex-shrink:0" in entry_rule
    assert "min-width:0" in entry_rule


# --- E→F twelfth freeze: render-aware duplicate-bullet detection ---

def _bullet_pdf(monkeypatch, with_marker: bool, with_glyph: bool):
    words, rects = [], []
    if with_glyph:
        words.append({"text": "\u2022", "top": 120.0, "bottom": 128.4, "x0": 71.41, "x1": 74.37})
    words.append({"text": "Analyzed", "top": 120.0, "bottom": 128.4, "x0": 76.0, "x1": 120.0})
    if with_marker:
        rects.append({"top": 121.4, "bottom": 124.22, "x0": 61.42, "x1": 64.24})
    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda path: _FakePdf([_FakePage(words, rects=rects)]))


def test_duplicate_bullet_glyph_plus_rendered_marker_is_reported(monkeypatch) -> None:
    """D→E shape: a real rendered pseudo-marker beside a source bullet glyph →
    still reported as a duplicate."""
    _bullet_pdf(monkeypatch, with_marker=True, with_glyph=True)

    duplicates = c_pipeline_module.rendered_duplicate_bullet_lines(Path("ignored.pdf"))

    assert len(duplicates) == 1
    assert duplicates[0]["glyph"] == "\u2022"
    assert duplicates[0]["marker_x0"] == 61.42


def test_suppressed_marker_with_source_glyph_passes(monkeypatch) -> None:
    """F shape: content:none suppression (no rendered marker) + source glyph →
    no duplicate; the inert CSS declaration is invisible to this check."""
    _bullet_pdf(monkeypatch, with_marker=False, with_glyph=True)

    assert c_pipeline_module.rendered_duplicate_bullet_lines(Path("ignored.pdf")) == []


def test_no_glyph_no_marker_passes(monkeypatch) -> None:
    _bullet_pdf(monkeypatch, with_marker=False, with_glyph=False)

    assert c_pipeline_module.rendered_duplicate_bullet_lines(Path("ignored.pdf")) == []


# --- Stage-1 infrastructure round (owner work order 2026-09-10/11) ----------


def _body_scaffold_with(headings: list[BodyHeadingScaffold], **entry_kwargs) -> BodyScaffold:
    entry = BodyEntryScaffold(left_x0_pt=46.909, right_x1_pt=576.0, evidence_ids=["adobe.3"], **entry_kwargs)
    return BodyScaffold(headings=headings, entry=entry)


def test_target_measured_uppercase_headings_compile_to_uppercase_transform() -> None:
    """E→F twelfth-freeze ruling: all-uppercase measured headings compile to
    uppercase; the frozen D→E sentence-case path (lowercase+first-letter) is
    unchanged when target headings are not all-uppercase."""
    lower = _body_scaffold_with([BodyHeadingScaffold(
        verbatim="Experience", page=1, top_pt=98.99, x0_pt=36.0, x1_pt=106.74,
        font_height_pt=14.35, font_size_pt=14.346, line_height_pt=17.25, bold=True,
        font_family="Lato", evidence_ids=["adobe.1"],
    )])
    css = "\n".join(c_pipeline_module._body_css(lower))
    assert ".section-heading{text-transform:uppercase!important}" not in css

    upper = _body_scaffold_with([BodyHeadingScaffold(
        verbatim="EXPERIENCE", page=1, top_pt=98.99, x0_pt=36.0, x1_pt=106.74,
        font_height_pt=14.35, font_size_pt=14.346, line_height_pt=17.25, bold=True,
        font_family="Lato", evidence_ids=["adobe.1"],
    )])
    css = "\n".join(c_pipeline_module._body_css(upper))
    assert ".section-heading{text-transform:uppercase!important}" in css


def test_uppercase_override_wins_the_cascade_over_frozen_sentence_case() -> None:
    """The compiler appends the body style node after the header style node, so
    the target-derived uppercase rule wins over the seed's Title Case rule."""
    upper = _body_scaffold_with([BodyHeadingScaffold(
        verbatim="SKILLS", page=1, top_pt=98.99, x0_pt=36.0, x1_pt=68.52,
        font_height_pt=14.35, font_size_pt=14.346, line_height_pt=17.25, bold=True,
        font_family="Lato", evidence_ids=["adobe.2"],
    )])
    html = compile_header_template(BASE, TOPOLOGY, SCAFFOLD, SUMMARY, FitParameters(gaps_pt=[1, 2, 3]), body_scaffold=upper)
    body_style = re.search(r'<style data-c1-compiler="body/1">([\s\S]*?)</style>', html).group(1)
    header_style = re.search(r'<style data-c1-compiler="header/1">([\s\S]*?)</style>', html).group(1)
    assert ".section-heading{text-transform:uppercase!important}" in body_style
    assert ".section-heading{text-transform:lowercase!important}" in header_style


def test_rule_below_heading_is_block_full_width_at_measured_gaps() -> None:
    heading = BodyHeadingScaffold(
        verbatim="EXPERIENCE", page=1, top_pt=98.99, x0_pt=36.0, x1_pt=106.74,
        font_height_pt=14.35, font_size_pt=14.346, line_height_pt=17.25, bold=True,
        font_family="Lato", rule_below_gap_pt=4.2, rule_below_x0_pt=36.0, rule_below_x1_pt=576.0,
        rule_below_stroke_pt=0.398, rule_below_color_hex="#000000", post_rule_content_gap_pt=7.5,
        evidence_ids=["adobe.1"],
    )
    css = "\n".join(c_pipeline_module._body_css(_body_scaffold_with([heading])))
    assert ".section-heading .heading-rule{display:block;width:100%;flex-grow:0;margin-left:0;" in css
    assert "margin-top:calc(4.200pt + var(--c1-heading-rule-gap,0pt));" in css
    assert "margin-bottom:calc(7.500pt + var(--c1-heading-content-gap,0pt));" in css
    assert "border-top:0.398pt solid #000000;}" in css
    assert ".section-heading h2{margin-bottom:0}" in css  # gaps owned by the rule margins


def test_contact_separator_is_css_content_never_html_text() -> None:
    """The measured separator renders as CSS ::before content between contact
    items — no HTML text node, so the invented-content scan is unaffected."""
    heading = BodyHeadingScaffold(
        verbatim="SKILLS", page=1, top_pt=98.99, x0_pt=36.0, x1_pt=68.52,
        font_height_pt=14.35, font_size_pt=14.346, line_height_pt=17.25, bold=True,
        font_family="Lato", evidence_ids=["adobe.2"],
    )
    scaffold = BodyScaffold(
        headings=[heading],
        entry=BodyEntryScaffold(left_x0_pt=46.909, right_x1_pt=576.0, evidence_ids=["adobe.3"]),
        contact_icons_present=False,
        contact_separator=" | ",
    )
    html = compile_header_template(BASE, TOPOLOGY, SCAFFOLD, SUMMARY, FitParameters(gaps_pt=[1, 2, 3]), body_scaffold=scaffold)
    body_style = re.search(r'<style data-c1-compiler="body/1">([\s\S]*?)</style>', html).group(1)
    assert ".c1-icon{display:none}" in body_style
    assert ".c1-contact-item{margin-right:0}" in body_style
    assert ".c1-contact-item + .c1-contact-item::before{content:'|'}" in body_style
    # no literal separator text node in the contact DOM
    assert "<i class=\"c1-icon" not in html  # no icon elements built for an icon-less target
    header_html = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert " | " not in header_html
    # every contact value is wrapped in a compiler-owned item container even
    # for an icon-free target, so the adjacent-sibling ::before separator
    # exists; non-contact slots stay unwrapped and the wrapper never carries
    # the template's 'contact-item' token (that implies an icon)
    items = re.findall(r'<span class="c1-contact-item">', header_html)
    assert len(items) >= 4  # phone/envelope/github/linkedin all wrapped
    assert "contact-item c1-contact-item" not in header_html  # no icon-idiom class on icon-free items
    assert "<i" not in header_html  # no icon elements built for an icon-less target


def test_icons_still_built_when_target_contact_line_has_icons() -> None:
    heading = BodyHeadingScaffold(
        verbatim="SKILLS", page=1, top_pt=98.99, x0_pt=36.0, x1_pt=68.52,
        font_height_pt=14.35, font_size_pt=14.346, line_height_pt=17.25, bold=True,
        font_family="Lato", evidence_ids=["adobe.2"],
    )
    scaffold = BodyScaffold(
        headings=[heading],
        entry=BodyEntryScaffold(left_x0_pt=46.909, right_x1_pt=576.0, evidence_ids=["adobe.3"]),
        contact_icons_present=True,
        contact_separator=None,
    )
    html = compile_header_template(BASE, TOPOLOGY, SCAFFOLD, SUMMARY, FitParameters(gaps_pt=[1, 2, 3]), body_scaffold=scaffold)
    body_style = re.search(r'<style data-c1-compiler="body/1">([\s\S]*?)</style>', html).group(1)
    assert ".c1-icon{display:none}" not in body_style
    assert "fa-brands fa-github" in html
    assert ".c1-contact-item + .c1-contact-item::before" not in body_style


def _stability_pdf(monkeypatch, lines: list[dict]) -> None:
    class _Page:
        def __init__(self, words):
            self._words = words
            self.chars = []

        def extract_words(self):
            return self._words

    class _Doc:
        def __init__(self, pages):
            self.pages = pages

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda path: _Doc([_Page(lines)]))


def test_per_line_render_stability_passes_within_tolerance(monkeypatch) -> None:
    lines = [{"text": "Software Engineer II", "top": 137.28, "bottom": 148.19, "x0": 46.909, "x1": 300.0, "page": 1}]
    monkeypatch.setattr(c_pipeline_module, "_pdf_lines_and_marks", lambda pdf: (lines, []))
    stable, details = c_pipeline_module.per_line_render_stability(Path("a.pdf"), Path("b.pdf"))
    assert stable
    assert details["max_delta_x0_pt"] == 0.0 and details["max_delta_x1_pt"] == 0.0


def test_per_line_render_stability_fails_on_drift(monkeypatch) -> None:
    first = [{"text": "April 2019 – Present", "top": 137.28, "bottom": 148.19, "x0": 400.0, "x1": 572.24, "page": 1}]
    second = [{"text": "April 2019 – Present", "top": 137.28, "bottom": 148.19, "x0": 400.0, "x1": 575.99, "page": 1}]
    pdfs = iter([first, second])
    monkeypatch.setattr(c_pipeline_module, "_pdf_lines_and_marks", lambda pdf: (next(pdfs), []))
    stable, details = c_pipeline_module.per_line_render_stability(Path("a.pdf"), Path("b.pdf"))
    assert not stable
    assert details["max_delta_x1_pt"] == pytest.approx(3.75, abs=0.01)
    assert details["mismatches"][0]["code"] == "line_geometry"


def test_per_line_render_stability_fails_on_line_count_drift(monkeypatch) -> None:
    first = [{"text": "one", "top": 1, "bottom": 2, "x0": 10.0, "x1": 20.0, "page": 1}]
    second = [
        {"text": "one", "top": 1, "bottom": 2, "x0": 10.0, "x1": 20.0, "page": 1},
        {"text": "two", "top": 3, "bottom": 4, "x0": 10.0, "x1": 20.0, "page": 1},
    ]
    pdfs = iter([first, second])
    monkeypatch.setattr(c_pipeline_module, "_pdf_lines_and_marks", lambda pdf: (next(pdfs), []))
    stable, details = c_pipeline_module.per_line_render_stability(Path("a.pdf"), Path("b.pdf"))
    assert not stable
    assert details["mismatches"][0]["code"] == "line_count"


def test_fail_closed_font_gate_fails_on_substituted_required_font() -> None:


    gates = HardGateResult(passed=True, independent={"first": {"fonts": {
        "requested": ["Roboto", "Charter"],
        "generated_pdf_fonts": ["AAAAAA+Arial-BoldMT", "CAAAAA+ArialMT"],
        "missing_or_substituted": [],
        "approved_substitutions_used": ["Roboto"],
    }}})
    summary = {"style_groups": {"s1": {"font_family": "Roboto"}, "s2": {"font_family": "CMS Y 9"}}}
    template = "<style>body{font-family:Roboto}</style>"
    gated = c_pipeline_module.fail_closed_font_gate(gates, template, summary)
    assert not gated.passed
    codes = [failure.code for failure in gated.failures]
    assert "font_gate_fail_closed" in codes
    details = next(failure.details for failure in gated.failures if failure.code == "font_gate_fail_closed")
    assert details["family"] == "Roboto"  # target-measured ∩ template-requested; CMS Y 9 is not template-requested


def test_fail_closed_font_gate_passes_when_required_fonts_embedded() -> None:
    gates = HardGateResult(passed=True, independent={"first": {"fonts": {
        "requested": ["Roboto"],
        "generated_pdf_fonts": ["AAAAAA+Roboto-Regular", "BAAAAA+FontAwesome6Free-Solid"],
        "missing_or_substituted": [],
        "approved_substitutions_used": [],
    }}})
    summary = {"style_groups": {"s1": {"font_family": "Roboto"}}}
    template = "<style>body{font-family:Roboto}</style>"
    gated = c_pipeline_module.fail_closed_font_gate(gates, template, summary)
    assert gated.passed


def test_fail_closed_font_gate_transparently_declares_used_substitutions() -> None:
    """The A-era substitution map is demoted to an explicit declaration: a used
    substitution is recorded in the gate result even when it does not fail the
    run (i.e. for families that are requested but not target-measured)."""
    gates = HardGateResult(passed=True, independent={"first": {"fonts": {
        "requested": ["Roboto", "Charter"],
        "generated_pdf_fonts": ["AAAAAA+ArialMT"],
        "missing_or_substituted": [],
        "approved_substitutions_used": ["Charter"],
    }}})
    summary = {"style_groups": {}}  # nothing measured in target → nothing required
    template = "<style>body{font-family:Charter}</style>"
    gated = c_pipeline_module.fail_closed_font_gate(gates, template, summary)
    assert gated.passed  # no target-measured ∩ template-requested requirement violated
    assert gated.independent["first"]["fonts"]["approved_substitutions_used"] == ["Charter"]  # stays transparent


def test_pinned_export_environment_forces_font_load_determinism() -> None:
    env = c_pipeline_module.pinned_export_environment(SUMMARY)
    assert "--virtual-time-budget=10000" in env["flags"]
    # the pinned flags themselves are preserved
    assert "--print-to-pdf-no-header" in env["flags"]


def test_pdf_font_match_accepts_same_design_variant_rejects_other_design() -> None:
    """E→D cold start (2026-09-11): a multi-word measured family ('Charter
    BT') matches the embedded resource of the same design family
    ('Charter-Roman') — the A-era substitution map demoted to structure;
    a different design (Arial for Roboto) still fails."""
    generated = ["AAAAAA+Charter-Roman", "BAAAAA+ArialMT"]
    assert _pdf_font_name_matches(generated, "Charter BT")
    assert not _pdf_font_name_matches(generated, "Roboto")
    assert _pdf_font_name_matches(["CAAAAA+Lato-Regular"], "Lato")


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


def test_zero_bullet_marker_suppression_wins_the_cascade() -> None:
    """E→D cold start (2026-09-11): the seed's class-scoped marker rule must
    not outspecify the compiler-owned suppression on a zero-bullet target."""
    from tests.experiments.c_pipeline import _body_css
    css = " ".join(_body_css(BODY_SCAFFOLD, bullet_marker_target=False))
    assert "content:none!important" in css


def test_double_leading_glyph_is_a_duplicate_regardless_of_marker_kind(tmp_path) -> None:
    from tests.experiments.c_pipeline import rendered_duplicate_bullet_lines
    from tests.experiments.a_pipeline import _export_pinned_html_to_pdf, _pinned_chrome_environment
    pdf = tmp_path / "dup.pdf"
    html = tmp_path / "dup.html"
    html.write_text(
        '<!DOCTYPE html><html><head><style>body{font-family:"Lato";font-size:10pt}</style></head>'
        '<body><p style="margin:40pt">• • Analyzed performance data</p></body></html>'
    )
    _export_pinned_html_to_pdf(html, pdf, _pinned_chrome_environment({"margins_pt": {"default": {}}}))
    duplicates = rendered_duplicate_bullet_lines(pdf)
    assert duplicates, "two adjacent leading glyphs must be flagged"


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


def test_validate_topology_allows_location_on_contact_row() -> None:
    """Owner option B: a contact row may carry the location slot when the
    scaffold declares it (D target's contact line carries the location
    marker); the pre-ruling contact-only restriction is superseded."""
    scaffold = [
        HeaderScaffold(role="name", top_pt=19.0, x0_pt=100.0, x1_pt=200.0, evidence_ids=["e1"], slots=["name"], alignment="center"),
        HeaderScaffold(role="contact", top_pt=46.0, x0_pt=68.0, x1_pt=523.0, evidence_ids=["e1"], slots=["phone", "location"], alignment="center"),
        HeaderScaffold(role="tagline", top_pt=65.0, x0_pt=225.0, x1_pt=368.0, evidence_ids=["e1"], slots=["tagline"], alignment="center"),
    ]
    topology = HeaderTopology(candidate_id="c1", rows=[
        HeaderRow(target_role="name", slots=["name"], alignment="center"),
        HeaderRow(target_role="contact", slots=["phone", "location"], alignment="center"),
        HeaderRow(target_role="tagline", slots=["tagline"], alignment="center"),
    ])
    assert validate_topology(topology, scaffold, ["name", "phone", "location"]) == []


def test_deterministic_topology_equals_selected_architect_topology_offline() -> None:
    # Owner phase 2 ablation (2026-09-11): the deterministic header topology
    # (scaffold + required slots) must equal the adopted Architect topology of
    # every frozen green run — rows, slots, and measured alignment — and must
    # pass validate_topology with zero failures. Guards the ablation's
    # equivalence evidence against future drift.
    import json as _json
    import os as _os
    from tests.experiments.c_pipeline import (
        apply_measured_alignment,
        derive_deterministic_topology,
        required_slot_kinds,
        validate_topology,
    )

    runs = {
        "D→E": "runs/c_pipeline_D_to_E_20260910T200018Z",
        "E→F": "runs/c_pipeline_D_to_E_20260910T195515Z",
        "F→E": "runs/c1_matrix_FE2_20260910T200958Z",
        "E→D": "runs/c1_matrix_ED_B_20260911T044203Z",
    }
    for pair, run in runs.items():
        scaffold = [
            HeaderScaffold.model_validate(row)
            for row in _json.load(open(_os.path.join(_os.path.dirname(__file__), run, "header_scaffold.json")))
        ]
        source_text = open(_os.path.join(_os.path.dirname(__file__), run, "source_text.txt")).read()
        required = required_slot_kinds(source_text)
        deterministic = apply_measured_alignment(
            derive_deterministic_topology(scaffold, required), scaffold
        )
        assert validate_topology(deterministic, scaffold, required) == [], (pair, "deterministic topology rejected")
        selected = HeaderTopology.model_validate(
            _json.load(open(_os.path.join(_os.path.dirname(__file__), run, "selected_topology.json")))
        )
        det_rows = [[row.target_role, row.slots, row.alignment] for row in deterministic.rows]
        sel_rows = [[row.target_role, row.slots, row.alignment] for row in selected.rows]
        assert det_rows == sel_rows, (pair, det_rows, sel_rows)
