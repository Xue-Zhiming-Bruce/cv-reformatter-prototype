from __future__ import annotations

import hashlib
import re

import pytest

from tests.experiments.a_pipeline import _missing_source_tokens
from tests.experiments.fill_plan import analyze_candidate_provenance, deduplicate_candidate_html
from tests.experiments.refinement import (
    CandidateState,
    apply_builder_operation,
    validate_builder_operation,
    validate_filler_conformance,
)


def test_provenance_reports_missing_and_invented_content() -> None:
    source = "Jane Candidate\nSKILLS\nPython"
    candidate = """<html><body>
      <header data-source-block="document"><span data-source-line="L0001">Jane Candidate</span></header>
      <section data-source-block="block:L0002" data-source-heading="L0002">
        <h2 data-source-line="L0002">SKILLS</h2><p>Rust</p>
      </section>
    </body></html>"""

    codes = {finding["code"] for finding in analyze_candidate_provenance(candidate, source)["findings"]}

    assert "missing_source_content" in codes
    assert "invented_content" in codes


def test_duplicate_source_fragments_are_removed_once() -> None:
    source = "Jane Candidate\nSKILLS\nPython"
    candidate = """<html><body>
      <header data-source-block="document"><span data-source-line="L0001">Jane Candidate</span></header>
      <section data-source-block="block:L0002" data-source-heading="L0002">
        <h2 data-source-line="L0002">SKILLS</h2>
        <p data-source-line="L0003">Python</p><p data-source-line="L0003">Python</p>
      </section>
    </body></html>"""

    fixed, removed = deduplicate_candidate_html(candidate, source)

    assert removed == 1
    assert fixed.count('data-source-line="L0003"') == 1


def test_global_dedup_preserves_list_structure_and_legal_line_splits() -> None:
    source = "SKILLS\nAlpha Beta\nGamma"
    candidate = """<html><body><section data-source-block="block:L0001" data-source-heading="L0001">
      <h2 data-source-line="L0001">SKILLS</h2>
      <ul><li data-source-line="L0002">Alpha</li><li data-source-line="L0003">Gamma</li></ul>
      <ul><li data-source-line="L0002">Beta</li><li data-source-line="L0003">Gamma</li></ul>
      <ul><li data-source-line="L0003">Gamma</li></ul>
    </section></body></html>"""

    fixed, removed = deduplicate_candidate_html(candidate, source)

    assert removed == 2
    assert fixed.count("<ul>") == 3
    assert fixed.count('data-source-line="L0002"') == 2
    assert fixed.count('data-source-line="L0003"') == 1


def test_global_dedup_prefers_copy_inside_authoritative_section() -> None:
    source = "HIGHLIGHTS\nGamma\nWORK EXPERIENCE\nCompany"
    candidate = """<html><body>
      <section data-source-block="block:L0003"><ul><li id="wrong" data-source-line="L0002" data-source-block="block:L0001">Gamma</li></ul></section>
      <section data-source-block="block:L0001"><ul><li id="right" data-source-line="L0002">Gamma</li></ul></section>
    </body></html>"""

    fixed, removed = deduplicate_candidate_html(candidate, source)

    assert removed == 1
    assert 'id="wrong"' not in fixed
    assert 'id="right"' in fixed
    assert fixed.count("<ul>") == 2


def test_filler_cannot_change_template_presentation() -> None:
    template = '<html><style>.name { font-size: 20px; }</style><body><p class="name">[NAME]</p></body></html>'
    candidate = '<html><style>.name { font-size: 12px; }</style><body><p class="name">Jane</p></body></html>'

    assert validate_filler_conformance(template, candidate) == ["Filler modified or added <style> content"]


def test_builder_applies_one_evidence_backed_style_change() -> None:
    template = '<html><style>.name { font-size: 12pt; }</style><body><p class="name">[NAME]</p></body></html>'
    filled = template.replace("[NAME]", "Jane")
    measurements = [{
        "measurement_ref": "style_1.font_size_pt",
        "value": 18,
        "unit": "pt",
        "provenance": {"source": "target"},
    }]
    operation = validate_builder_operation(
        {
            "action": "set_style_token",
            "selector": ".name",
            "property": "font-size",
            "measurement_ref": "style_1.font_size_pt",
        },
        template,
        measurements,
    )
    state = CandidateState(template, filled, hashlib.sha256(template.encode()).hexdigest(), [])

    changed = apply_builder_operation(state, operation, measurements)

    assert ".name { font-size: 18pt; }" in changed.template_html
    assert ".name { font-size: 18pt; }" in changed.filled_html
    assert len(changed.builder_changes) == 1


def test_builder_rejects_unmeasured_values() -> None:
    with pytest.raises(ValueError, match="unresolved"):
        validate_builder_operation(
            {
                "action": "set_style_token",
                "selector": ".name",
                "property": "font-size",
                "measurement_ref": "style_1.font_size_pt",
            },
            '<style>.name { font-size: 12pt; }</style>',
            [],
        )


