"""C2-0c focused tests: deterministic state -> DOCX render plan -> native OOXML.

Offline lane (default): a synthetic C2LayoutState + an authored candidate
render context exercise DOCX compilation, native-structure inspection,
content accounting, compatibility classification, and fail-closed behavior —
no Chrome, no corpus, no LibreOffice, no network. The ``local_dataset`` lane
runs the real canonical pairs (E→F primary, E→D gap-bearing, D→E) end to end
with LibreOffice previews (evaluation evidence only).

Run:

    pytest tests/experiments/test_c2_docx_renderer.py -m "not local_dataset"
    pytest tests/experiments/test_c2_docx_renderer.py -m local_dataset
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tests.experiments.c2_docx_renderer import (
    build_document,
    content_accounting,
    conversion_compatibility_report,
    deterministic_docx_bytes,
    expected_reading_order,
    inspect_docx,
    reading_order_gate,
    run_pair,
)
from tests.experiments.c2_renderer import compile_render_plan
from tests.experiments.test_c2_pipeline import BULLET_TIERS, compile_synthetic
from tests.experiments.test_c2_renderer import rich_candidate


def _state_and_plan(labels: list[str] | None = None):
    state = compile_synthetic(labels)
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    return state, plan


def _write_docx(state, plan, tmp_path: Path, name: str = "c2_output.docx") -> Path:
    path = tmp_path / name
    path.write_bytes(deterministic_docx_bytes(build_document(state, plan)))
    return path


# ---------------------------------------------------------------------------
# Determinism, package validity, native structures
# ---------------------------------------------------------------------------


def test_deterministic_bytes_across_two_compiles(tmp_path: Path) -> None:
    state, plan = _state_and_plan()
    first = deterministic_docx_bytes(build_document(state, plan))
    second = deterministic_docx_bytes(build_document(state, plan))
    assert first == second
    path = _write_docx(state, plan, tmp_path)
    assert zipfile.is_zipfile(path)


def test_package_opens_and_carries_native_structures(tmp_path: Path) -> None:
    state, plan = _state_and_plan()
    path = _write_docx(state, plan, tmp_path)
    inspection = inspect_docx(path)
    assert inspection["valid_package"] is True
    assert "[Content_Types].xml" not in inspection  # raw parts are not leaked
    # Headings are native Word heading paragraphs with the state labels.
    assert inspection["heading_paragraphs"] == [
        section.label for section in plan.sections if not section.empty
    ]
    # Measured bullet designs become real Word list paragraphs (entry
    # bullets and bulleted item lists alike), in document order.
    bullet_items: list[str] = []
    for section in plan.sections:
        if section.content_kind == "entries":
            bullet_items.extend(
                line.text for entry in section.entries for line in entry.bullet_items
            )
        elif section.items and section.bullet_marker == "bullet":
            bullet_items.extend(item.text for item in section.items)
    assert inspection["list_paragraphs"] == bullet_items
    if bullet_items:
        assert inspection["list_style_numbered_in_styles_xml"] is True
    # Page geometry comes from the measured state section properties.
    geometry = inspection["section_geometry"]
    assert geometry["page_width_pt"] == pytest.approx(state.page.width_pt, abs=0.01)
    assert geometry["page_height_pt"] == pytest.approx(state.page.height_pt, abs=0.01)
    assert geometry["margin_left_pt"] == pytest.approx(state.page.margin_left_pt, abs=0.01)


def test_measured_rules_become_native_paragraph_borders(tmp_path: Path) -> None:
    from tests.experiments.test_c2_renderer import _shape_summary

    summary = _shape_summary()
    state = compile_synthetic(
        ["WORK EXPERIENCE"], rules=summary["rules"], elements=summary["elements"]
    )
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    path = _write_docx(state, plan, tmp_path)
    inspection = inspect_docx(path)
    required_rules = [section for section in plan.sections if section.rule_id and not section.empty]
    assert len(required_rules) == 1
    assert inspection["paragraph_borders_in_document_xml"] == 1
    # Measured stroke and color survive into the OOXML border definition.
    with zipfile.ZipFile(path) as package:
        document_xml = package.read("word/document.xml").decode("utf-8")
    assert 'w:val="single"' in document_xml
    assert "111827" in document_xml


def test_zero_bullet_design_renders_verbatim_text_paragraphs(tmp_path: Path) -> None:
    """A zero-bullet target (marker 'none') never becomes a Word list: the
    source bullet glyphs stay verbatim text (proposal §10.5 ruling)."""
    state, plan = _state_and_plan()
    mutated = state.model_copy(deep=True)
    mutated.nodes = [
        node.model_copy(
            update={"content": node.content.model_copy(update={"bullet_marker": "none"})}
        )
        if node.kind == "section" and node.content is not None
        else node
        for node in mutated.nodes
    ]
    mutated.nodes = [
        node.model_copy(update={"list_marker": "none"})
        if node.kind == "list_row"
        else node
        for node in mutated.nodes
    ]
    plan = compile_render_plan(mutated, rich_candidate(include_unmatched=False))
    path = _write_docx(mutated, plan, tmp_path)
    inspection = inspect_docx(path)
    assert inspection["list_paragraphs"] == []
    text_lines = [
        line.text for section in plan.sections for entry in section.entries
        for line in entry.text_lines
    ]
    paragraph_texts = [paragraph["text"] for paragraph in inspection["paragraphs"]]
    assert text_lines
    for line in text_lines:
        assert line in paragraph_texts


# ---------------------------------------------------------------------------
# Reading order and exact content accounting
# ---------------------------------------------------------------------------


def test_reading_order_and_accounting_are_exact(tmp_path: Path) -> None:
    state, plan = _state_and_plan()
    path = _write_docx(state, plan, tmp_path)
    inspection = inspect_docx(path)
    order = reading_order_gate(plan, inspection)
    assert order["passed"] is True, order["first_mismatch"]
    accounting = content_accounting(plan, inspection)
    assert accounting["passed"] is True, (accounting["missing"], accounting["duplicated"])
    assert accounting["missing"] == [] and accounting["duplicated"] == []
    assert accounting["omission_leaks"] == []
    assert accounting["rendered_leaves"] == len(plan.leaf_ledger)
    assert set(accounting["leaf_records"]) == set(plan.leaf_ledger)


def test_reading_order_gate_fails_on_reordered_paragraphs(tmp_path: Path) -> None:
    state, plan = _state_and_plan()
    path = _write_docx(state, plan, tmp_path)
    document = build_document(state, plan)
    # Move the last body paragraph before the first heading: order must fail.
    body = document.paragraphs[-1]._element  # noqa: SLF001
    anchor = document.paragraphs[0]._element  # noqa: SLF001
    anchor.addprevious(body)
    mutated_path = tmp_path / "reordered.docx"
    mutated_path.write_bytes(deterministic_docx_bytes(document))
    inspection = inspect_docx(mutated_path)
    assert reading_order_gate(plan, inspection)["passed"] is False
    assert content_accounting(plan, inspection)["passed"] is False


def test_an_explicit_omission_is_never_rendered(tmp_path: Path) -> None:
    from tests.experiments.c2_pipeline import UnroutableContent
    from tests.experiments.test_c2_renderer import candidate_with_unroutable

    state = compile_synthetic()
    candidate = candidate_with_unroutable("omit")
    plan = compile_render_plan(state, candidate)
    path = _write_docx(state, plan, tmp_path)
    inspection = inspect_docx(path)
    assert "unroutable.location" not in plan.leaf_ledger
    accounting = content_accounting(plan, inspection)
    assert accounting["passed"] is True
    assert accounting["explicitly_omitted"][0]["text"] == "CANDCITY, ST"
    assert accounting["omission_leaks"] == []
    all_text = "\n".join(paragraph["text"] for paragraph in inspection["paragraphs"])
    assert "CANDCITY" not in all_text


def test_a_dropped_paragraph_fails_the_accounting_gate(tmp_path: Path) -> None:
    state, plan = _state_and_plan()
    document = build_document(state, plan)
    # Silent content loss: remove one rendered body paragraph.
    victim = next(paragraph for paragraph in document.paragraphs if paragraph.text)
    victim._element.getparent().remove(victim._element)  # noqa: SLF001
    path = tmp_path / "lossy.docx"
    path.write_bytes(deterministic_docx_bytes(document))
    inspection = inspect_docx(path)
    accounting = content_accounting(plan, inspection)
    assert accounting["passed"] is False
    assert accounting["missing"], "the dropped leaf must be reported"


# ---------------------------------------------------------------------------
# Compatibility report (deterministic, evidence-backed, fail closed)
# ---------------------------------------------------------------------------


def test_compatibility_report_classifies_full_coverage_as_exact_plus_adjusted() -> None:
    # The measured-entry-elements state: entry tiers measured, no unresolved
    # bindings, no icons/tables/images -> nothing unsupported.
    from tests.experiments.test_c2_renderer import _shape_summary

    summary = _shape_summary()
    state = compile_synthetic(
        ["WORK EXPERIENCE"], rules=summary["rules"], elements=summary["elements"]
    )
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    report = conversion_compatibility_report(state, plan)
    assert report.source_format.startswith("layout-state/1")
    assert "docx" in report.output_format
    assert report.exact  # exact features recorded
    assert report.content_loss_risk is False
    # Adjusted features preserve content (approved degradation only); the
    # single-section state appends the unmatched candidate sections.
    assert all(feature.content_preserved for feature in report.adjusted)
    assert {feature.feature for feature in report.adjusted} == {
        "entry two-column row layout",
        "candidate-only overflow sections",
    }
    assert report.unsupported == []
    assert report.owner_confirmation_required is False


def test_unsupported_features_require_explicit_owner_confirmation() -> None:
    # An unbindable measured section stays unresolved and renders nothing;
    # the compatibility contract must classify it unsupported (fail closed).
    state, plan = _state_and_plan(["WORK EXPERIENCE", "ANOTHER SECTION"])
    assert plan.skipped_unresolved_sections == ["ANOTHER SECTION"]
    report = conversion_compatibility_report(state, plan)
    assert any(
        feature.feature == "unresolved section bindings" for feature in report.unsupported
    )
    assert report.owner_confirmation_required is True


def test_a_failed_plan_refuses_docx_compilation() -> None:
    import pytest

    from tests.experiments.c2_pipeline import SectionContent

    state, _ = _state_and_plan()
    broken = state.model_copy(deep=True)
    for index, node in enumerate(broken.nodes):
        if node.kind == "section" and node.binding and node.binding.sources == ["work_experience"]:
            broken.nodes[index] = node.model_copy(
                update={"content": SectionContent(content_kind="unsupported", sources=["work_experience"])}
            )
    plan = compile_render_plan(broken, rich_candidate(include_unmatched=False))
    assert plan.status == "failed"
    with pytest.raises(RuntimeError, match="refusing to render a failed plan"):
        build_document(broken, plan)


# ---------------------------------------------------------------------------
# Real canonical pairs (local corpus + cached evidence; LibreOffice previews)
# ---------------------------------------------------------------------------

REQUIRED_ARTIFACTS = (
    "c2_layout_state.json", "candidate_render_context.json", "context_coverage.json",
    "docx_render_plan.json", "c2_output.docx", "content_accounting.json",
    "ooxml_inspection.json", "conversion_compatibility_report.json",
    "docx_determinism.json", "hard_gates.json", "review.html",
)


@pytest.mark.local_dataset
@pytest.mark.parametrize("pair", ["E_F", "E_D", "D_E"])
def test_canonical_pairs_end_to_end(pair: str) -> None:
    result = run_pair(pair)
    run_dir = Path(result["run_dir"])
    for artifact in REQUIRED_ARTIFACTS:
        assert (run_dir / artifact).exists(), artifact
    coverage = json.loads((run_dir / "context_coverage.json").read_text())
    assert coverage["total_coverage"] is True
    accounting = json.loads((run_dir / "content_accounting.json").read_text())
    assert accounting["passed"] is True, (accounting["missing"], accounting["duplicated"])
    inspection = json.loads((run_dir / "ooxml_inspection.json").read_text())
    assert inspection["valid_package"] is True
    assert inspection["reading_order_gate"]["passed"] is True
    determinism = json.loads((run_dir / "docx_determinism.json").read_text())
    assert determinism["bytes_equal_after_metadata_normalization"] is True
    report = json.loads((run_dir / "conversion_compatibility_report.json").read_text())
    gates = json.loads((run_dir / "hard_gates.json").read_text())
    # All adjusted degradations preserve content; unsupported requires owner
    # confirmation and the run honestly reports it fail-closed.
    assert all(feature["content_preserved"] for feature in report["adjusted"])
    assert report["owner_confirmation_required"] == bool(report["unsupported"])
    assert gates["gates"]["unsupported_features_confirmed"] is (not report["unsupported"])
    if report["unsupported"]:
        assert gates["passed"] is False and gates["owner_confirmation_required"] is True
    else:
        assert gates["passed"] is True
