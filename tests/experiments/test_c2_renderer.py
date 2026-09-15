"""C2-0b focused tests: deterministic RenderPlan compiler, HTML renderer, gates.

Offline lane (default): a synthetic state + an authored candidate render
context exercise compile/determinism/isolation/fail-closed behavior without
Chrome, the corpus, or the network. The ``local_dataset`` lane runs the
frozen C1 pairs end to end through the real pinned Chrome exporter (still no
live provider call — the target evidence comes from the persistent cache).

Run:

    pytest tests/experiments/test_c2_renderer.py -m "not local_dataset"
    pytest tests/experiments/test_c2_renderer.py -m local_dataset
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

import tests.experiments.c2_renderer as renderer_module
from tests.experiments.c2_pipeline import (
    C2LayoutState,
    CandidateDocument,
    CandidateLeaf,
    CandidateSection,
    SectionContent,
    UnroutableContent,
    state_bytes,
)
from tests.experiments.c2_renderer import (
    OVERFLOW_POLICY,
    C2RenderPlan,
    blank_page_gate,
    candidate_accounting_gate,
    compile_render_plan,
    content_gate,
    content_shape_verification,
    privacy_gate,
    render_html,
    structure_gate,
)
from tests.experiments.test_c2_pipeline import (
    BODY,
    BULLET_TIERS,
    FULL_COVERAGE_LABELS,
    SUMMARY,
    compile_synthetic,
)


@pytest.fixture()
def fake_pdf_text(monkeypatch: pytest.MonkeyPatch):
    """Replace PDF text extraction with a canned string (offline gate tests)."""

    def _install(text: str) -> str:
        monkeypatch.setattr(renderer_module, "read_pdf_text", lambda _path: text)
        return text

    return _install


# ---------------------------------------------------------------------------
# Synthetic render context (authored, independent of the compiled state)
# ---------------------------------------------------------------------------


def _leaf(leaf_id: str, kind: str, source: str | None = None, **kwargs: object) -> CandidateLeaf:
    if "parent" in kwargs:
        kwargs["parent_leaf_id"] = kwargs.pop("parent")
    return CandidateLeaf(leaf_id=leaf_id, kind=kind, source=source, **kwargs)  # type: ignore[arg-type]


def rich_candidate(include_unmatched: bool = True) -> CandidateDocument:
    """A full structured render context with text, mirroring the C2-0b shape."""
    leaves = [
        _leaf("header.name", "header_field", slot="name", text="CANDNAME Doe"),
        _leaf("header.location", "header_field", slot="location", text="CANDCITY, ST"),
        _leaf("header.phone", "header_field", slot="phone", text="(000) 000-0000"),
        _leaf("header.envelope", "header_field", slot="envelope", text="cand@example.com"),
        _leaf("header.github", "header_field", slot="github", text="github.com/cand"),
        _leaf("header.linkedin", "header_field", slot="linkedin", text="linkedin.com/in/cand"),
        _leaf("summary.p1", "summary_paragraph", "summary", text="SUMMARY — candidate paragraph."),
        _leaf("skills.g1", "skill_group", "skills", text="Group One"),
        _leaf("skills.g1.i1", "skill", "skills", parent="skills.g1", text="Skill A"),
        _leaf("skills.g1.i2", "skill", "skills", parent="skills.g1", text="Skill B"),
        _leaf("work.e1", "work_entry", "work_experience", text="CANDCORP One"),
        _leaf("work.e1.role", "entry_detail", "work_experience", parent="work.e1", text="Engineer"),
        _leaf("work.e1.m1", "entry_meta", "work_experience", parent="work.e1", text="Candtown, CA"),
        _leaf("work.e1.m2", "entry_meta", "work_experience", parent="work.e1", text="2020 – Present"),
        _leaf("work.e1.b1", "work_bullet", "work_experience", parent="work.e1", text="• Did candidate work"),
        _leaf("work.e2", "work_entry", "work_experience", text="CANDCORP Two"),
        _leaf("work.e2.m1", "entry_meta", "work_experience", parent="work.e2", text="2015 – 2020"),
        _leaf("education.e1", "education_entry", "education", text="CAND University"),
        _leaf("education.e1.d1", "entry_detail", "education", parent="education.e1", text="B.S. Candidate Studies"),
        _leaf("education.e1.m1", "entry_meta", "education", parent="education.e1", text="2011 – 2015"),
    ]
    if include_unmatched:
        leaves += [
            _leaf("hobby.i1", "additional_item", "additional_details", text="CANDHOBIES item"),
            _leaf("hobby.i2", "additional_item", "additional_details", text="Second candidate hobby"),
        ]
    sections = [
        CandidateSection(section_id="summary", heading=None, source="summary",
                         content_kind="paragraph", leaf_ids=["summary.p1"]),
        CandidateSection(section_id="skills", heading="CAND SKILLS", source="skills",
                         content_kind="item_list", leaf_ids=["skills.g1", "skills.g1.i1", "skills.g1.i2"]),
        CandidateSection(section_id="work", heading="CAND WORK", source="work_experience",
                         content_kind="entries",
                         leaf_ids=["work.e1", "work.e1.role", "work.e1.m1", "work.e1.m2",
                                   "work.e1.b1", "work.e2", "work.e2.m1"]),
        CandidateSection(section_id="education", heading="CAND EDUCATION", source="education",
                         content_kind="entries",
                         leaf_ids=["education.e1", "education.e1.d1", "education.e1.m1"]),
    ]
    if include_unmatched:
        sections.append(
            CandidateSection(section_id="hobbies", heading="CAND HOBBIES",
                             source="additional_details", content_kind="item_list",
                             leaf_ids=["hobby.i1", "hobby.i2"])
        )
    return CandidateDocument(candidate_id="synthetic-rich", leaves=leaves, sections=sections)


def plan_for(labels: list[str] | None = None, include_unmatched: bool = True) -> tuple[C2LayoutState, C2RenderPlan]:
    state = compile_synthetic(labels) if labels else compile_synthetic()
    candidate = rich_candidate(include_unmatched=include_unmatched)
    return state, compile_render_plan(state, candidate)


# ---------------------------------------------------------------------------
# Determinism and structure
# ---------------------------------------------------------------------------


def test_deterministic_state_and_candidate_produce_identical_plan_and_html() -> None:
    state, plan = plan_for()
    again_state, again_plan = plan_for()
    assert json.loads(plan.model_dump_json()) == json.loads(again_plan.model_dump_json())
    assert state_bytes(state) == state_bytes(again_state)
    assert render_html(state, plan) == render_html(again_state, again_plan)
    assert state_bytes(state) == state_bytes(compile_synthetic())  # state untouched


def test_plan_status_and_full_leaf_ownership_on_full_coverage() -> None:
    state, plan = plan_for()
    assert plan.status == "fully_materialized"
    assert plan.failures == [] and plan.unhomed == []
    candidate = rich_candidate()
    body_leaf_ids = {leaf.leaf_id for leaf in candidate.leaves if leaf.kind != "header_field"}
    assert body_leaf_ids <= set(plan.leaf_ledger)
    assert len(plan.leaf_ledger) == len(candidate.leaves)


def test_no_seed_html_is_read_or_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    # The renderer neither imports nor executes any C1 seed machinery, and the
    # compiled HTML carries no C1 lineage.
    import tests.experiments.a_pipeline as a_pipeline
    for symbol in ("_seed_template", "compile_header_template", "_body_css"):
        monkeypatch.setattr(a_pipeline, symbol, pytest.fail, raising=False)
    state, plan = plan_for()
    html = render_html(state, plan)
    source = Path(renderer_module.__file__).read_text(encoding="utf-8")
    assert "candidate_html" not in source
    assert "_seed_template" not in source and "compile_header_template" not in source
    assert "data-c1-" not in html and "c1-dna" not in html
    assert "<img" not in html and "background-image" not in html


def test_target_candidate_facts_cannot_enter_html_or_pdf(fake_pdf_text, monkeypatch) -> None:
    state, plan = plan_for()
    html = render_html(state, plan)
    target_lines = [
        "TARGETFACT Corp", "targetfact-role", "TARGETFACT@corp.example",
        "SUMMARY",  # a rendered label: excluded from the privacy check
    ]
    fake_pdf_text("\n".join(target_lines))
    monkeypatch.setattr(renderer_module, "_html_text", _identity_html_text)
    result = privacy_gate(plan, Path("unused.pdf"), html, Path("unused.pdf"))
    assert result["passed"] is False  # the fake PDF text carries the facts
    leaked = {
        record["line"]
        for record in result["checked_target_lines"]
        if record["in_html"] or record["in_pdf"]
    }
    assert {"TARGETFACT Corp", "targetfact-role", "TARGETFACT@corp.example"} <= leaked
    assert "SUMMARY" not in leaked  # rendered template label, not a leaked fact
    assert "summary" in result["excluded_labels"]
    # And none of the target facts are present in the rendered HTML at all.
    text = _strip_tags(html)
    for fact in ("TARGETFACT Corp", "targetfact-role", "TARGETFACT@corp.example"):
        assert fact not in text


def _identity_html_text(document: str) -> tuple[str, list[str]]:
    return _strip_tags(document), []


def _strip_tags(document: str) -> str:
    class _P(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self.parts.append(data)

    parser = _P()
    parser.feed(document)
    return re.sub(r"\s+", " ", " ".join(parser.parts))


def test_every_candidate_leaf_has_exactly_one_html_destination(fake_pdf_text) -> None:
    state, plan = plan_for()
    html = render_html(state, plan)
    fake_pdf_text("\n".join(_all_leaf_texts(plan)))
    gate = content_gate(plan, html, Path("unused.pdf"))
    assert gate["passed"], gate
    assert all(record["element_identity_count"] == 1 for record in gate["leaf_records"].values())


def _all_leaf_texts(plan: C2RenderPlan) -> list[str]:
    texts: list[str] = []
    if plan.header_overflow is not None:
        texts.extend(field.text for field in plan.header_overflow.fields)
    for row in plan.header_rows:
        texts.extend(field.text for field in row.fields)
    for section in [*plan.sections, *plan.appended_sections]:
        texts.extend(line.text for line in [*section.paragraph_lines, *section.items])
        for entry in section.entries:
            for line in [*entry.title_lines, *entry.meta_lines, *entry.bullet_items, *entry.text_lines]:
                texts.append(line.text)
    return texts


def test_missing_and_duplicated_leaves_fail() -> None:
    # Missing: a candidate body leaf outside any declared section cannot even
    # form a render context (fail closed before rendering).
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="outside any section"):
        CandidateDocument(
            candidate_id="orphan",
            leaves=[
                _leaf("covered.item", "additional_item", "additional_details", text="Covered"),
                _leaf("orphan.item", "additional_item", "additional_details", text="Orphan"),
            ],
            sections=[
                CandidateSection(section_id="work", heading="W", source="additional_details",
                                 content_kind="item_list", leaf_ids=["covered.item"]),
            ],
        )
    # A leaf that renders twice trips the ownership ledger guard.
    from tests.experiments.c2_pipeline import own_leaf

    ledger: dict[str, str] = {}
    failures: list[str] = []
    assert own_leaf(ledger, failures, "work.e1", "section.01.entry") is True
    assert own_leaf(ledger, failures, "work.e1", "section.02.entry") is False
    assert failures and "consumed more than once" in failures[0]


def test_section_local_structure_is_isolated_and_counterfactual_stable() -> None:
    state, plan = plan_for(["WORK EXPERIENCE", "EDUCATION"], include_unmatched=False)
    html = render_html(state, plan)
    # Counterfactual: move Work's entry column; Education's compiled structure
    # must not change by a single byte.
    mutated = state.model_copy(deep=True)
    for index, node in enumerate(mutated.nodes):
        if node.node_id == "section.01.entry":
            mutated.nodes[index] = node.model_copy(
                update={"columns": [node.columns[0].model_copy(update={"x0_pt": 99.0})]}
            )
    assert mutated.nodes != state.nodes  # the mutation happened
    mutated_plan = compile_render_plan(mutated, rich_candidate(include_unmatched=False))
    mutated_html = render_html(mutated, mutated_plan)
    education_html = _section_html(html, "section.02")
    education_html_mutated = _section_html(mutated_html, "section.02")
    assert education_html == education_html_mutated
    assert "margin-left: 63pt" in _section_html(mutated_html, "section.01")  # mutation landed


def _section_html(html: str, node_id: str) -> str:
    start = html.index(f'data-node-id="{node_id}"')
    start = html.rindex("<section", 0, start)
    end = html.index("</section>", start) + len("</section>")
    return html[start:end]


def test_candidate_only_sections_append_after_target_sections_with_source_headings() -> None:
    state, plan = plan_for(["WORK EXPERIENCE", "EDUCATION"])  # skills/summary/hobbies unmatched
    assert OVERFLOW_POLICY == "append_after_template_with_source_heading"
    assert [section.node_id for section in plan.appended_sections] == [
        "candidate_only.summary", "candidate_only.skills", "candidate_only.hobbies",
    ]
    html = render_html(state, plan)
    positions = {
        node_id: html.index(f'data-node-id="{node_id}"')
        for node_id in ("section.01", "section.02", "candidate_only.summary", "candidate_only.hobbies")
    }
    target_end = html.index("</section>", positions["section.02"])
    assert all(position > target_end for node_id, position in positions.items() if node_id.startswith("candidate_only"))
    # Appended sections retain their source headings (rendered from the state's
    # measured heading style token, never candidate typography).
    assert ">CAND SKILLS<" in html and ">CAND HOBBIES<" in html
    assert 'data-overflow-policy="append_after_template_with_source_heading"' in html


def test_target_section_order_is_preserved(fake_pdf_text) -> None:
    labels = ["SUMMARY", "TECHNICAL SKILLS", "WORK EXPERIENCE", "EDUCATION"]
    state, plan = plan_for(labels, include_unmatched=False)
    assert [section.label for section in plan.sections if not section.empty] == labels
    html = render_html(state, plan)
    h2_sequence = re.findall(r"<h2[^>]*>([^<]*)</h2>", html)
    assert h2_sequence == labels
    fake_pdf_text(" ".join(labels))
    gate = structure_gate(plan, state, html, Path("unused.pdf"))
    assert gate["section_order_matches_state"] is True
    assert gate["expected_section_order"] == labels


def test_body_nodes_compile_without_absolute_y_positioning(fake_pdf_text) -> None:
    state, plan = plan_for()
    html = render_html(state, plan)
    assert not re.search(r"position\s*:\s*(absolute|fixed|relative)", html)
    # And the gate flags it if introduced.
    poisoned = html.replace(".c2-section { }", ".c2-section { position: absolute; }")
    fake_pdf_text(" ")
    gate = structure_gate(plan, state, poisoned, Path("unused.pdf"))
    assert gate["body_absolute_or_fixed_positioning"] is True
    assert gate["passed"] is False


def test_unsupported_and_nonmaterializable_composite_fail_closed() -> None:
    state, _ = plan_for()

    def work_section(mutated: C2LayoutState) -> int:
        return next(
            index
            for index, node in enumerate(mutated.nodes)
            if node.kind == "section" and node.binding
            and node.binding.sources == ["work_experience"]
            and node.binding.mapping_action == "map"
        )

    broken = state.model_copy(deep=True)
    index = work_section(broken)
    node = broken.nodes[index]
    broken.nodes[index] = node.model_copy(
        update={"content": SectionContent(content_kind="unsupported", sources=["work_experience"])}
    )
    plan = compile_render_plan(broken, rich_candidate(include_unmatched=False))
    assert plan.status == "failed"
    assert any("unsupported content" in failure for failure in plan.failures)
    with pytest.raises(RuntimeError, match="refusing to render a failed plan"):
        render_html(broken, plan)

    # C2-0cM: a composite whose sub-contents are all materializable now
    # compiles and renders BOTH sources under the one measured heading.
    # (The default state also maps a separate EDUCATION section, which would
    # double-bind the source; drop it — one candidate source, one destination.)
    composite = plan_for(
        [label for label in FULL_COVERAGE_LABELS if label != "EDUCATION"],
        include_unmatched=False,
    )[0].model_copy(deep=True)
    from tests.experiments.c2_pipeline import SectionBinding

    index = work_section(composite)
    node = composite.nodes[index]
    composite.nodes[index] = node.model_copy(
        update={
            "binding": SectionBinding(
                sources=["work_experience", "education"], mapping_action="map",
                composite=True, evidence_ids=node.binding.evidence_ids if node.binding else ["e"],
            ),
            "content": SectionContent(
                content_kind="composite", sources=["work_experience", "education"],
                sub_contents=[
                    SectionContent(content_kind="entries", sources=["work_experience"]),
                    SectionContent(content_kind="entries", sources=["education"]),
                ],
            ),
        }
    )
    plan = compile_render_plan(composite, rich_candidate(include_unmatched=False))
    assert plan.status == "fully_materialized", plan.failures
    composite_plan = next(s for s in plan.sections if s.content_kind == "composite")
    assert {entry.entry_leaf_id for entry in composite_plan.entries} >= {
        "work.e1", "work.e2", "education.e1",
    }
    html = render_html(composite, plan)
    assert "CANDCORP One" in html and "CAND University" in html

    # A composite sub-content without a proven materialization stays fail-closed.
    unsupported_sub = composite.model_copy(deep=True)
    unsupported_sub.nodes[index] = unsupported_sub.nodes[index].model_copy(
        update={
            "content": SectionContent(
                content_kind="composite", sources=["work_experience", "education"],
                sub_contents=[
                    SectionContent(content_kind="entries", sources=["work_experience"]),
                    SectionContent(content_kind="unsupported", sources=["education"]),
                ],
            ),
        }
    )
    plan = compile_render_plan(unsupported_sub, rich_candidate(include_unmatched=False))
    assert plan.status == "failed"
    assert any("fail-closed" in failure for failure in plan.failures)


def test_empty_target_sections_render_nothing_but_are_recorded() -> None:
    # Full-coverage state; the candidate has no certifications content.
    state, plan = plan_for()
    certifications = next(
        section for section in plan.sections if section.source_role == "certifications"
    )
    assert certifications.empty is True
    html = render_html(state, plan)
    assert "CERTIFICATIONS" not in _strip_tags(html)
    assert any("no candidate content" in note for note in plan.notes)


def _shape_scaffold(labels: list[str] | None = None) -> Any:
    from tests.experiments.c_pipeline import BodyEntryScaffold, BodyScaffold
    from tests.experiments.test_c2_pipeline import _heading

    return BodyScaffold(
        headings=[
            _heading(label, 140.0 + 120.0 * index, f"e.heading.{index + 1}")
            for index, label in enumerate(labels or ["WORK EXPERIENCE"])
        ],
        entry=BodyEntryScaffold(
            left_x0_pt=46.9, right_x1_pt=576.0, right_row_top_delta_pt=0.0,
            evidence_ids=["e.entry"],
        ),
        contact_icons_present=False, contact_separator="|",
    )


def _shape_summary() -> dict:
    """Synthetic summary carrying an above-heading rule (whose measured bbox
    differs from the heading text bounds) and measured entry-block elements."""
    summary = json.loads(json.dumps(SUMMARY))
    summary["rules"] = [
        {
            "page_number": 1,
            "bbox": {"x0": 72.0 / 612.0, "top": 133.0 / 792.0, "x1": 400.0 / 612.0},
            "stroke_width_pt": 0.75,
            "color_hex": "#111827",
            "gap_above_pt": 6.0,
            "gap_below_pt": 4.0,
            "element_id": "syn.rule.1",
        }
    ]
    summary["elements"] = [
        *summary["elements"],
        # Measured entry block one: title (bold tier), meta row, detail, bullet.
        {"page": 1, "entry_path": "work/e1", "style_id": "style_1", "bbox_pt": {"x0": 46.9, "top": 200.0, "bottom": 215.2}, "text_sample": "CANDCORP One"},
        {"page": 1, "entry_path": "work/e1", "style_id": "style_2", "bbox_pt": {"x0": 500.0, "top": 200.0, "bottom": 215.0}, "text_sample": "Candtown, CA"},
        {"page": 1, "entry_path": "work/e1", "style_id": "style_2", "bbox_pt": {"x0": 46.9, "top": 217.2, "bottom": 232.2}, "text_sample": "CANDROLE Engineer"},
        {"page": 1, "entry_path": "work/e1", "style_id": "style_2", "bbox_pt": {"x0": 70.0, "top": 240.0, "bottom": 255.0}, "text_sample": "• Did candidate work indeed"},
        # Measured entry block two: the rhythm gap is measured between blocks.
        {"page": 1, "entry_path": "work/e2", "style_id": "style_1", "bbox_pt": {"x0": 46.9, "top": 260.0, "bottom": 275.2}, "text_sample": "CANDCORP Two"},
        {"page": 1, "entry_path": "work/e2", "style_id": "style_2", "bbox_pt": {"x0": 500.0, "top": 260.0, "bottom": 275.0}, "text_sample": "2015 – 2020"},
    ]
    return summary


def _shape_pair() -> tuple[C2LayoutState, C2RenderPlan, str, dict, Any]:
    state = compile_synthetic(rules=_shape_summary()["rules"], elements=_shape_summary()["elements"])
    candidate = rich_candidate(include_unmatched=False)
    plan = compile_render_plan(state, candidate)
    html = render_html(state, plan)
    return state, plan, html, _shape_summary(), _shape_scaffold()


def _rule_line_pdf(path: Path, x0: float, x1: float) -> Path:
    """A real one-page PDF with one horizontal rule at the given x-extent."""
    from reportlab.pdfgen import canvas

    document = canvas.Canvas(str(path))
    document.setLineWidth(0.4)
    document.line(x0, 300, x1, 300)
    document.showPage()
    document.save()
    return path


def _heading_rule_pdf(path: Path, items: list[dict]) -> Path:
    """A real PDF with rendered heading text lines and rules at measured
    positions: each item carries ``label`` and ``heading_top_pt`` plus an
    optional ``rule`` {x0, x1, top}. Rendered rule objects therefore carry
    page AND vertical position, like the exported Chrome PDFs."""
    from reportlab.pdfgen import canvas

    document = canvas.Canvas(str(path))
    for item in items:
        document.setFont("Helvetica", 10)
        document.drawString(72.0, 792.0 - item["heading_top_pt"] - 7.2, item["label"])
        rule = item.get("rule")
        if rule:
            document.setLineWidth(0.4)
            document.line(rule["x0"], 792.0 - rule["top"], rule["x1"], 792.0 - rule["top"])
    document.showPage()
    document.save()
    return path


def test_content_shapes_are_verified_against_measured_evidence() -> None:
    from tests.experiments.c_pipeline import BodyEntryScaffold, BodyScaffold
    from tests.experiments.test_c2_pipeline import _heading

    state, plan = plan_for()
    html = render_html(state, plan)
    scaffold = BodyScaffold(
        headings=[_heading("WORK EXPERIENCE", 140.0, "e.heading")], entry=BodyEntryScaffold(
            left_x0_pt=46.9, right_x1_pt=576.0, right_row_top_delta_pt=0.0,
            evidence_ids=["e.entry"],
        ),
        contact_icons_present=False, contact_separator="|",
    )
    # Entry typography/inter-entry rhythm/content style values the state does
    # not carry: the shape gate must stay FALSE and name the capability gaps
    # (truthful, never a placeholder-true hard gate).
    result = content_shape_verification(
        state, plan, scaffold, dict(BULLET_TIERS), html, json.loads(json.dumps(SUMMARY))
    )
    assert result["passed"] is False
    gaps = {
        row["capability_gap"]
        for row in (property_row for entry in result["rows"] for property_row in entry["properties"])
    }
    assert any(gaps)
    # Bullet design declared but no measured dot: inconsistent.
    broken = content_shape_verification(
        state, plan, scaffold, {}, html, json.loads(json.dumps(SUMMARY))
    )
    assert broken["passed"] is False
    assert any(
        row["capability_gap"]
        for row in broken["rows"]
        for row in row["properties"]
    )


def test_rule_verification_resolves_the_measured_rule_bbox() -> None:
    """Evidence-boundary regression: the state rule stores the matched rule's
    OWN measured bbox (never the heading text bounds)."""
    from tests.experiments.c2_pipeline import _resolve_above_heading_rule
    from tests.experiments.test_c2_pipeline import _heading

    heading = _heading("WORK EXPERIENCE", 140.0, "e.heading")  # text x1 = 171
    summary = _shape_summary()
    rule, evidence = _resolve_above_heading_rule(1, heading, summary, 792.0, 612.0)
    assert rule is not None and evidence is not None
    # The rule's real measured x-extent, NOT the heading text bounds.
    assert (rule.x0_pt, rule.x1_pt) == (72.0, 400.0)
    assert (evidence["x0_pt"], evidence["x1_pt"]) == (72.0, 400.0)
    assert rule.placement == "above_heading"


def test_rule_geometry_is_verified_in_the_rendered_output(tmp_path: Path) -> None:
    """Regression for the short-rule D→E output: a rendered rule whose x-extent
    does not match the measured bbox must FAIL the shape gate."""
    state = compile_synthetic(
        ["WORK EXPERIENCE"], rules=_shape_summary()["rules"], elements=_shape_summary()["elements"]
    )
    candidate = rich_candidate(include_unmatched=False)
    plan = compile_render_plan(state, candidate)
    html = render_html(state, plan)
    summary = _shape_summary()
    scaffold = _shape_scaffold()

    # The measured rule (72→400, top 133) rendered at the measured extent on
    # the heading's page and in its vertical region: gate passes.
    measured_pdf = _heading_rule_pdf(
        tmp_path / "measured.pdf",
        [{"label": "WORK EXPERIENCE", "heading_top_pt": 140.0,
          "rule": {"x0": 72.0, "x1": 400.0, "top": 133.0}}],
    )
    result = content_shape_verification(
        state, plan, scaffold, dict(BULLET_TIERS), html, summary, measured_pdf
    )
    rule_rows = [
        row
        for entry in result["rows"]
        for row in entry["properties"]
        if row["property"] == "rule"
    ]
    assert len(rule_rows) == 1
    assert rule_rows[0]["required_by_declared_shape"] is True
    assert rule_rows[0]["capability_gap"] is None, rule_rows[0]
    assert result["passed"] is True

    # The short-rule geometry (the pre-fix D→E output): the rendered rule does
    # NOT span the measured bbox — the gate must FAIL.
    short_pdf = _heading_rule_pdf(
        tmp_path / "short.pdf",
        [{"label": "WORK EXPERIENCE", "heading_top_pt": 140.0,
          "rule": {"x0": 36.0, "x1": 106.7, "top": 133.0}}],
    )
    broken = content_shape_verification(
        state, plan, scaffold, dict(BULLET_TIERS), html, summary, short_pdf
    )
    broken_rules = [
        row
        for entry in broken["rows"]
        for row in entry["properties"]
        if row["property"] == "rule"
    ]
    assert broken["passed"] is False
    assert any(row["capability_gap"] for row in broken_rules)
    # And the failed check records the rendered extents it found.
    assert broken_rules[0]["measured"]["rendered_rule_extents"]


def test_typography_consumption_is_element_scoped(tmp_path: Path) -> None:
    """A class name that only exists inside <style> is NOT renderer
    consumption: title/detail/meta/content classes must sit on the correct
    semantic nodes, and the meta tier is part of the consumed result."""
    state = compile_synthetic(
        ["WORK EXPERIENCE"], rules=_shape_summary()["rules"], elements=_shape_summary()["elements"]
    )
    candidate = rich_candidate(include_unmatched=False)
    plan = compile_render_plan(state, candidate)
    html = render_html(state, plan)
    summary = _shape_summary()
    scaffold = _shape_scaffold()
    # The section carries a required rule, so the gate also consumes its
    # rendered vector object from the exported PDF (measured extent 72→400
    # in the heading's vertical region).
    measured_pdf = _heading_rule_pdf(
        tmp_path / "typography.pdf",
        [{"label": "WORK EXPERIENCE", "heading_top_pt": 140.0,
          "rule": {"x0": 72.0, "x1": 400.0, "top": 133.0}}],
    )
    result = content_shape_verification(
        state, plan, scaffold, dict(BULLET_TIERS), html, summary, measured_pdf
    )
    assert result["passed"] is True, result
    by_property = {
        row["property"]: row
        for entry in result["rows"]
        for row in entry["properties"]
    }
    # Every tier is required and consumed on its correct semantic nodes.
    for name in ("entry_typography", "inter_entry_rhythm", "content_typography"):
        assert by_property[name]["required_by_declared_shape"] is True, name
        assert by_property[name]["capability_gap"] is None, (name, by_property[name])
    tiers = by_property["entry_typography"]["measured"]
    assert tiers == {"title": True, "detail": True, "meta": True}
    # Element-scoped proof: strip the classes from the rendered ELEMENTS (the
    # <style> definitions remain) and the gate must fail.
    stripped = re.sub(r'class="c2-style-[a-z0-9_]+"', "", html)
    stripped_result = content_shape_verification(
        state, plan, scaffold, dict(BULLET_TIERS), stripped, summary
    )
    assert stripped_result["passed"] is False


# ---------------------------------------------------------------------------
# Candidate-content accounting (owner corrective pass)
# ---------------------------------------------------------------------------


def candidate_with_unroutable(disposition: str, slot: str | None = None) -> CandidateDocument:
    candidate = rich_candidate(include_unmatched=False)
    leaves = [leaf for leaf in candidate.leaves if leaf.leaf_id != "header.location"]
    unroutable = UnroutableContent(
        text="CANDCITY, ST",
        reason="no measured header row carries a location",
        before_leaf_id="header.phone",
        disposition=disposition,  # type: ignore[arg-type]
        slot=slot,
    )
    return candidate.model_copy(update={"leaves": leaves, "unroutable": [unroutable]})


def test_unroutable_render_content_routes_through_the_header_overflow_node(fake_pdf_text) -> None:
    # Truthful accounting: render-disposition unroutables are NOT excluded
    # from the exactly-once/content-loss gate — they route through an explicit
    # candidate-only header-overflow plan node and are owned and verified.
    state = compile_synthetic()
    candidate = candidate_with_unroutable("render", slot="location")
    plan = compile_render_plan(state, candidate)
    assert plan.header_overflow is not None
    assert plan.leaf_ledger["unroutable.location"] == "header_overflow.location"
    assert plan.status == "fully_materialized"
    html = render_html(state, plan)
    assert "CANDCITY, ST" in html
    assert 'data-c2-candidate-only="true"' in html
    fake_pdf_text("\n".join([*_all_leaf_texts(plan)]))
    gate = content_gate(plan, html, Path("unused.pdf"))
    assert gate["passed"], gate["missing_pdf"]
    assert gate["leaf_records"]["unroutable.location"]["rendered_with_value"] is True
    accounting = candidate_accounting_gate(plan, gate)
    assert accounting["passed"] is True
    assert accounting["routed_header_overflow"] == ["unroutable.location"]


def test_explicit_omission_is_a_separate_disposition_never_rendered(fake_pdf_text) -> None:
    state = compile_synthetic()
    candidate = candidate_with_unroutable("omit")
    plan = compile_render_plan(state, candidate)
    assert plan.header_overflow is None
    assert "unroutable.location" not in plan.leaf_ledger
    assert [omission.text for omission in plan.explicit_omissions] == ["CANDCITY, ST"]
    html = render_html(state, plan)
    assert "CANDCITY" not in html  # omitted content is not rendered
    fake_pdf_text("\n".join(_all_leaf_texts(plan)))
    gate = content_gate(plan, html, Path("unused.pdf"))
    assert gate["passed"] is True
    accounting = candidate_accounting_gate(plan, gate)
    assert accounting["passed"] is True
    assert accounting["explicitly_omitted"][0]["text"] == "CANDCITY, ST"


def test_a_value_neither_rendered_nor_omitted_fails_the_accounting_gate() -> None:
    state = compile_synthetic()
    candidate = candidate_with_unroutable("omit")
    plan = compile_render_plan(state, candidate)
    # Simulate a run where a render-disposition unroutable was never routed:
    # the accounting gate must fail the run (no silent content loss).
    flipped = plan.model_copy(deep=True)
    flipped.explicit_omissions = []
    flipped.unroutable = [
        UnroutableContent(
            text="CANDCITY, ST",
            reason="no measured header row carries a location",
            before_leaf_id="header.phone",
            disposition="render",
            slot="location",
        )
    ]
    accounting = candidate_accounting_gate(flipped, {"passed": True})
    assert accounting["passed"] is False
    assert accounting["unresolved_unroutable"] == ["CANDCITY, ST"]


def test_a_render_disposition_without_a_slot_fails_at_authoring() -> None:
    with pytest.raises(Exception, match="overflow slot"):
        UnroutableContent(
            text="CANDCITY, ST",
            reason="no measured header row carries a location",
            before_leaf_id="header.phone",
            disposition="render",
            slot=None,
        )


# ---------------------------------------------------------------------------
# Blank-page gate (owner corrective pass: every page inspected independently)
# ---------------------------------------------------------------------------


def _reportlab_pdf(path: Path, pages: list[str | None]) -> Path:
    """A real multi-page PDF; a None page is left completely blank."""
    from reportlab.pdfgen import canvas

    document = canvas.Canvas(str(path))
    for text in pages:
        if text is not None:
            document.drawString(72, 720, text)
        document.showPage()
    document.save()
    return path


def test_blank_page_gate_passes_on_a_single_content_page(tmp_path: Path) -> None:
    pdf = _reportlab_pdf(tmp_path / "one_page.pdf", ["Real page one content"])
    result = blank_page_gate(pdf)
    assert result["passed"] is True
    assert result["pages_inspected"] == 1
    assert result["pages"][0]["meaningful_text"] is True


def test_blank_page_gate_fails_on_an_extra_blank_page(tmp_path: Path) -> None:
    # Regression: an exported document whose second page is empty must FAIL.
    pdf = _reportlab_pdf(
        tmp_path / "two_pages.pdf", ["Page one content", None]
    )
    result = blank_page_gate(pdf)
    assert result["passed"] is False
    assert result["blank_pages"] == [2]
    assert result["pages"][1]["meaningful_text"] is False


def test_blank_page_gate_accepts_a_rule_only_page(tmp_path: Path) -> None:
    # A page with no text but an approved visual object (a rule) is not blank.
    from reportlab.pdfgen import canvas

    path = tmp_path / "rule_only.pdf"
    document = canvas.Canvas(str(path))
    document.drawString(72, 720, "Page one content")
    document.showPage()
    document.setLineWidth(0.5)
    document.line(72, 400, 500, 400)
    document.showPage()
    document.save()
    result = blank_page_gate(path)
    assert result["passed"] is True
    assert result["pages"][1]["visual_objects"] > 0


def _two_rule_summary() -> dict:
    """Two same-extent rules (72→400), one per section heading (y 133 / 193,
    matching compile_synthetic's 60pt heading spacing)."""
    summary = _shape_summary()
    second = json.loads(json.dumps(summary["rules"][0]))
    second["bbox"]["top"] = 193.0 / 792.0
    second["element_id"] = "syn.rule.2"
    summary["rules"] = [summary["rules"][0], second]
    return summary


def _two_rule_scaffold() -> Any:
    """Scaffold matching compile_synthetic's 60pt heading spacing (the rule
    y positions in _two_rule_summary must resolve against these headings)."""
    from tests.experiments.c_pipeline import BodyEntryScaffold, BodyScaffold
    from tests.experiments.test_c2_pipeline import _heading

    return BodyScaffold(
        headings=[
            _heading(label, 140.0 + 60.0 * index, f"e.heading.{index + 1}")
            for index, label in enumerate(["WORK EXPERIENCE", "EDUCATION"])
        ],
        entry=BodyEntryScaffold(
            left_x0_pt=46.9, right_x1_pt=576.0, right_row_top_delta_pt=0.0,
            evidence_ids=["e.entry"],
        ),
        contact_icons_present=False, contact_separator="|",
    )


def _rule_rows(result: dict, section: str) -> list[dict]:
    return [
        row
        for entry in result["rows"] if entry["section"] == section
        for row in entry["properties"]
        if row["property"] == "rule"
    ]


def test_a_required_rule_needs_a_rendered_vector_object(tmp_path: Path) -> None:
    """A required rule with NO matching rendered vector object must FAIL the
    gate — an empty rendered-extent list can never pass (pre-fix loophole)."""
    state = compile_synthetic(
        ["WORK EXPERIENCE"], rules=_shape_summary()["rules"], elements=_shape_summary()["elements"]
    )
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    html = render_html(state, plan)
    from reportlab.pdfgen import canvas

    text_only_pdf = tmp_path / "no_rules.pdf"
    document = canvas.Canvas(str(text_only_pdf))
    document.drawString(72, 400, "body text only, no rule")
    document.showPage()
    document.save()
    result = content_shape_verification(
        state, plan, _shape_scaffold(), dict(BULLET_TIERS), html,
        _shape_summary(), text_only_pdf,
    )
    rule_row = _rule_rows(result, "section.01")[0]
    assert rule_row["required_by_declared_shape"] is True
    assert rule_row["capability_gap"] is not None
    assert result["passed"] is False
    # And passing no PDF at all must equally fail a required rule.
    no_pdf = content_shape_verification(
        state, plan, _shape_scaffold(), dict(BULLET_TIERS), html, _shape_summary()
    )
    assert no_pdf["passed"] is False


def test_one_rendered_rule_cannot_satisfy_two_required_section_rules(tmp_path: Path) -> None:
    """Two same-extent section rules require TWO rendered vector objects:
    one rendered rule is consumed by the first check; the second section's
    rule then has no un-consumed match and FAILS (insufficient count)."""
    summary = _two_rule_summary()
    state = compile_synthetic(
        ["WORK EXPERIENCE", "EDUCATION"], rules=summary["rules"], elements=summary["elements"]
    )
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    html = render_html(state, plan)
    # Only ONE rendered rule object at the shared extent, on page 1 in the
    # first section's vertical region.
    one_rule_pdf = _heading_rule_pdf(
        tmp_path / "one_rule.pdf",
        [
            {"label": "WORK EXPERIENCE", "heading_top_pt": 140.0,
             "rule": {"x0": 72.0, "x1": 400.0, "top": 133.0}},
            {"label": "EDUCATION", "heading_top_pt": 200.0},
        ],
    )
    result = content_shape_verification(
        state, plan, _two_rule_scaffold(), dict(BULLET_TIERS), html, summary, one_rule_pdf
    )
    first = _rule_rows(result, "section.01")[0]
    second = _rule_rows(result, "section.02")[0]
    assert first["capability_gap"] is None
    assert first["measured"]["matched_rendered_extent_index"] == 0
    assert second["capability_gap"] is not None
    assert second["measured"]["matched_rendered_extent_index"] is None
    assert result["passed"] is False
    # Control: with BOTH rules rendered (each in its own heading's vertical
    # region), each check consumes its own object.
    from reportlab.pdfgen import canvas

    both_pdf = tmp_path / "two_rules.pdf"
    both_pdf = _heading_rule_pdf(
        both_pdf,
        [
            {"label": "WORK EXPERIENCE", "heading_top_pt": 140.0,
             "rule": {"x0": 72.0, "x1": 400.0, "top": 133.0}},
            {"label": "EDUCATION", "heading_top_pt": 200.0,
             "rule": {"x0": 72.0, "x1": 400.0, "top": 193.0}},
        ],
    )
    ok = content_shape_verification(
        state, plan, _two_rule_scaffold(), dict(BULLET_TIERS), html, summary, both_pdf
    )
    assert _rule_rows(ok, "section.01")[0]["capability_gap"] is None
    assert _rule_rows(ok, "section.02")[0]["capability_gap"] is None


def test_a_correct_width_rule_at_the_wrong_y_position_fails(tmp_path: Path) -> None:
    """Vertical-region regression: a rule with the CORRECT x-extent but far
    from its section's rendered heading (wrong page region) must FAIL — the
    gate associates each expected rule with its page/section vertical region."""
    state = compile_synthetic(
        ["WORK EXPERIENCE"], rules=_shape_summary()["rules"], elements=_shape_summary()["elements"]
    )
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    html = render_html(state, plan)
    # Correct width 72→400, but rendered 200pt BELOW the rendered heading
    # (heading top 140; the above-heading region ends ~2pt below it).
    misplaced_pdf = _heading_rule_pdf(
        tmp_path / "misplaced.pdf",
        [{"label": "WORK EXPERIENCE", "heading_top_pt": 140.0,
          "rule": {"x0": 72.0, "x1": 400.0, "top": 340.0}}],
    )
    result = content_shape_verification(
        state, plan, _shape_scaffold(), dict(BULLET_TIERS), html,
        _shape_summary(), misplaced_pdf,
    )
    rule_row = _rule_rows(result, "section.01")[0]
    assert rule_row["capability_gap"] is not None
    assert rule_row["measured"]["matched_rendered_extent_index"] is None
    assert result["passed"] is False
    # And on the wrong PAGE it fails as well.
    import pdfplumber

    from reportlab.pdfgen import canvas

    wrong_page_pdf = tmp_path / "wrong_page.pdf"
    document = canvas.Canvas(str(wrong_page_pdf))
    document.setFont("Helvetica", 10)
    document.drawString(72.0, 792.0 - 140.0 - 7.2, "WORK EXPERIENCE")
    document.showPage()
    document.setLineWidth(0.4)
    document.line(72.0, 792.0 - 133.0, 400.0, 792.0 - 133.0)
    document.showPage()
    document.save()
    with pdfplumber.open(wrong_page_pdf) as check:
        assert len(check.pages) == 2
    wrong_page = content_shape_verification(
        state, plan, _shape_scaffold(), dict(BULLET_TIERS), html,
        _shape_summary(), wrong_page_pdf,
    )
    assert wrong_page["passed"] is False
    assert _rule_rows(wrong_page, "section.01")[0]["capability_gap"] is not None


def test_a_contentless_section_records_its_rule_as_not_required() -> None:
    """A mapped section with no candidate content renders nothing and records
    its rule as NOT required (never as rendered or as a capability gap)."""
    summary = _two_rule_summary()
    state = compile_synthetic(
        ["WORK EXPERIENCE", "CERTIFICATIONS"], rules=summary["rules"],
        elements=summary["elements"],
    )
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    certifications = next(
        section for section in plan.sections if section.source_role == "certifications"
    )
    assert certifications.empty is True
    html = render_html(state, plan)
    result = content_shape_verification(
        state, plan, _two_rule_scaffold(), dict(BULLET_TIERS), html, summary
    )
    row = _rule_rows(result, "section.02")[0]
    assert row["required_by_declared_shape"] is False
    assert row["capability_gap"] is None


# ---------------------------------------------------------------------------
# Real frozen-C1 pairs (local corpus + cached evidence + pinned Chrome)
# ---------------------------------------------------------------------------

REQUIRED_ARTIFACTS = (
    "c2_layout_state.json", "candidate_render_context.json", "c2_render_plan.json",
    "c2_output.html", "c2_output.pdf", "content_validation.json",
    "structure_validation.json", "privacy_validation.json", "render_determinism.json",
    "capability_gaps.json", "leaf_ownership.json", "comparison_manifest.json",
    "review.html", "target_page_1.png", "c1_page_1.png", "c2_page_1.png",
    "diff_target_vs_c2_page_1.png", "diff_c1_vs_c2_page_1.png",
    "context_coverage.json", "content_accounting.json", "blank_page_validation.json",
    "hard_gates.json",
)


@pytest.mark.local_dataset
@pytest.mark.parametrize("pair", ["D_E", "E_F", "E_D"])
def test_frozen_c1_pair_runs_end_to_end(pair: str) -> None:
    from tests.experiments.c2_pipeline import C2_0B_PAIRS

    spec = C2_0B_PAIRS[pair]
    target = Path(renderer_module.__file__).resolve().parents[2] / (
        f"tests/local_datasets/resume_matrix/resume_{spec['target']}.pdf"
    )
    if not target.exists():
        pytest.skip("local resume_matrix corpus not present")
    result = renderer_module.run_pair(pair)
    run_dir = Path(result["run_dir"])
    for artifact in REQUIRED_ARTIFACTS:
        assert (run_dir / artifact).exists(), artifact
    coverage = json.loads((run_dir / "context_coverage.json").read_text())
    assert coverage["total_coverage"] is True
    ownership = json.loads((run_dir / "leaf_ownership.json").read_text())
    assert ownership["status"] == "fully_materialized"
    assert ownership["ownership_exactly_one"] is True
    accounting = json.loads((run_dir / "content_accounting.json").read_text())
    assert accounting["passed"] is True
    assert accounting["unhomed"] == [] and accounting["unresolved_unroutable"] == []
    blank = json.loads((run_dir / "blank_page_validation.json").read_text())
    assert blank["passed"] is True and blank["blank_pages"] == []
    manifest = json.loads((run_dir / "comparison_manifest.json").read_text())
    assert manifest["frozen_c1_baseline"]["run_id"]
    gates = json.loads((run_dir / "hard_gates.json").read_text())["gates"]
    if spec["target"] == "D":
        # Gap-only pair: D's target evidence carries no measured entry
        # typography tiers, so the shape gate stays FALSE with named
        # capability gaps — never a placeholder-true hard gate.
        assert gates["content_shapes_match_evidence"] is False
        shape = json.loads((run_dir / "content_shape_verification.json").read_text())
        gaps = {
            row["capability_gap"]
            for entry in shape["rows"]
            for row in entry["properties"]
            if row["capability_gap"]
        }
        assert gaps
        assert manifest["parity_scope"].startswith("D-target pairs are gap-only")
    else:
        assert manifest["hard_gates_passed"] is True
        assert all(gates.values())