def test_deterministic_source_coverage_replaces_l0_model_call() -> None:
    assert _missing_source_tokens("Jane Candidate\nPython SQL", "Jane Candidate Python SQL") == []
    assert _missing_source_tokens("Jane Candidate\nPython SQL", "Jane Candidate Python") == ["sql"]


def test_header_element_renames_builder_div_header() -> None:
    # Owner decision 2026-09-09 (E->D postmortem, re-applied after slimming):
    # the Builder may render the contact area as <div class="header"> but the
    # gate requires a real <header>; renaming the tag is deterministic,
    # idempotent, and class selectors keep matching.
    from tests.experiments.fill_plan import normalize_header_element

    template = (
        '<html><body><div class="header" data-section="header">'
        '<div class="name" data-slot="name">[FULL NAME]</div>'
        '<div class="contact" data-slot="contact"><span>[PHONE]</span></div>'
        '</div><div class="section" data-section="summary"></div></body></html>'
    )
    fixed = normalize_header_element(template)
    assert '<header class="header" data-section="header">' in fixed
    assert "</header>" in fixed
    assert normalize_header_element(fixed) == fixed  # idempotent
    # A real <header> anywhere means no rename; no div.header means no-op.
    with_header = template.replace('<div class="header"', '<header class="header"').replace(
        '</div><div class="section"', '</header><div class="section"'
    )
    assert normalize_header_element(with_header) == with_header
    assert normalize_header_element("<html><body><p>x</p></body></html>") == "<html><body><p>x</p></body></html>"


def test_section_headings_injected_verbatim_when_filler_drops_them() -> None:
    # Owner decision 2026-09-09 (F->D postmortem): the Filler renders section
    # bodies but drops the source heading line; code writes it verbatim into
    # the unannotated heading slot (or clones the template prototype when the
    # heading element is gone). Never re-cased (codex review); all-caps look
    # is delegated to the marked text-transform rule.
    from tests.experiments.fill_plan import append_heading_case_rule, normalize_section_headings

    template = (
        '<html><head><style>h2 { color: #111; }</style></head><body>'
        '<div class="section" data-section="skills"><h2 class="section-heading">TECHNICAL SKILLS</h2>'
        '<p>[SOURCE TEXT]</p></div>'
        '</body></html>'
    )
    source = "SUMMARY\ns\nTECHNICAL SKILLS\nLanguages: Python"
    candidate = (
        '<html><body>'
        '<div class="section" data-section="skills" data-source-block="block:L0003" data-source-heading="L0003">'
        '<p data-source-line="L0004">Languages: Python</p>'
        '</div>'
        '</body></html>'
    )
    ruled = append_heading_case_rule(template)
    assert "text-transform: uppercase" in ruled
    assert append_heading_case_rule(ruled) == ruled  # idempotent
    fixed = normalize_section_headings(candidate, ruled, source)
    injected = re.search(r'<div class="section"[^>]*><h2 ([^>]*)>([^<]*)</h2>', fixed)
    assert injected and injected.group(2) == "TECHNICAL SKILLS"  # verbatim
    assert 'data-source-line="L0003"' in injected.group(1) and 'section-heading' in injected.group(1)
    assert 'data-text-transform="uppercase"' in fixed  # all-caps look via CSS
    assert 'data-source-line="L0004">Languages: Python' in fixed  # body untouched
    assert normalize_section_headings(fixed, ruled, source) == fixed  # idempotent


def test_heading_element_claiming_the_annotation_is_annotated_in_place() -> None:
    # Owner ruling 2026-09-11 (E→F final rerun): the Filler may echo the
    # section's annotations onto the heading element itself. That element's
    # text is the heading line verbatim exactly once, so the uniquely
    # unambiguous normalization annotates it in place — cloning a heading
    # inside it duplicates the source line (verbatim gates reject the
    # doubled rendering).
    from tests.experiments.fill_plan import normalize_section_headings

    source = "Skills\nLanguages: Python"
    candidate = (
        '<html><body>'
        '<div class="section" data-section="skills">'
        '<h2 data-slot="heading" data-source-block="block:L0001" data-source-heading="L0001">Skills</h2>'
        '<p data-source-line="L0002">Languages: Python</p>'
        '</div>'
        '</body></html>'
    )
    fixed = normalize_section_headings(candidate, candidate, source)
    assert ">Skills<h2" not in fixed  # no nested heading clone
    assert fixed.count("Skills") == 1  # heading text exactly once
    h2 = re.search(r"<h2 ([^>]*)>([^<]*)</h2>", fixed)
    assert h2 and h2.group(2) == "Skills"  # verbatim source line
    assert 'data-source-line="L0001"' in h2.group(1)  # annotated in place
    assert "data-text-transform" not in h2.group(1)  # not all-caps source
    assert 'data-source-line="L0002">Languages: Python' in fixed  # body untouched
    assert normalize_section_headings(fixed, candidate, source) == fixed  # idempotent
