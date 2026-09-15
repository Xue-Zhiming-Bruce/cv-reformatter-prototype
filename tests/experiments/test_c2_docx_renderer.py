"""C2-0c focused tests: deterministic state -> DOCX render plan -> native OOXML.

Offline lane (default): a synthetic C2LayoutState + an authored candidate
render context exercise DOCX compilation, native-structure inspection
(tables included), presentation-marker handling, exact content accounting,
output-verified compatibility claims, pagination classification, and
fail-closed behavior — no Chrome, no corpus, no LibreOffice, no network.
The ``local_dataset`` lane runs the real canonical pairs (E→F primary,
D→E generalization/native lists, E→D gap-only control) end to end with
LibreOffice previews (evaluation evidence only).

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
    MAX_FITTING_ITERATIONS,
    FitAdjustments,
    SectionFit,
    _strip_leading_marker_glyphs,
    _style_of,
    build_document,
    compare_geometry,
    content_accounting,
    conversion_compatibility_report,
    deterministic_docx_bytes,
    expected_reading_order,
    expected_visual_rows,
    fit_docx,
    installed_font_families,
    inspect_docx,
    map_rendered_to_expected,
    reading_order_gate,
    resolve_written_fonts,
    run_pair,
    strip_presentation_marker,
    typography_tables,
)
from tests.experiments.c2_renderer import LeafText, compile_render_plan
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


def _full_pipeline(tmp_path: Path, labels: list[str] | None = None, candidate=None):
    """Build -> write -> inspect -> account -> report (the whole pipeline)."""
    state = compile_synthetic(labels)
    if candidate is None:
        candidate = rich_candidate(include_unmatched=False)
    plan = compile_render_plan(state, candidate)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "c2_output.docx"
    path.write_bytes(deterministic_docx_bytes(build_document(state, plan)))
    inspection = inspect_docx(path)
    inspection["reading_order_gate"] = reading_order_gate(plan, inspection)
    accounting = content_accounting(plan, inspection)
    return state, plan, path, inspection, accounting


# ---------------------------------------------------------------------------
# Source presentation markers vs candidate content (work order Part 2.3)
# ---------------------------------------------------------------------------


def test_confirmed_presentation_markers_are_stripped_only_for_native_bullets() -> None:
    # Bullet glyph and dashes are confirmed presentation markers: converted
    # into the native Word bullet when (and only when) the line becomes one.
    for marker in ("•", "-", "–", "—"):
        assert strip_presentation_marker(f"{marker} substantive text", True) == "substantive text"
        # A plain/zero-bullet paragraph keeps EVERY glyph verbatim.
        assert strip_presentation_marker(f"{marker} substantive text", False) == (
            f"{marker} substantive text"
        )
    # Arrows are NOT confirmed markers: they may be content and always stay.
    assert strip_presentation_marker("→ Storage: item", True) == "→ Storage: item"
    assert strip_presentation_marker("→ Storage: item", False) == "→ Storage: item"
    # A dash fused to the word (no whitespace) is content, not a marker.
    assert strip_presentation_marker("-5°C storage", True) == "-5°C storage"
    # Substantive text is never rewritten.
    assert strip_presentation_marker("plain line", True) == "plain line"


def test_native_bullet_markers_convert_in_the_docx_and_accounting(tmp_path: Path) -> None:
    """A '•'-prefixed bullet becomes a native Word bullet WITHOUT a double
    marker; the accounting records the conversion and verifies the substantive
    text exactly once."""

    def candidate_with_prefix(prefix: str):
        candidate = rich_candidate(include_unmatched=False)
        leaves = [
            leaf.model_copy(update={"text": f"{prefix} Did candidate work"})
            if leaf.leaf_id == "work.e1.b1"
            else leaf
            for leaf in candidate.leaves
        ]
        return candidate.model_copy(update={"leaves": leaves})

    from tests.experiments.c2_docx_renderer import expected_paragraphs

    for prefix, rendered_start in (("•", "Did candidate work"), ("–", "Did candidate work"), ("→", "→ Did candidate work")):
        state, plan, path, inspection, accounting = _full_pipeline(
            tmp_path / f"bullet_{ord(prefix)}", candidate=candidate_with_prefix(prefix)
        )
        assert accounting["passed"] is True, (accounting["missing"], accounting["duplicated"])
        expected_bullets = [
            paragraph["text"]
            for paragraph in expected_paragraphs(plan)
            if paragraph["kind"] == "bullet" or paragraph.get("native_bullet")
        ]
        assert inspection["list_paragraphs"] == expected_bullets
        assert rendered_start in expected_bullets
        leaf_record = accounting["leaf_records"]["work.e1.b1"]
        assert leaf_record["rendered_exactly_once"] is True
        assert leaf_record["presentation_marker_converted"] is (prefix != "→")


# ---------------------------------------------------------------------------
# Determinism, package validity, native structures, topology
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
    # Headings are native Word heading paragraphs with the state labels.
    assert inspection["heading_paragraphs"] == [
        section.label for section in plan.sections if not section.empty
    ]
    # Measured bullet designs become real Word list paragraphs (entry
    # bullets and bulleted item lists alike), in document order.
    expected_paragraphs = __import__(
        "tests.experiments.c2_docx_renderer", fromlist=["expected_paragraphs"]
    ).expected_paragraphs(plan)
    expected_bullets = [
        paragraph["text"]
        for paragraph in expected_paragraphs
        if paragraph["kind"] == "bullet" or paragraph.get("native_bullet")
    ]
    assert inspection["list_paragraphs"] == expected_bullets
    assert inspection["list_style_numbered_in_styles_xml"] is True
    # Page geometry comes from the measured state section properties.
    geometry = inspection["section_geometry"]
    assert geometry["page_width_pt"] == pytest.approx(state.page.width_pt, abs=0.051)
    assert geometry["page_height_pt"] == pytest.approx(state.page.height_pt, abs=0.051)
    assert geometry["margin_left_pt"] == pytest.approx(state.page.margin_left_pt, abs=0.051)
    # Every rendered paragraph carries explicit spacing control: built-in
    # Word style defaults (Heading 1 / Normal / List Bullet) cannot leak.
    assert inspection["explicit_spacing_count"] == inspection["paragraph_count"]


def test_entry_rows_keep_left_right_topology_as_borderless_tables(tmp_path: Path) -> None:
    state, plan, path, inspection, accounting = _full_pipeline(tmp_path)
    entry_sections = [section for section in plan.sections if section.content_kind == "entries"]
    entries_with_meta = [
        section for section in entry_sections
        if any(entry.meta_lines for entry in section.entries)
    ]
    assert entries_with_meta
    tables = inspection["tables"]
    assert tables, "entries with metadata must render as tables"
    for record in tables:
        assert record["columns"] == 2
        assert record["borders_none"] is True, "table borders must be genuinely absent"
        assert record["rows_cannot_split"] is True
    # Table content participates in inspection and accounting (no silent
    # omission by document.paragraphs).
    title_texts = [
        line.text
        for section in entries_with_meta
        for entry in section.entries
        for line in entry.title_lines
    ]
    document_texts = [record["text"] for record in inspection["paragraphs"]]
    for text in title_texts:
        assert text in document_texts
    assert accounting["passed"] is True
    # Meta lines sit in the right cell (after all left-column lines of the
    # same entry) — deterministic cell reading order.
    assert all(record["in_table"] for record in inspection["paragraphs"]
               if record["text"] in {line.text for section in entries_with_meta
                                     for entry in section.entries for line in entry.meta_lines})


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
    state, plan, path, inspection, accounting = _full_pipeline(tmp_path)
    order = reading_order_gate(plan, inspection)
    assert order["passed"] is True, order["first_mismatch"]
    assert accounting["passed"] is True, (accounting["missing"], accounting["duplicated"])
    assert accounting["missing"] == [] and accounting["duplicated"] == []
    assert accounting["omission_leaks"] == []
    assert accounting["rendered_leaves"] == len(plan.leaf_ledger)
    assert set(accounting["leaf_records"]) == set(plan.leaf_ledger)


def test_reading_order_gate_fails_on_reordered_paragraphs(tmp_path: Path) -> None:
    state, plan = _state_and_plan()
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


def test_dropped_table_content_fails_the_accounting_gate(tmp_path: Path) -> None:
    """Table content is inspected in document order: deleting a table cell
    paragraph is detectable content loss (never silently omitted)."""
    state, plan = _state_and_plan()
    document = build_document(state, plan)
    table = document.tables[0]
    victim = table.rows[0].cells[0].paragraphs[0]
    victim._element.getparent().remove(victim._element)  # noqa: SLF001
    path = tmp_path / "lossy_table.docx"
    path.write_bytes(deterministic_docx_bytes(document))
    inspection = inspect_docx(path)
    accounting = content_accounting(plan, inspection)
    assert accounting["passed"] is False
    assert accounting["missing"]


# ---------------------------------------------------------------------------
# Compatibility report (output-verified claims; pagination classification)
# ---------------------------------------------------------------------------


def test_every_exact_claim_is_output_verified(tmp_path: Path) -> None:
    # The measured-entry-elements state: entry tiers measured, no unresolved
    # bindings, no icons/tables/images -> nothing unsupported.
    from tests.experiments.test_c2_renderer import _shape_summary

    summary = _shape_summary()
    state = compile_synthetic(
        ["WORK EXPERIENCE"], rules=summary["rules"], elements=summary["elements"]
    )
    candidate = rich_candidate(include_unmatched=False)
    plan = compile_render_plan(state, candidate)
    path = tmp_path / "c2_output.docx"
    path.write_bytes(deterministic_docx_bytes(build_document(state, plan)))
    inspection = inspect_docx(path)
    inspection["reading_order_gate"] = reading_order_gate(plan, inspection)
    accounting = content_accounting(plan, inspection)
    pagination = {
        "target_page_count": 1,
        "frozen_c1_page_count": 1,
        "docx_preview_page_count": 1,
        "detail": "synthetic",
    }
    report = conversion_compatibility_report(state, plan, inspection, accounting, pagination)
    assert report.source_format.startswith("layout-state/1")
    assert "docx" in report.output_format
    assert report.exact, "applicable exact claims must be recorded"
    # NO unconditional static claims: every exact claim carries recorded
    # output evidence and is verified against the written OOXML.
    for item in report.exact:
        assert item.verified is True, item.claim
        assert item.evidence, item.claim
    assert report.content_loss_risk is False
    assert all(feature.content_preserved for feature in report.adjusted)
    assert {feature.feature for feature in report.adjusted} == {
        "entry two-column row layout",
        "candidate-only overflow sections",
    }
    assert report.unsupported == []
    assert report.owner_confirmation_required is False
    assert report.pagination.classification == "exact"


def test_pagination_difference_is_explicitly_classified(tmp_path: Path) -> None:
    state, plan, path, inspection, accounting = _full_pipeline(tmp_path)
    pagination = {
        "target_page_count": 1,
        "frozen_c1_page_count": 1,
        "docx_preview_page_count": 2,
        "detail": "synthetic drift",
    }
    report = conversion_compatibility_report(state, plan, inspection, accounting, pagination)
    assert report.pagination.target_page_count == 1
    assert report.pagination.frozen_c1_page_count == 1
    assert report.pagination.docx_preview_page_count == 2
    assert report.pagination.classification == "adjusted"
    # The pagination change surfaces as a visible adjusted degradation.
    assert any(feature.feature == "pagination" for feature in report.adjusted)


def test_unsupported_features_require_explicit_owner_confirmation(tmp_path: Path) -> None:
    # An unbindable measured section stays unresolved and renders nothing;
    # the compatibility contract must classify it unsupported (fail closed).
    state, plan, path, inspection, accounting = _full_pipeline(
        tmp_path, labels=["WORK EXPERIENCE", "ANOTHER SECTION"]
    )
    assert plan.skipped_unresolved_sections == ["ANOTHER SECTION"]
    report = conversion_compatibility_report(state, plan, inspection, accounting)
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
    "preview_validation.json", "docx_determinism.json", "hard_gates.json",
    "docx_rendered_geometry.json", "docx_geometry_comparison.json",
    "docx_fitting_log.json",
    "review.html", "target_page_1.png", "c1_page_1.png",
)


@pytest.mark.local_dataset
@pytest.mark.parametrize("pair", ["E_F", "D_E", "E_D"])
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
    # Every exact claim is output-verified with recorded evidence.
    assert report["exact"]
    assert all(item["verified"] and item["evidence"] for item in report["exact"])
    # All adjusted degradations preserve content; pagination is classified.
    assert all(feature["content_preserved"] for feature in report["adjusted"])
    assert report["pagination"] and report["pagination"]["classification"] in {
        "exact", "adjusted", "unsupported",
    }
    assert report["owner_confirmation_required"] == bool(report["unsupported"])
    # Preview evidence participated in the hard gates.
    preview = json.loads((run_dir / "preview_validation.json").read_text())
    assert gates["gates"]["preview_and_blank_pages"] is (
        bool(preview.get("available")) and preview["blank_page_gate"]["passed"]
    )
    assert preview["blank_page_gate"]["blank_pages"] == []
    # Rendered-geometry evidence (work order Parts 1/2/4): measured from the
    # preview PDF, compared node-locally in points, hard-gated separately.
    comparison = json.loads((run_dir / "docx_geometry_comparison.json").read_text())
    assert comparison["schema_version"] == "c2-docx-geometry-comparison/1"
    # The geometry verdict is consistent with its own counts (a pair may
    # honestly fail on a remaining delta — D→E's borderline trailing page —
    # and fail-closed pairs may carry unmeasurable capability gaps).
    assert comparison["gate_passed"] is (
        comparison["counts"]["failed"] == 0
        and comparison["counts"]["unmeasurable"] == 0
        and comparison["mapping_unmapped"] == 0
    )
    assert gates["gates"]["rendered_geometry_matches_declared_contract"] is comparison["gate_passed"]
    fitting = json.loads((run_dir / "docx_fitting_log.json").read_text())
    assert 1 <= fitting["iterations"] <= fitting["max_iterations"] == 3
    assert len(fitting["iterations_log"]) == fitting["iterations"]
    typography = report["typography"]
    assert typography["classification"] in {"exact", "adjusted"}
    assert typography["authored_typography"] and typography["rendered_typography"]
    for record in typography["rendered_typography"]:
        # Requested, written, and rendered fonts are recorded separately; a
        # substituted family can never carry the exact classification.
        assert set(record) >= {
            "requested_font", "written_font", "rendered_font", "classification",
        }
        if record["requested_font"] != record["rendered_font"]:
            assert record["classification"] == "adjusted"
    if report["unsupported"]:
        assert gates["passed"] is False and gates["owner_confirmation_required"] is True
    else:
        assert gates["passed"] is True
    # C2-0cR: every canonical run carries leaf-level horizontal coverage, and
    # the repairability checkpoint evidence (pre-repair comparison + render)
    # is retained when the first fitting iteration failed.
    assert any(row["property"].startswith("leaf_") for row in comparison["rows"])
    if pair == "E_F":
        before = json.loads((run_dir / "docx_geometry_comparison_before.json").read_text())
        before_child_failures = [
            row for row in before["rows"]
            if row["classification"] == "fail"
            and row["property"] in {"leaf_child_marker_x", "leaf_child_text_x", "leaf_child_hanging_indent"}
            and row["node"] == "section.04"
        ]
        # The pre-repair E→F comparison must expose the Experience child
        # indentation gap the old 40/40 result could not see.
        assert before_child_failures, before["counts"]
        after_by_key = {
            (row["property"], row.get("detail") or ""): row for row in comparison["rows"]
        }
        for row in before_child_failures:
            after = after_by_key[(row["property"], row.get("detail") or "")]
            assert after["classification"] == "pass", after
        assert gates["passed"] is True
        assert gates["gates"]["rendered_geometry_matches_declared_contract"] is True
        assert report["pagination"]["docx_preview_page_count"] == 1
        assert (run_dir / "c2_output_before.pdf").exists()
    else:
        # D→E and E→D retain their previous honest fail-closed status; the
        # expanded coverage did not tune them toward passing.
        assert gates["passed"] is False
        assert gates["gates"]["rendered_geometry_matches_declared_contract"] is False


# ---------------------------------------------------------------------------
# Rendered-geometry measurement, comparison, typography, and fitting
# (work order Parts 1-5; offline — no LibreOffice, no corpus)
# ---------------------------------------------------------------------------


def _fake_target_geo(state, plan) -> dict:
    """A target-geometry measurement derived from the declared state/plan
    (the values the compiler was told to consume)."""
    sections = {}
    rules = {rule.rule_id: rule.model_dump() for rule in state.rules}
    for section in [*plan.sections, *plan.appended_sections]:
        if section.empty:
            continue
        rule = rules.get(section.rule_id) if section.rule_id else None
        entry_node = next(
            (node for node in state.nodes if node.kind == "entry_row" and node.parent_id == section.node_id),
            None,
        )
        heading_token = _style_of(state, section.style_id)
        sections[section.node_id] = {
            "label": section.label,
            "page": 1,
            "heading_top_pt": 40.0,
            "heading_x0_pt": (rule["x0_pt"] if rule else float(state.page.margin_left_pt)),
            "heading_size_pt": heading_token.font_size_pt if heading_token else 14.346,
            "rule": (
                {
                    "page": 1, "top_pt": 60.0, "x0_pt": rule["x0_pt"],
                    "x1_pt": rule["x1_pt"], "stroke_pt": rule["stroke_pt"],
                }
                if rule else None
            ),
            "content_start_x_pt": section.base_x0_pt if section.base_x0_pt is not None else float(state.page.margin_left_pt),
            "entry_right_edge_pt": (
                entry_node.columns[-1].x1_pt
                if entry_node is not None and entry_node.columns[-1].x1_pt is not None
                else None
            ),
            "bullets": (
                {"marker_x0_pt": section.bullet_dot_x0_pt, "text_x0_pt": section.bullet_text_x0_pt}
                if section.bullet_dot_x0_pt is not None else None
            ),
            "content_lines": 3,
        }
    return {"measured_from": "synthetic", "sections": sections, "rules": []}


def _fake_rendered(state, plan, *, overrides=None, page_y_shift=0.0):
    """A rendered-geometry measurement whose lines sit exactly on the
    positions a correct DOCX render produces (derived from the state/plan),
    plus per-property offsets to prove the gate fails on real deltas."""
    overrides = overrides or {}
    font = "BAAAAA+ArialMT"
    page = state.page
    rules_by_id = {rule.rule_id: rule.model_dump() for rule in state.rules}
    lines: list[dict] = []
    rules: list[dict] = []
    y = float(page.margin_top_pt) + 4.0 + page_y_shift
    line_number = 0

    def line(kind, top, x0, x1, size, font, text, chars):
        return {
            "page": 1, "top": round(top, 3), "bottom": round(top + size, 3),
            "x0": round(x0, 3), "x1": round(x1, 3), "size": size, "font": font,
            "text": text, "chars": chars,
        }

    # header rows (verbatim field text in measured order)
    for row_plan in [*plan.header_rows, *([plan.header_overflow] if plan.header_overflow else [])]:
        if not getattr(row_plan, "fields", None):
            continue
        token = _style_of(state, row_plan.style_id)
        size = token.font_size_pt if token else 9.0
        if getattr(row_plan, "gap_above_pt", None):
            y += float(row_plan.gap_above_pt)
        text = " ".join(
            (f" {row_plan.separator} " if index else "") + field.text
            for index, field in enumerate(row_plan.fields)
        ) if getattr(row_plan, "separator", None) else "   ".join(field.text for field in row_plan.fields)
        chars = [
            {"text": char, "x0": float(page.margin_left_pt) + 0.5 * i,
             "x1": float(page.margin_left_pt) + 0.5 * i + 0.5,
             "bottom": y + size, "size": size, "font": font}
            for i, char in enumerate(text)
        ]
        lines.append(line("header", y, float(page.margin_left_pt), float(page.margin_left_pt) + 0.5 * len(text), size, font, text, chars))
        y += size

    for section_plan in [*plan.sections, *plan.appended_sections]:
        if section_plan.empty:
            continue
        heading_token = _style_of(state, section_plan.style_id)
        rule = rules_by_id.get(section_plan.rule_id) if section_plan.rule_id else None
        gap_above = section_plan.heading_gap_above_pt
        if gap_above is None:
            gap_above = 0.0
        elif section_plan.candidate_only:
            gap_above = 12.0  # the documented appended-heading rhythm rule
        y += gap_above
        heading_size = heading_token.font_size_pt if heading_token else 14.346
        heading_x0 = (
            float(rule["x0_pt"]) if rule else float(page.margin_left_pt)
        )
        if overrides and "heading_x" in overrides:
            heading_x0 += overrides["heading_x"]
        heading_top = y
        chars = [
            {"text": char, "x0": heading_x0 + 0.6 * index, "x1": heading_x0 + 0.6 * index + 0.6,
             "bottom": y + heading_size, "size": heading_size, "font": font}
            for index, char in enumerate(section_plan.label)
        ]
        heading_bottom = y + heading_size
        lines.append(line("heading", y, heading_x0, heading_x0 + 0.6 * len(section_plan.label), heading_size, font, section_plan.label, chars))
        y = heading_bottom
        if rule is not None:
            if rule.get("placement") == "below_heading":
                rule_top = heading_bottom + float(rule.get("gap_above_pt") or 0.0) + (overrides or {}).get("heading_to_rule_gap", 0.0)
                y = rule_top + float(rule.get("gap_below_pt") or 0.0) + (overrides or {}).get("heading_to_content_gap", 0.0)
            else:
                # above_heading: rule sits ABOVE the heading text.
                rule_top = heading_top - float(rule.get("gap_below_pt") or 0.0) - (overrides or {}).get("heading_to_rule_gap", 0.0)
                y = heading_bottom + float(section_plan.heading_gap_below_pt or 0.0) + (overrides or {}).get("heading_to_content_gap", 0.0)
            rules.append({
                "page": 1, "top_pt": round(rule_top, 3),
                "x0_pt": round(float(rule["x0_pt"]), 3),
                "x1_pt": round(float(rule["x1_pt"]), 3),
                "stroke_pt": round(float(rule["stroke_pt"]), 3),
            })
        content_size = (
            _style_of(state, section_plan.content_style_id).font_size_pt
            if _style_of(state, section_plan.content_style_id) else 9.0
        )
        entry_node = next(
            (node for node in state.nodes if node.kind == "entry_row" and node.parent_id == section_plan.node_id),
            None,
        )
        for index, entry in enumerate(section_plan.entries):
            if index and section_plan.inter_entry_gap_above_pt is not None:
                y += float(section_plan.inter_entry_gap_above_pt)
            title_token = _style_of(state, section_plan.title_style_id)
            meta_token = _style_of(state, section_plan.meta_style_id or section_plan.detail_style_id or section_plan.title_style_id)
            meta_lines = entry.meta_lines
            title_lines = entry.title_lines
            for line_index, title in enumerate(entry.title_lines):
                meta = meta_lines[line_index] if line_index < len(meta_lines) else None
                title_x0 = (
                    float(section_plan.base_x0_pt) + (overrides or {}).get("entry_left_column_x", 0.0)
                    if section_plan.base_x0_pt is not None else float(page.margin_left_pt)
                )
                title_size = title_token.font_size_pt if title_token else content_size
                meta_size = meta_token.font_size_pt if meta_token else content_size
                meta_x1 = (
                    float(entry_node.columns[-1].x1_pt) + (overrides or {}).get("entry_right_edge", 0.0)
                    if entry_node is not None and entry_node.columns[-1].x1_pt is not None
                    else title_x0 + 200.0
                )
                text = title.text if meta is None else f"{title.text} {meta.text}"
                chars = [
                    {"text": char, "x0": title_x0 + 0.5 * i, "x1": title_x0 + 0.5 * i + 0.5,
                     "bottom": y + title_size, "size": title_size, "font": font}
                    for i, char in enumerate(title.text)
                ]
                if meta is not None:
                    chars += [
                        {"text": char, "x0": meta_x1 - (len(meta.text) - i) * 0.5,
                         "x1": meta_x1 - (len(meta.text) - 1 - i) * 0.5,
                         "bottom": y + meta_size, "size": meta_size, "font": font}
                        for i, char in enumerate(meta.text)
                    ]
                lines.append(line("entry_row", y, title_x0, meta_x1, title_size, font, text, chars))
                y += title_size
            for bullet in entry.bullet_items:
                marker_x = (
                    float(section_plan.bullet_dot_x0_pt) + (overrides or {}).get("bullet_marker_x", 0.0)
                    if section_plan.bullet_dot_x0_pt is not None else float(page.margin_left_pt)
                )
                text_x = (
                    float(section_plan.bullet_text_x0_pt) + (overrides or {}).get("bullet_text_x", 0.0)
                    if section_plan.bullet_text_x0_pt is not None else marker_x + 6.0
                )
                marker = "\uf0b7"
                chars = (
                    [{"text": marker, "x0": marker_x, "x1": marker_x + 4.0, "bottom": y + content_size,
                      "size": content_size, "font": "OpenSymbol"}]
                    + [{"text": char, "x0": text_x + 0.5 * i, "x1": text_x + 0.5 * i + 0.5,
                        "bottom": y + content_size, "size": content_size, "font": font}
                       for i, char in enumerate(_strip_leading_marker_glyphs(bullet.text))]
                )
                lines.append(line("bullet", y, marker_x, text_x + len(bullet.text) * 4.0, content_size, font, f"{marker} {_strip_leading_marker_glyphs(bullet.text)}", chars))
                y += content_size
            child_x0 = (
                float(section_plan.base_x0_pt)
                if section_plan.base_x0_pt is not None else float(page.margin_left_pt)
            )
            for text_line in entry.text_lines:
                # The declared compiler aligns entry child detail lines with
                # their entry's content column (C2-0cR root-cause fix).
                lines.append(line("textline", y, child_x0, child_x0 + 200.0, content_size, font, text_line.text,
                                  [{"text": char, "x0": child_x0 + 0.5 * i, "x1": child_x0 + 0.5 * i + 0.5,
                                    "bottom": y + content_size, "size": content_size, "font": font}
                                   for i, char in enumerate(text_line.text)]))
                y += content_size
        for item in section_plan.items:
            lines.append(line("item", y, float(page.margin_left_pt), float(page.margin_left_pt) + 200.0, content_size, font, item.text,
                              [{"text": char, "x0": float(page.margin_left_pt) + 0.5 * i, "x1": float(page.margin_left_pt) + 0.5 * i + 0.5,
                                "bottom": y + content_size, "size": content_size, "font": font}
                               for i, char in enumerate(item.text)]))
            y += content_size
        for paragraph_line in section_plan.paragraph_lines:
            lines.append(line("paragraph", y, float(page.margin_left_pt), float(page.margin_left_pt) + 300.0, content_size, font, paragraph_line.text,
                              [{"text": char, "x0": float(page.margin_left_pt) + 0.5 * i, "x1": float(page.margin_left_pt) + 0.5 * i + 0.5,
                                "bottom": y + content_size, "size": content_size, "font": font}
                               for i, char in enumerate(paragraph_line.text)]))
            y += content_size
    lines.sort(key=lambda entry: (entry["page"], entry["top"]))
    mapping = map_rendered_to_expected(plan, lines)
    return {
        "measured_from": "synthetic",
        "page_count": 1,
        "pages": [{"page": 1, "width_pt": float(page.width_pt), "height_pt": float(page.height_pt)}],
        "line_count": len(lines),
        "lines": [{key: item[key] for key in ("page", "top", "bottom", "x0", "x1", "size", "font", "text")} for item in lines],
        "mapping": mapping,
        "rules": rules,
        "sparse_trailing_page": None,
    }


def _shape_state_and_plan():
    """A state with measured heading gaps, entry tiers, and a rule (the
    _shape_summary evidence), so every declared basis exists."""
    from tests.experiments.test_c2_renderer import _shape_summary
    summary = _shape_summary()
    state = compile_synthetic(
        ["WORK EXPERIENCE"], rules=summary["rules"], elements=summary["elements"]
    )
    return state, compile_render_plan(state, rich_candidate(include_unmatched=False))


def test_correct_geometry_passes_within_tolerance() -> None:
    state, plan = _shape_state_and_plan()
    comparison = compare_geometry(state, plan, _fake_target_geo(state, plan), _fake_rendered(state, plan))
    assert comparison["mapping_unmapped"] == 0
    assert comparison["counts"]["failed"] == 0, [r for r in comparison["rows"] if r["classification"] != "pass"]
    assert comparison["gate_passed"] is True


def test_geometry_uses_relative_geometry_not_absolute_page_y() -> None:
    state, plan = _shape_state_and_plan()
    baseline = compare_geometry(state, plan, _fake_target_geo(state, plan), _fake_rendered(state, plan))
    shifted = compare_geometry(
        state, plan, _fake_target_geo(state, plan),
        _fake_rendered(state, plan, page_y_shift=400.0),
    )
    assert shifted["counts"] == baseline["counts"]
    assert [r["classification"] for r in shifted["rows"]] == [r["classification"] for r in baseline["rows"]]
    assert shifted["gate_passed"] is True


def test_wrong_entry_column_x_and_metadata_right_edge_fail() -> None:
    state, plan = _shape_state_and_plan()
    target_geo = _fake_target_geo(state, plan)
    shifted_left = compare_geometry(
        state, plan, target_geo, _fake_rendered(state, plan, overrides={"entry_left_column_x": 10.0})
    )
    row = next(r for r in shifted_left["rows"] if r["property"] == "entry_left_column_x")
    assert row["classification"] == "fail" and row["delta"] > row["tolerance_pt"]
    assert shifted_left["gate_passed"] is False
    shifted_right = compare_geometry(
        state, plan, target_geo, _fake_rendered(state, plan, overrides={"entry_right_edge": -8.0})
    )
    edge_row = next(r for r in shifted_right["rows"] if r["property"] == "entry_right_edge")
    assert edge_row["classification"] == "fail" and abs(edge_row["delta"] + 8.0) < 0.01
    assert shifted_right["gate_passed"] is False


def test_wrong_bullet_indent_and_heading_rule_gap_fail() -> None:
    from tests.experiments.c2_docx_renderer import compare_geometry
    from tests.experiments.test_c2_renderer import _shape_summary

    summary = _shape_summary()
    state = compile_synthetic(["WORK EXPERIENCE"], rules=summary["rules"], elements=summary["elements"])
    plan = compile_render_plan(state, rich_candidate(include_unmatched=False))
    target_geo = _fake_target_geo(state, plan)
    bad_gap = compare_geometry(
        state, plan, target_geo, _fake_rendered(state, plan, overrides={"heading_to_rule_gap": 4.0})
    )
    row = next(r for r in bad_gap["rows"] if r["property"] == "heading_to_rule_gap")
    assert row["classification"] == "fail" and abs(row["delta"] - 4.0) <= 0.01
    assert bad_gap["gate_passed"] is False


def test_unmappable_required_text_fails_rather_than_disappearing() -> None:
    state, plan = _state_and_plan(["WORK EXPERIENCE"])
    rendered = _fake_rendered(state, plan)
    # Drop one required bullet row's lines entirely: the mapping must fail and
    # record the missing row (it may never silently disappear).
    victim = next(r for r in rendered["mapping"]["mapped"] if r["kind"] == "bullet")
    victim_line_texts = {line["text"] for line in victim["lines"]}
    remaining_lines = [
        line for line in rendered["lines"]
        if line["text"] not in victim_line_texts
    ]
    mapping = map_rendered_to_expected(plan, remaining_lines)
    assert mapping["passed"] is False
    assert any(
        entry["expected_text"] == victim["text"] for entry in mapping["unmapped"]
    )


def test_font_substitution_cannot_pass_as_typography_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    state, plan = _state_and_plan(["WORK EXPERIENCE"])
    # The requested family is NOT installed: the documented portable fallback
    # is written and rendered, so typography is classified adjusted.
    monkeypatch.setattr(
        "tests.experiments.c2_docx_renderer.installed_font_families",
        lambda: frozenset({"arial"}),
    )
    state = state.model_copy(deep=True)
    state.styles = [
        style.model_copy(update={"font_family": "Roboto"}) for style in state.styles
    ]
    resolved = resolve_written_fonts(state)
    assert resolved["style.body"]["requested"] == "Roboto"
    assert resolved["style.body"]["written"] == "Arial"
    assert resolved["style.body"]["substituted"] is True
    rendered = _fake_rendered(state, plan)
    tables = typography_tables(state, plan, rendered, resolved)
    assert tables["classification"] == "adjusted"
    assert all(record["classification"] == "adjusted" for record in tables["rendered_typography"])
    # With the family actually installed and rendered, the record is exact.
    monkeypatch.setattr(
        "tests.experiments.c2_docx_renderer.installed_font_families",
        lambda: frozenset({"arial", "roboto"}),
    )
    state = state.model_copy(deep=True)
    state.styles = [style.model_copy(update={"font_family": "Roboto"}) for style in state.styles]
    rendered_exact = _fake_rendered(state, plan, overrides={})
    for mapped_row in rendered_exact["mapping"]["mapped"]:
        bold = bool((_style_of(state, mapped_row.get("style_id")) or _style_of(state, mapped_row.get("meta_style_id"))) and (_style_of(state, mapped_row.get("style_id")) or _style_of(state, mapped_row.get("meta_style_id"))).bold)
        face = "BAAAAA+Roboto-Bold" if bold else "BAAAAA+Roboto-Regular"
        for line in mapped_row["lines"]:
            line["font"] = face
            for char in line["chars"]:
                char["font"] = face
    tables_exact = typography_tables(state, plan, rendered_exact, resolve_written_fonts(state))
    # With the requested family installed and rendered, no record can carry a
    # substitution-driven 'adjusted' classification; matching family/size/weight
    # records are exact.
    assert all(record["classification"] != "adjusted" for record in tables_exact["rendered_typography"])
    assert any(record["classification"] == "exact" for record in tables_exact["rendered_typography"])


def test_requested_and_rendered_fonts_are_recorded_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tests.experiments.c2_docx_renderer.installed_font_families",
        lambda: frozenset({"arial"}),
    )
    state, plan = _state_and_plan(["WORK EXPERIENCE"])
    state = state.model_copy(deep=True)
    state.styles = [style.model_copy(update={"font_family": "Roboto"}) for style in state.styles]
    resolved = resolve_written_fonts(state)
    tables = typography_tables(state, plan, _fake_rendered(state, plan), resolved)
    record = tables["rendered_typography"][0]
    assert record["requested_font"] == "Roboto"
    assert record["written_font"] == "Arial"
    assert record["rendered_font"] == "arial"
    assert tables["authored_typography"]


def test_incorrect_font_size_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tests.experiments.c2_docx_renderer.installed_font_families",
        lambda: frozenset({"arial"}),
    )
    state, plan = _state_and_plan(["WORK EXPERIENCE"])
    target_geo = _fake_target_geo(state, plan)
    rendered = _fake_rendered(state, plan)
    # Perturb one rendered line's font size beyond the documented tolerance.
    heading_row = next(r for r in rendered["mapping"]["mapped"] if r["kind"] == "heading")
    heading_row["lines"][0]["size"] += 2.0
    comparison = compare_geometry(state, plan, target_geo, rendered)
    row = next(r for r in comparison["rows"] if r["property"] == "heading_font_size")
    assert row["classification"] == "fail"
    assert comparison["gate_passed"] is False


def test_sparse_trailing_page_is_explicitly_classified() -> None:
    state, plan = _state_and_plan(["WORK EXPERIENCE"])
    target_geo = _fake_target_geo(state, plan)
    two_pages = _fake_rendered(state, plan)
    two_pages["page_count"] = 2
    two_pages["sparse_trailing_page"] = {
        "page": 2, "content_extent_pt": 60.0, "writable_height_pt": 722.8,
        "fraction": 0.08, "sparse": True,
    }
    comparison = compare_geometry(state, plan, target_geo, two_pages)
    row = next(r for r in comparison["rows"] if r["property"] == "sparse_trailing_page")
    assert row["classification"] == "fail"
    assert comparison["gate_passed"] is False
    # One page: no trailing page can exist.
    one_page = _fake_rendered(state, plan)
    comparison = compare_geometry(state, plan, target_geo, one_page)
    row = next(r for r in comparison["rows"] if r["property"] == "sparse_trailing_page")
    assert row["classification"] == "pass"


def test_the_fitter_stops_after_the_documented_iteration_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    state, plan = _state_and_plan(["WORK EXPERIENCE"])
    docx_path = Path("unused.docx")
    calls = {"renders": 0}

    def fake_preview(docx_path, run_dir):
        calls["renders"] += 1
        return {"available": True, "pdf": "unused.pdf", "page_count": 1}

    def always_failing(state, plan, target_geo, rendered):
        return {
            "schema_version": "x", "passed": False, "gate_passed": False,
            "counts": {"total": 1, "passed": 0, "failed": 1, "unmeasurable": 0, "not_applicable": 0},
            "adjustable": True,
            "rows": [{
                "property": "heading_gap_above", "node": "section.01", "basis": 10.0,
                "basis_source": "declared_state", "rendered": 20.0, "delta": 10.0,
                "tolerance_pt": 1.5, "classification": "fail",
                "control": "heading_space_before_pt", "detail": "",
            }],
        }

    monkeypatch.setattr("tests.experiments.c2_docx_renderer.build_document", lambda *a, **k: None)
    monkeypatch.setattr("tests.experiments.c2_docx_renderer.deterministic_docx_bytes", lambda document: b"docx")
    monkeypatch.setattr("tests.experiments.c2_docx_renderer._preview_pdf", fake_preview)
    monkeypatch.setattr("tests.experiments.c2_docx_renderer.measure_target_geometry", lambda *a, **k: {"sections": {}})
    monkeypatch.setattr("tests.experiments.c2_docx_renderer.measure_rendered_geometry", lambda *a, **k: {"page_count": 1})
    monkeypatch.setattr(
        "tests.experiments.c2_docx_renderer.compare_geometry",
        lambda state, plan, target_geo, rendered: always_failing(state, plan, target_geo, rendered),
    )
    result = fit_docx(state, plan, Path("unused.pdf"), docx_path, Path("."))
    assert result["iterations"] == MAX_FITTING_ITERATIONS == 3
    assert result["converged"] is False
    assert len(result["log"]) == 3
    # The fitter kept applying the documented correction each iteration
    # (bounded; it never loops indefinitely).
    corrections = [
        iteration["corrections"]["sections"].get("section.01", {}).get("heading_space_before_pt", 0.0)
        for iteration in result["log"]
    ]
    assert corrections == [0.0, pytest.approx(-10.0), pytest.approx(-20.0)]


# ---------------------------------------------------------------------------
# C2-0cR: leaf-level indentation coverage + the bounded indentation repair
# ---------------------------------------------------------------------------


def _state_plan_with_entry_textlines():
    """The measured shape state/plan with two verbatim child detail lines
    added to the first work entry — the E→F blind-spot shape: source-glyph
    ("• ...") and plain detail lines that stay verbatim TEXT because the
    plan declares no native bullet for them. The plan ledger is extended so
    the added leaves stay owned (accounting remains exact)."""
    state, plan = _shape_state_and_plan()
    section = plan.sections[0]
    entry = section.entries[0]
    patched = entry.model_copy(update={
        "text_lines": [
            LeafText(leaf_id="work.e1.t1", text="• Candidate detail line that must indent with its entry"),
            LeafText(leaf_id="work.e1.t2", text="Candidate plain detail line"),
        ],
    })
    sections = [
        section.model_copy(update={"entries": [patched, *section.entries[1:]]}),
        *plan.sections[1:],
    ]
    ledger = dict(plan.leaf_ledger)
    ledger["work.e1.t1"] = entry.node_id
    ledger["work.e1.t2"] = entry.node_id
    return state, plan.model_copy(update={"sections": sections, "leaf_ledger": ledger})


def _leaf_rows(comparison: dict, node: str = "section.01") -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in comparison["rows"]:
        if row["node"] == node and row["property"].startswith("leaf_"):
            grouped.setdefault(row["property"], []).append(row)
    return grouped


def test_pre_repair_geometry_fails_on_the_uncovered_child_textline() -> None:
    """Regression: a child textline rendered AWAY from its measured target
    anchor must FAIL the expanded gate — the old section-level rows (which
    only checked the FIRST content row) reported 40/40 while every Experience
    detail line sat at the page margin."""
    state, plan = _state_plan_with_entry_textlines()
    rendered = _fake_rendered(state, plan)
    comparison = compare_geometry(state, plan, _fake_target_geo(state, plan), rendered)
    grouped = _leaf_rows(comparison)
    assert grouped["leaf_child_marker_x"], "child textline leaves must carry marker coverage"
    failing = [row for row in grouped["leaf_child_marker_x"] if row["classification"] == "fail"]
    assert failing, grouped
    assert all(row["basis_source"] == "measured_target" for row in grouped["leaf_child_marker_x"])
    assert comparison["gate_passed"] is False
    assert comparison["adjustable"] is True  # the fitter has a documented control


def test_section_first_row_alignment_cannot_mask_child_misalignment() -> None:
    """content_start_x checks only the section's first content row; the
    expanded gate must still fail when a LATER child row is misaligned."""
    state, plan = _state_plan_with_entry_textlines()
    rendered = _fake_rendered(state, plan)
    comparison = compare_geometry(state, plan, _fake_target_geo(state, plan), rendered)
    content_start = next(
        row for row in comparison["rows"] if row["property"] == "content_start_x" and row["node"] == "section.01"
    )
    assert content_start["classification"] == "pass"
    assert comparison["gate_passed"] is False


def test_target_measured_marker_text_and_hanging_are_separate_contracts() -> None:
    """Marker x, bullet text x, and the hanging indent are measured and
    compared separately per leaf: shifting only the text start fails the
    text and hanging rows while the marker row still passes."""
    state, plan = _state_plan_with_entry_textlines()
    rendered = _fake_rendered(state, plan)
    # Put the child line ON its measured anchors first (marker 60.0, text
    # 64.0), then shift ONLY the text after the leading glyph by +3pt.
    shifted = 0
    for mapped in rendered["mapping"]["mapped"]:
        if mapped["kind"] == "textline" and mapped["text"].startswith("•"):
            line0 = mapped["lines"][0]
            chars = line0["chars"]
            space_width = chars[1]["x1"] - chars[1]["x0"]
            chars[0]["x0"], chars[0]["x1"] = 60.0, 60.0 + space_width
            chars[1]["x0"], chars[1]["x1"] = 60.0 + space_width, 64.0
            for offset, char in enumerate(chars[2:]):
                char["x0"] = 64.0 + 0.5 * offset + 3.0
                char["x1"] = 64.0 + 0.5 * offset + 0.5 + 3.0
            line0["x0"] = 60.0
            line0["x1"] = round(max(char["x1"] for char in chars), 3)
            shifted += 1
            break
    assert shifted
    comparison = compare_geometry(state, plan, _fake_target_geo(state, plan), rendered)
    grouped = _leaf_rows(comparison)
    marker = grouped["leaf_child_marker_x"][0]
    text = grouped["leaf_child_text_x"][0]
    hanging = grouped["leaf_child_hanging_indent"][0]
    assert marker["classification"] == "pass"
    assert text["classification"] == "fail"
    assert hanging["classification"] == "fail"
    assert {marker["property"], text["property"], hanging["property"]} == {
        "leaf_child_marker_x", "leaf_child_text_x", "leaf_child_hanging_indent",
    }


def test_child_textline_without_target_anchor_is_honestly_unmeasurable() -> None:
    """A child leaf whose target carries no measurable bullet-text anchor has
    no basis: it must fail the gate as unmeasurable (never a silent pass)."""
    state, plan = _state_plan_with_entry_textlines()
    target_geo = _fake_target_geo(state, plan)
    target_geo["sections"]["section.01"]["bullets"] = None
    rendered = _fake_rendered(state, plan)
    comparison = compare_geometry(state, plan, target_geo, rendered)
    grouped = _leaf_rows(comparison)
    text_rows = grouped["leaf_child_text_x"]
    assert any(row["classification"] == "unmeasurable" for row in text_rows)
    assert comparison["gate_passed"] is False
    # The declared marker basis still applies where the state measures one.
    assert any(row["classification"] == "pass" for row in grouped["leaf_child_marker_x"])


def test_the_bounded_edit_changes_only_the_intended_stable_nodes() -> None:
    """FitAdjustments.sections[<node>].entry_child_text_indent_pt moves ONLY
    that node's child detail lines; every other paragraph (text and indent)
    is byte-identical, and the edited paragraph keeps its text verbatim."""
    state, plan = _state_plan_with_entry_textlines()
    before = build_document(state, plan)
    after = build_document(
        state, plan,
        adjustments=FitAdjustments(sections={"section.01": SectionFit(node_id="section.01", entry_child_text_indent_pt=10.7)}),
    )
    from docx.shared import Pt

    def paragraph_snapshot(document):
        return [
            (paragraph.text, paragraph.paragraph_format.left_indent)
            for paragraph in document.paragraphs
        ]
    before_rows = paragraph_snapshot(before)
    after_rows = paragraph_snapshot(after)
    assert [text for text, _ in before_rows] == [text for text, _ in after_rows]
    changed = [
        (index, b, a) for index, (b, a) in enumerate(zip(before_rows, after_rows))
        if b[1] != a[1]
    ]
    # Exactly the two child detail lines of section.01's first entry moved
    # (the source-glyph line and the plain line); every other paragraph is
    # byte-identical and all texts stay verbatim.
    assert len(changed) == 2, changed
    assert all("Candidate detail line" in after_rows[index][0] or "Candidate plain detail line" in after_rows[index][0]
               for index, _, _ in changed)
    expected = Pt(46.9 - 36.0 + 10.7)  # declared entry-column alignment + correction
    assert all(abs(a[1].pt - expected.pt) < 0.01 for _, _, a in changed)


def test_candidate_content_and_accounting_unchanged_by_the_edit(tmp_path: Path) -> None:
    """The bounded edit is presentation-only: reading order, leaf texts, and
    content accounting are identical with and without the adjustment."""
    state, plan = _state_plan_with_entry_textlines()
    plain = build_document(state, plan)
    edited = build_document(
        state, plan,
        adjustments=FitAdjustments(sections={"section.01": SectionFit(node_id="section.01", entry_child_text_indent_pt=10.7)}),
    )
    plain_path = tmp_path / "plain.docx"
    edited_path = tmp_path / "edited.docx"
    plain_path.write_bytes(deterministic_docx_bytes(plain))
    edited_path.write_bytes(deterministic_docx_bytes(edited))
    inspection_plain = inspect_docx(plain_path)
    inspection_edited = inspect_docx(edited_path)
    # Semantic text is identical with and without the edit (verbatim glyphs).
    assert [p["text"] for p in inspection_plain["paragraphs"]] == [
        p["text"] for p in inspection_edited["paragraphs"]
    ]
    accounting_plain = content_accounting(plan, inspection_plain)
    accounting_edited = content_accounting(plan, inspection_edited)
    assert accounting_plain["passed"] is True and accounting_edited["passed"] is True
    assert accounting_plain["leaf_records"] == accounting_edited["leaf_records"]
    assert accounting_plain["presentation_marker_conversions"] == (
        accounting_edited["presentation_marker_conversions"]
    )


def test_unrelated_section_geometry_is_unchanged_by_the_edit() -> None:
    """Applying the bounded edit flips ONLY the intended node's child rows;
    rows of every unrelated node keep their property/classification/value."""
    state, plan = _state_plan_with_entry_textlines()
    before_rendered = _fake_rendered(state, plan)
    comparison_before = compare_geometry(state, plan, _fake_target_geo(state, plan), before_rendered)
    # The repaired render: the source-glyph child line moved onto the
    # measured anchors (marker 60.0pt, text 64.0pt — the shape state's
    # measured bullet tiers); the plain child line keeps its measured
    # content-start position.
    after_rendered = _fake_rendered(state, plan)
    for mapped in after_rendered["mapping"]["mapped"]:
        if mapped["kind"] == "textline" and mapped["text"].startswith("•"):
            line0 = mapped["lines"][0]
            chars = line0["chars"]
            space_width = chars[1]["x1"] - chars[1]["x0"]
            chars[0]["x0"], chars[0]["x1"] = 60.0, 60.0 + space_width
            chars[1]["x0"], chars[1]["x1"] = 60.0 + space_width, 64.0
            for offset, char in enumerate(chars[2:]):
                char["x0"] = 64.0 + 0.5 * offset
                char["x1"] = 64.0 + 0.5 * offset + 0.5
            line0["x0"] = 60.0
            line0["x1"] = round(max(char["x1"] for char in chars), 3)
    comparison_after = compare_geometry(state, plan, _fake_target_geo(state, plan), after_rendered)
    key = lambda row: (row["property"], row["node"], row.get("detail") or "")
    before_by_key = {key(row): row for row in comparison_before["rows"]}
    for row in comparison_after["rows"]:
        before = before_by_key.get(key(row))
        assert before is not None, key(row)
        if row["node"] != "section.01" or not row["property"].startswith("leaf_child"):
            assert row["classification"] == before["classification"], key(row)
            assert row["rendered"] == before["rendered"], key(row)
    grouped = _leaf_rows(comparison_after)
    assert all(row["classification"] == "pass" for rows in grouped.values() for row in rows)
    assert comparison_after["gate_passed"] is True
