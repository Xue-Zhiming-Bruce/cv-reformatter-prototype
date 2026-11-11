"""Unit tests for local-PDF text-fill-color measurement."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.template_analysis.commercial.local_color import (
    _hex_from_color,
    enrich_badges_from_local_pdf,
    enrich_colors_from_local_pdf,
    enrich_rules_from_local_pdf,
)
from app.template_analysis.commercial.models import (
    NormalizedBox,
    NormalizedLayoutEvidence,
    NormalizedPage,
    NormalizedTextBlock,
)
from scripts.run_abc_live_matrix import (
    _badge_failures,
    _body_size_failure,
    _measure_dominant_text_size,
    _measure_html_badges,
    _measure_pdf_rules,
    _rule_geometry_failures,
    _structure_failures,
    _surface_drift,
)
from tests.helpers.badge_probe_pdfs import (
    build_deep_label_inset_bars_pdf,
    build_outlined_chip_pdf,
    build_overflowing_chip_pdf,
    build_section_banner_pdf,
    build_wide_padded_and_square_chip_pdf,
)
from tests.helpers.synthetic_pdf import (
    build_banner_and_triangle_target_pdf,
    build_multiline_rounded_shape_target_pdf,
    build_partial_heading_rules_target_pdf,
    build_pipe_contact_header_target_pdf,
    build_rounded_chip_target_pdf,
    build_rounded_decoration_target_pdf,
    build_styled_target_pdf,
    build_tech_teal_palette_target_pdf,
)


def test_hex_conversion_across_colorspaces() -> None:
    assert _hex_from_color((0.12, 0.2, 0.35)) == "#1F3359"
    assert _hex_from_color((0.0, 0.0, 0.0)) == "#000000"
    assert _hex_from_color((1.0, 1.0, 1.0)) == "#FFFFFF"
    assert _hex_from_color((0.5,)) == "#808080"  # gray
    assert _hex_from_color(0.5) == "#808080"
    assert _hex_from_color((0.0, 0.0, 0.0, 0.0)) == "#FFFFFF"  # CMYK zero
    assert _hex_from_color((1.0, 1.0, 1.0, 0.0)) == "#000000"  # CMYK full
    assert _hex_from_color(None) is None
    assert _hex_from_color("PatternColor") is None
    assert _hex_from_color((0.1, 0.2, 0.3, 0.4, 0.5)) is None


def _block(
    element_id: str, text: str, *, x0: float, top: float, x1: float, bottom: float,
    color_hex: str | None = None,
) -> NormalizedTextBlock:
    return NormalizedTextBlock(
        element_id=element_id,
        text=text,
        page_number=1,
        bbox=NormalizedBox(x0=x0, top=top, x1=x1, bottom=bottom),
        color_hex=color_hex,
    )


def _evidence(*blocks: NormalizedTextBlock) -> NormalizedLayoutEvidence:
    return NormalizedLayoutEvidence(
        provider="adobe",
        pages=[NormalizedPage(page_number=1, width_pt=612, height_pt=792)],
        page_count=1,
        full_text="\n".join(block.text for block in blocks),
        text_blocks=list(blocks),
    )


def test_enrich_measures_majority_fill_colors(tmp_path: Path) -> None:
    pdf = build_styled_target_pdf(tmp_path / "styled.pdf")
    evidence = _evidence(
        # "Alex Example" 24pt bold, fill (0.12, 0.2, 0.35) at x 220-340, top 35-59
        _block("b.title", "Alex Example", x0=0.34, top=0.03, x1=0.60, bottom=0.09),
        # body 10pt, fill black at top 130-140
        _block("b.body", "Engineer building reliable data systems.", x0=0.34, top=0.15, x1=0.95, bottom=0.19),
        # sidebar white text at x 24-33, top 91-105
        _block("b.side", "CONTACT", x0=0.02, top=0.11, x1=0.10, bottom=0.14),
        # empty region — no chars, stays unmeasured
        _block("b.void", "ghost", x0=0.30, top=0.50, x1=0.90, bottom=0.55),
    )

    enriched = enrich_colors_from_local_pdf(evidence, pdf)

    by_id = {block.element_id: block for block in enriched.text_blocks}
    assert by_id["b.title"].color_hex == "#1F3359"
    assert by_id["b.body"].color_hex == "#000000"
    assert by_id["b.side"].color_hex == "#FFFFFF"
    assert by_id["b.void"].color_hex is None  # still fails the bridge closed
    for key in ("b.title", "b.body", "b.side"):
        provenance = by_id[key].provenance["color_hex"]
        assert provenance.source == "local_pdf"
        assert provenance.provider == "pdfplumber"
        assert provenance.source_element_id == key
    assert _measure_dominant_text_size(pdf) == 10.0
    assert _body_size_failure(10.0, 10.2) is None
    assert _body_size_failure(10.0, 10.3) == (
        "dominant body size drift=0.30pt exceeds 0.25pt"
    )


def test_provider_measured_colors_are_never_overwritten(tmp_path: Path) -> None:
    pdf = build_styled_target_pdf(tmp_path / "styled.pdf")
    evidence = _evidence(
        _block("b.title", "Alex Example", x0=0.34, top=0.03, x1=0.60, bottom=0.09, color_hex="#111111"),
    )

    enriched = enrich_colors_from_local_pdf(evidence, pdf)

    assert enriched.text_blocks[0].color_hex == "#111111"
    assert "color_hex" not in enriched.text_blocks[0].provenance


def test_enrich_records_only_long_horizontal_rules(tmp_path: Path) -> None:
    pdf = build_pipe_contact_header_target_pdf(tmp_path / "rules.pdf")

    enriched = enrich_rules_from_local_pdf(_evidence(), pdf)

    assert len(enriched.rules) == 2
    assert enriched.graphic_count == 2
    assert {rule.color_hex for rule in enriched.rules} == {"#000000"}
    assert {rule.stroke_width_pt for rule in enriched.rules} == {0.75}
    assert all(rule.provenance.source == "local_pdf" for rule in enriched.rules)
    assert all(rule.gap_above_pt is not None for rule in enriched.rules)
    assert all(rule.gap_below_pt is not None for rule in enriched.rules)

    geometry = _measure_pdf_rules(pdf)
    assert geometry["count"] == 2
    assert geometry["heading"]["length_pt"] == 463
    assert _rule_geometry_failures(geometry, geometry) == []
    drifted = {
        **geometry,
        "heading": {**geometry["heading"], "length_pt": 464.1},
    }
    assert _rule_geometry_failures(geometry, drifted) == [
        "heading.length_pt drift=1.10pt exceeds 1.0pt"
    ]


def test_enrich_records_partial_heading_rule_target_without_filling_gaps(
    tmp_path: Path,
) -> None:
    pdf = build_partial_heading_rules_target_pdf(tmp_path / "partial-rules.pdf")

    enriched = enrich_rules_from_local_pdf(_evidence(), pdf)

    assert len(enriched.rules) == 3  # one header plus two of three headings
    assert sorted(rule.stroke_width_pt for rule in enriched.rules) == [0.75, 0.75, 1.25]


def test_structure_gate_rejects_heading_width_drift_over_one_point() -> None:
    structure = {
        "header_lines": [],
        "section_labels": [
            {"text": "SKILLS", "size_pt": 12.0, "width_pt": 47.4}
        ],
        "entry_title_size_pt": None,
    }
    assert _structure_failures(
        structure,
        {
            **structure,
            "section_labels": [
                {"text": "SKILLS", "size_pt": 12.0, "width_pt": 48.3}
            ],
        },
    ) == []
    # Width tolerance is re-baselined to the HTML→Adobe path (3.5pt, ADR 0006
    # / BACKEND_ROADMAP B3); a 0.9pt drift must still pass and a 1.10pt drift
    # still fails (no loosening below the evidence floor).
    assert _structure_failures(
        structure,
        {
            **structure,
            "section_labels": [
                {"text": "SKILLS", "size_pt": 12.0, "width_pt": 48.26}
            ],
        },
    ) == []
    assert _structure_failures(
        structure,
        {
            **structure,
            "section_labels": [
                {"text": "SKILLS", "size_pt": 12.0, "width_pt": 51.0}
            ],
        },
    ) == ["section_labels[0].width drift=3.60pt exceeds 3.50pt"]


def test_matrix_failure_review_retains_error_without_placeholder_output(tmp_path: Path, monkeypatch) -> None:
    """A rejected API run stays inspectable and cannot appear as a completed PDF."""
    import csv
    import json
    import re
    from scripts import run_abc_live_matrix as matrix

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for label in ("A", "B"):
        build_styled_target_pdf(inputs / f"resume_{label}.pdf")
    monkeypatch.setattr(matrix, "DATASET_DIR", inputs)
    run = tmp_path / "run"
    run.mkdir()
    report = {"pairs_requested": ["A:B"], "pairs": [{
        "pair": "A→B", "artifact_id": None, "files": {}, "observations": [],
        "steps": {"process_source": {"status": "fail", "http_status": 502,
                  "error": "candidate_segmentation_failed", "elapsed_seconds": 1}},
    }]}
    matrix._write_review(run, report)
    manifest = json.loads((run / "run_manifest.json").read_text())
    result = json.loads((run / "transformations/resume_a__to__resume_b/result.json").read_text())
    assert manifest["state"] == "failed"
    assert manifest["review_policy"]["final_output_acceptance"] == "human_pending"
    assert result["status"] == "failed" and result["generated_pdf"] is None
    assert result["errors"][0]["http_status"] == 502
    assert not list((run / "transformations").rglob("candidate_profile.pdf"))
    with (run / "transformation_matrix.csv").open() as output:
        rows = list(csv.DictReader(output))
    assert len(rows) == 1 and rows[0]["error_message"] == "candidate_segmentation_failed"
    index = (run / "RESULTS_INDEX.md").read_text()
    assert "| [ ] | 1 |" in index and "content 0/1" in index
    assert all((run / target).exists() for target in re.findall(r"\]\(([^)]+)\)", index))


def test_matrix_stale_target_replay_stops_before_any_api_call(tmp_path: Path, monkeypatch) -> None:
    import json
    import sys
    import pytest
    from scripts import run_abc_live_matrix as matrix

    (tmp_path / "resume_B.pdf").write_bytes(b"selected target bytes")
    cache = tmp_path / "saved"
    cache.mkdir()
    (cache / "target.pdf").write_bytes(b"different target bytes")
    (cache / "target_format.json").write_text(json.dumps({"stored_filename": "target.pdf"}))
    (cache / "target_layout_evidence.json").write_text(json.dumps({"target_checksum": "0" * 64}))
    monkeypatch.setattr(matrix, "DATASET_DIR", tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("API_LLM_PROVIDER", raising=False)
    monkeypatch.setattr(sys, "argv", ["matrix", "--pairs", "E:B", "--target-evidence", str(cache)])
    def unexpected(*args, **kwargs):
        pytest.fail("A stale target replay must not reach an API request")
    monkeypatch.setattr(matrix, "_request", unexpected)
    with pytest.raises(ValueError, match="checksum does not match"):
        matrix.main()
    assert not (tmp_path / "runs").exists()


def test_matrix_retains_successful_api_receipt_with_raw_target_differences(tmp_path: Path, monkeypatch) -> None:
    """API receipts are stubbed; actual differing PDFs still produce review diagnostics."""
    import json
    import shutil
    from scripts import run_abc_live_matrix as matrix
    from tests.helpers.synthetic_pdf import build_text_pdf_pages

    for key in ("E", "B"):
        build_styled_target_pdf(tmp_path / f"resume_{key}.pdf")
    artifact = tmp_path / "artifacts" / "artifact_matrix"
    artifact.mkdir(parents=True)
    monkeypatch.setattr(matrix, "DATASET_DIR", tmp_path)
    monkeypatch.setattr(matrix, "GENERATED_OUTPUTS_DIR", artifact.parent)
    for name in ("normalized_layout_evidence.json", "target_layout_evidence.json", "visual_comparison.json"):
        (artifact / name).write_text("{}")
    for name in ("content_validation.json", "structure_validation.json"):
        (artifact / name).write_text(json.dumps({"passed": True}))
    spec = artifact / "designs/d1/layout_template_spec.json"
    spec.parent.mkdir(parents=True)
    spec.write_text("{}")
    (artifact / "candidate_profile.html").write_text("<h1>Candidate Beta</h1>")
    build_text_pdf_pages(artifact / "candidate_profile.pdf", ["Candidate Beta", "Different approved content"],
        width_pt=612, height_pt=792, margin_top_pt=36, margin_left_pt=36,
        margin_bottom_pt=36, margin_right_pt=36)
    variants = []
    for variant in ("short", "medium", "long"):
        proof = artifact / "layout_proofs" / variant
        proof.mkdir(parents=True)
        (proof / "proof.html").write_text("<h1>SYNTHETIC</h1>")
        shutil.copy2(artifact / "candidate_profile.pdf", proof / "proof.pdf")
        variants.append({"variant": variant, "proof_id": variant,
                         "html_filename": "proof.html", "pdf_filename": "proof.pdf"})

    def receipts(_client, result, step, _method, path, **_kwargs):
        result["steps"][step] = {"status": "pass", "http_status": 200, "elapsed_seconds": 0.25}
        if step == "server":
            return {"status": "ok"}
        if step == "process_source":
            return {"artifact_id": artifact.name, "profile_revision_id": "initial",
                    "profile": {"full_name": "Candidate Beta"}}
        if step == "save_revision":
            return {"profile_revision_id": "saved"}
        if step == "upload_target":
            return {"target_format": {"used_as_template_source": True}}
        if step == "request_design":
            return {"design_id": "d1", "state": "ready"}
        if step == "approve_design" or path.endswith("/approve"):
            return {"state": "approved"}
        if step == "layout_proofs":
            return {"variants": variants}
        assert step in {"generate", "generate_blind"}
        return {**dict.fromkeys(("profile_revision_id", "profile_sha256", "layout_template_version",
                 "layout_spec_sha256", "html_sha256", "pdf_sha256"), "synthetic"), "client_ready": False}

    monkeypatch.setattr(matrix, "_request", receipts)
    result = matrix._run_pair(matrix.TestClient(matrix.app), "E", "B")
    assert result["steps"]["generate"] == {"status": "pass", "http_status": 200, "elapsed_seconds": 0.25}
    assert result["fidelity_diagnostics"]["issues"]
    assert result["fidelity_diagnostics"]["human_review"] == "pending"
    assert result["preview_identity"]["client_ready"] is False


def _chip(node_id: str, text: str, *, background: str = "#99F6E4", color: str = "#334155") -> str:
    return (
        f'<span data-node-id="{node_id}" style="display:inline-flex;align-items:center;'
        f'height:18pt;padding:0 9pt;border-radius:9pt;background:{background};'
        f'color:{color};">{text}</span>'
    )


def test_html_badge_measure_reads_rendered_chip_rows(tmp_path: Path) -> None:
    html = "\n".join(
        [
            '<div data-node-id="section.02.badge_group.0">',
            '<div data-node-id="section.02.badge_group.0.row.0" style="display:flex;gap:6pt;">',
            _chip("section.02.badge.skills.group.0.chip.0", "React"),
            _chip("section.02.badge.skills.group.0.chip.1", "Next.js"),
            _chip("section.02.badge.skills.group.0.chip.2", "TypeScript"),
            "</div>",
            "</div>",
            '<div data-node-id="section.02.badge_group.1">',
            '<div data-node-id="section.02.badge_group.1.row.0" style="display:flex;gap:6pt;">',
            _chip("section.02.badge.skills.group.1.chip.0", "Node.js"),
            _chip("section.02.badge.skills.group.1.chip.1", "Express"),
            "</div>",
            '<div data-node-id="section.02.badge_group.1.row.1" style="display:flex;gap:6pt;">',
            _chip("section.02.badge.skills.group.1.chip.2", "GraphQL"),
            "</div>",
            "</div>",
        ]
    )
    path = tmp_path / "candidate_profile.html"
    path.write_text(html, encoding="utf-8")

    assert _measure_html_badges(path) == {
        "count": 6,
        "fill_colors": ["#99F6E4"],
        "text_colors": ["#334155"],
        "items_per_line": [3, 2, 1],
    }


def test_html_badge_measure_reports_zero_without_chip_nodes(tmp_path: Path) -> None:
    path = tmp_path / "candidate_profile.html"
    path.write_text('<div class="chip-row"><p>No chips here</p></div>', encoding="utf-8")

    assert _measure_html_badges(path) == {
        "count": 0,
        "fill_colors": [],
        "text_colors": [],
        "items_per_line": [],
    }


def test_html_badge_measure_counts_a_chip_matched_by_both_forms_once(tmp_path: Path) -> None:
    path = tmp_path / "candidate_profile.html"
    path.write_text(
        '<div class="chip-row" data-node-id="section.02.badge_group.0.row.0">'
        '<span class="chip-style" data-node-id="section.02.badge.skills.group.0.chip.0" '
        'style="background:#99F6E4;color:#334155;">React</span>'
        '<span class="chip-style" data-node-id="section.02.badge.skills.group.0.chip.1" '
        'style="background:#99F6E4;color:#334155;">SQL</span>'
        "</div>",
        encoding="utf-8",
    )

    assert _measure_html_badges(path) == {
        "count": 2,
        "fill_colors": ["#99F6E4"],
        "text_colors": ["#334155"],
        "items_per_line": [2],
    }


def test_enrich_measures_generated_rounded_chip_paths(tmp_path: Path) -> None:
    pdf = build_rounded_chip_target_pdf(tmp_path / "rounded.pdf")

    enriched = enrich_badges_from_local_pdf(_evidence(), pdf)

    assert enriched.warnings == []
    assert len(enriched.badge_clusters) == 1
    cluster = enriched.badge_clusters[0]
    # 18 rendered chips; the first chip's fill path is emitted twice on purpose.
    assert cluster.badge_count == 18
    assert cluster.items_per_line == [6, 6, 6]
    assert cluster.fill_color_hex == "#99F6E4"
    assert cluster.text_color_hex == "#334155"
    assert cluster.height_pt == pytest.approx(18.0, abs=0.1)
    assert cluster.horizontal_padding_pt == pytest.approx(9.0, abs=0.2)
    assert cluster.provenance.source == "local_pdf"
    assert cluster.provenance.provider == "pdfplumber"


def test_enrich_leaves_rounded_decoration_unmeasured(tmp_path: Path) -> None:
    pdf = build_rounded_decoration_target_pdf(tmp_path / "decoration.pdf")

    enriched = enrich_badges_from_local_pdf(_evidence(), pdf)

    # The 400x24 pt banner (label inset 84 pt) and the 140x18 pt two-line shape are
    # decoration, never skills; the over-tall blob and the stroked rule are not chips.
    assert enriched.badge_clusters == []
    warnings = " ".join(enriched.warnings)
    # The 400x24 pt banner is a container: wider than six of its own heights with
    # its label filling only part of that width.
    assert "2 filled shape(s)" in warnings
    assert "400x24 pt shape is more than 6x wider than it is tall" in warnings
    assert "140x18 pt shape holds no single short text run" in warnings
    assert "60 pt" not in warnings
    assert "unmeasured pending inspection" in warnings


def test_enrich_warns_instead_of_raising_on_a_banner_sized_text_inset(
    tmp_path: Path,
) -> None:
    # Its label is inset 84 pt, outside the badge cluster's 0..72 pt range: the
    # shape must become a warning, never a ValidationError.
    pdf = build_banner_and_triangle_target_pdf(tmp_path / "banners.pdf")

    enriched = enrich_badges_from_local_pdf(_evidence(), pdf)

    assert enriched.badge_clusters == []
    warnings = " ".join(enriched.warnings)
    assert "400x24 pt shape is more than 6x wider than it is tall while its label spans only" in warnings
    assert "unmeasured pending inspection" in warnings


def test_enrich_leaves_banners_and_triangles_unmeasured(tmp_path: Path) -> None:
    # Three 400x24 pt banners (two rounded, one rectangular) and two 30x24 pt filled
    # triangles fill about half of their bounding boxes; none of them is a chip.
    pdf = build_banner_and_triangle_target_pdf(tmp_path / "decoration.pdf")

    enriched = enrich_badges_from_local_pdf(_evidence(), pdf)

    assert enriched.badge_clusters == []
    assert len(enriched.warnings) == 1
    warnings = enriched.warnings[0]
    assert "5 filled shape(s)" in warnings
    assert "400x24 pt shape is more than 6x wider than it is tall" in warnings
    assert "30x24 pt shape fills only 50% of its bounding box" in warnings
    assert "unmeasured pending inspection" in warnings


def test_enrich_reports_a_single_unclassified_rounded_shape(tmp_path: Path) -> None:
    pdf = build_multiline_rounded_shape_target_pdf(tmp_path / "multiline.pdf")

    enriched = enrich_badges_from_local_pdf(_evidence(), pdf)

    # One filled rounded shape alone is unmeasured, not "no badges": the warning
    # names the shape and its geometry so both readings stay distinguishable.
    assert enriched.badge_clusters == []
    assert len(enriched.warnings) == 1
    assert "1 filled shape(s)" in enriched.warnings[0]
    assert "140x24 pt shape holds no single short text run" in enriched.warnings[0]
    assert "unmeasured pending inspection" in enriched.warnings[0]


def test_enrich_measures_rectangular_chip_band(tmp_path: Path) -> None:
    pdf = build_tech_teal_palette_target_pdf(tmp_path / "teal.pdf")

    enriched = enrich_badges_from_local_pdf(_evidence(), pdf)

    assert len(enriched.badge_clusters) == 1
    cluster = enriched.badge_clusters[0]
    assert cluster.badge_count == 10
    assert cluster.items_per_line == [5, 5]
    assert cluster.fill_color_hex == "#99F6E4"
    assert cluster.text_color_hex == "#334155"
    assert cluster.height_pt == 20.0
    assert cluster.provenance.source == "local_pdf"
    assert cluster.provenance.provider == "pdfplumber"


PROOF_VARIANTS = ("short", "medium", "long")
CHIP_FILL = "#99F6E4"
CHIP_TEXT = "#334155"


def _badge_group_html(rows: list[int]) -> str:
    """Renderer-shaped chip markup: one badge group and one row container per row."""
    lines: list[str] = []
    chip_index = 0
    for group, count in enumerate(rows):
        lines.append(f'<div data-node-id="section.02.badge_group.{group}">')
        lines.append(
            f'<div data-node-id="section.02.badge_group.{group}.row.0" '
            'style="display:flex;gap:6pt;">'
        )
        for _ in range(count):
            node_id = f"section.02.badge.skills.group.{group}.chip.{chip_index}"
            lines.append(_chip(node_id, f"Skill {chip_index + 1}"))
            chip_index += 1
        lines.extend(["</div>", "</div>"])
    return "\n".join(lines)


def _badge_surface(count: int, rows: list[int]) -> dict:
    """One measured surface as the fixture built it, never as the detector reads it."""
    return {"count": count, "fill_colors": [CHIP_FILL], "text_colors": [CHIP_TEXT],
            "items_per_line": list(rows)}


def _agreeing_badge_surfaces(tmp_path: Path) -> dict[str, tuple[str, Path]]:
    """18 chips on every generated surface: three rows of six, as built."""
    pdf = build_rounded_chip_target_pdf(tmp_path / "chips_18.pdf", chip_count=18, per_row=6)
    html = _badge_group_html([6, 6, 6])
    return {"ordinary": (html, pdf), "blind": (html, pdf)}


def _matrix_badge_artifact(tmp_path: Path, monkeypatch, *, target_pdf: Path,
                           ordinary_pdf: Path) -> Path:
    """Artifact holding the analysis files a stubbed pair reads; return its directory."""
    import json
    import shutil
    from app.extraction.candidate_document_analyzer import NormalizedCandidateDocument
    from app.template_analysis.visual_comparator import compare_pdf_layouts
    from scripts import run_abc_live_matrix as matrix
    from tests.helpers.pipeline_layout import product_template

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    build_styled_target_pdf(inputs / "resume_E.pdf")
    shutil.copy2(target_pdf, inputs / "resume_B.pdf")
    artifact = tmp_path / "run/transformations/resume_e__to__resume_b/artifacts/artifact_matrix"
    artifact.mkdir(parents=True)
    monkeypatch.setattr(matrix, "DATASET_DIR", inputs)
    monkeypatch.setattr(matrix, "GENERATED_OUTPUTS_DIR", artifact.parent)
    files = {
        "normalized_layout_evidence.json": "{}", "target_layout_evidence.json": "{}",
        "content_validation.json": json.dumps({"passed": True}),
        "structure_validation.json": json.dumps({"passed": True}),
        "client_render_context.json": "{}", "production_render_plan.json": "{}",
        "normalized_candidate_document.json": NormalizedCandidateDocument(
            source_text_sha256="0" * 64, blocks=[]).model_dump_json(),
        "designs/d1/layout_template_spec.json": product_template().model_dump_json(),
    }
    for name, content in files.items():
        (artifact / name).parent.mkdir(parents=True, exist_ok=True)
        (artifact / name).write_text(content)
    shutil.copy2(ordinary_pdf, artifact / "candidate_profile.pdf")
    # A live run leaves the comparator report and its diff page in the artifact.
    compare_pdf_layouts(inputs / "resume_B.pdf", artifact / "candidate_profile.pdf", artifact)
    for variant in PROOF_VARIANTS:
        proof = artifact / "layout_proofs" / variant
        proof.mkdir(parents=True)
        (proof / "proof.html").write_text("<h1>SYNTHETIC</h1>")
        shutil.copy2(target_pdf, proof / "proof.pdf")
    return artifact


def _matrix_badge_receipts(artifact: Path, surfaces: dict[str, tuple[str, Path]],
                           calls: list[tuple[str, bool | None]],
                           writes: dict[str, dict]):
    """Stub every API step; each generate writes the bytes its surface fixture built."""
    import hashlib
    import shutil

    identity = dict.fromkeys(("profile_revision_id", "profile_sha256",
                              "layout_template_version", "layout_spec_sha256"), "synthetic")
    replies = {
        "server": {"status": "ok"},
        "process_source": {"artifact_id": artifact.name, "profile_revision_id": "initial",
                           "profile": {"full_name": "Candidate Beta"}},
        "save_revision": {"profile_revision_id": "saved"},
        "upload_target": {"target_format": {"used_as_template_source": True}},
        "request_design": {"design_id": "d1", "state": "ready"},
        "approve_design": {"state": "approved"},
        "layout_proofs": {"variants": [
            {"variant": variant, "proof_id": variant, "html_filename": "proof.html",
             "pdf_filename": "proof.pdf"} for variant in PROOF_VARIANTS]},
    }

    def receipts(_client, result, step, _method, path, **kwargs):
        result["steps"][step] = {"status": "pass", "http_status": 200, "elapsed_seconds": 0.0}
        if step not in {"generate", "generate_blind"}:
            if path.endswith("/approve"):
                return {"state": "approved"}
            return replies[step]
        calls.append((step, kwargs["json"].get("blind_profile")))
        html, pdf = surfaces[step]
        (artifact / "candidate_profile.html").write_text(html, encoding="utf-8")
        shutil.copy2(pdf, artifact / "candidate_profile.pdf")
        writes[step] = {
            **identity,
            "html_sha256": hashlib.sha256(
                (artifact / "candidate_profile.html").read_bytes()).hexdigest(),
            "pdf_sha256": hashlib.sha256(
                (artifact / "candidate_profile.pdf").read_bytes()).hexdigest(),
            "client_ready": False,
        }
        return writes[step]

    return receipts


def _run_matrix_badges(tmp_path: Path, monkeypatch, *, ordinary: tuple[str, Path],
                       blind: tuple[str, Path]) -> tuple[dict, list, dict, Path]:
    """Run one pair, with provider work stubbed and candidate surfaces built here."""
    from scripts import run_abc_live_matrix as matrix

    target_pdf = build_rounded_chip_target_pdf(
        tmp_path / "resume_B.pdf", chip_count=18, per_row=6)
    artifact = _matrix_badge_artifact(
        tmp_path, monkeypatch, target_pdf=target_pdf, ordinary_pdf=ordinary[1])
    calls: list[tuple[str, bool | None]] = []
    writes: dict[str, dict] = {}
    surfaces = {"generate": ordinary, "generate_blind": blind}
    monkeypatch.setattr(
        matrix, "_request", _matrix_badge_receipts(artifact, surfaces, calls, writes))
    result = matrix._run_pair(matrix.TestClient(matrix.app), "E", "B")
    return result, calls, writes, artifact


def test_matrix_badge_report_lists_every_generated_surface(tmp_path: Path, monkeypatch) -> None:
    surfaces = _agreeing_badge_surfaces(tmp_path)

    result, _, _, _ = _run_matrix_badges(
        tmp_path, monkeypatch, ordinary=surfaces["ordinary"], blind=surfaces["blind"])

    assert list(result["badges"]) == [
        "target", "generated_html", "generated_pdf",
        "generated_blind_html", "generated_blind_pdf",
    ]
    # The retained target-vs-generated message keeps its shape and its reader.
    assert _badge_failures(_badge_surface(18, [6, 6, 6]),
                           _badge_surface(12, [6, 6])) == [
        "badge count target=18 generated=12",
        "badge items_per_line target=[6, 6, 6] generated=[6, 6]",
    ]


def test_matrix_measures_generated_pdf_badges(tmp_path: Path, monkeypatch) -> None:
    surfaces = _agreeing_badge_surfaces(tmp_path)

    result, _, _, _ = _run_matrix_badges(
        tmp_path, monkeypatch, ordinary=surfaces["ordinary"], blind=surfaces["blind"])

    # 18 chips in three rows of six: the builder's chip_count/per_row and the markup.
    assert result["badges"]["generated_pdf"] == {
        "count": 18, "fill_colors": [CHIP_FILL], "text_colors": [CHIP_TEXT],
        "items_per_line": [6, 6, 6],
    }


def test_matrix_names_generated_html_pdf_badge_drift(tmp_path: Path, monkeypatch) -> None:
    ordinary_pdf = build_rounded_chip_target_pdf(
        tmp_path / "chips_12.pdf", chip_count=12, per_row=6)

    result, _, _, _ = _run_matrix_badges(
        tmp_path, monkeypatch, ordinary=(_badge_group_html([6, 6, 6]), ordinary_pdf),
        blind=(_badge_group_html([6, 6, 6]), ordinary_pdf))

    issues = result["fidelity_diagnostics"]["issues"]
    assert "generated badge count html=18 pdf=12" in issues
    assert [issue for issue in issues if "target=" in issue] == []


def test_matrix_compares_blind_and_ordinary_badges(tmp_path: Path, monkeypatch) -> None:
    ordinary_pdf = build_rounded_chip_target_pdf(
        tmp_path / "chips_18.pdf", chip_count=18, per_row=6)
    blind_pdf = build_rounded_chip_target_pdf(
        tmp_path / "chips_17.pdf", chip_count=17, per_row=6)

    result, _, _, _ = _run_matrix_badges(
        tmp_path, monkeypatch, ordinary=(_badge_group_html([6, 6, 6]), ordinary_pdf),
        blind=(_badge_group_html([6, 6, 5]), blind_pdf))

    assert "blind badge count ordinary=18 blind=17" in result["fidelity_diagnostics"]["issues"]
    assert result["badges"]["generated_blind_pdf"]["count"] == 17


def test_matrix_reports_no_generated_surface_drift_when_surfaces_agree(
    tmp_path: Path, monkeypatch
) -> None:
    surfaces = _agreeing_badge_surfaces(tmp_path)

    result, _, _, _ = _run_matrix_badges(
        tmp_path, monkeypatch, ordinary=surfaces["ordinary"], blind=surfaces["blind"])

    assert [issue for issue in result["fidelity_diagnostics"]["issues"] if "badge" in issue] == []


def test_matrix_compares_badge_rows_as_a_multiset() -> None:
    # HTML groups rows by node container, the PDF clusters per page: the same
    # row counts in another order are not a drift.
    assert _surface_drift(
        _badge_surface(6, [3, 2, 1]), _badge_surface(6, [1, 3, 2]),
        scope="generated", left_label="html", right_label="pdf") == []
    # A different grouping of fewer chips is one row drift plus the count drift.
    drift = _surface_drift(
        _badge_surface(18, [6, 6, 6]), _badge_surface(15, [4, 4, 6, 1]),
        scope="generated", left_label="html", right_label="pdf")
    assert [entry for entry in drift if "items_per_line" in entry] == [
        "generated badge items_per_line html=[6, 6, 6] pdf=[1, 4, 4, 6]",
    ]
    assert "generated badge count html=18 pdf=15" in drift


def test_matrix_generates_blind_before_ordinary(tmp_path: Path, monkeypatch) -> None:
    import hashlib

    ordinary_pdf = build_rounded_chip_target_pdf(
        tmp_path / "chips_18.pdf", chip_count=18, per_row=6)
    blind_pdf = build_rounded_chip_target_pdf(
        tmp_path / "chips_17.pdf", chip_count=17, per_row=6)

    result, calls, _, artifact = _run_matrix_badges(
        tmp_path, monkeypatch, ordinary=(_badge_group_html([6, 6, 6]), ordinary_pdf),
        blind=(_badge_group_html([6, 6, 5]), blind_pdf))

    assert calls == [("generate_blind", True), ("generate", None)]
    # The ordinary render runs last, so the artifact keeps the ordinary bytes.
    assert hashlib.sha256(
        (artifact / "candidate_profile.html").read_bytes()).hexdigest() == \
        result["preview_identity"]["html_sha256"]
    assert hashlib.sha256(
        (artifact / "candidate_profile.pdf").read_bytes()).hexdigest() == \
        result["preview_identity"]["pdf_sha256"]


def test_matrix_review_projection_keeps_blind_and_ordinary_outputs(
    tmp_path: Path, monkeypatch
) -> None:
    import hashlib
    from scripts import run_abc_live_matrix as matrix

    ordinary_pdf = build_rounded_chip_target_pdf(
        tmp_path / "chips_18.pdf", chip_count=18, per_row=6)
    blind_pdf = build_rounded_chip_target_pdf(
        tmp_path / "chips_17.pdf", chip_count=17, per_row=6)

    result, _, writes, artifact = _run_matrix_badges(
        tmp_path, monkeypatch, ordinary=(_badge_group_html([6, 6, 6]), ordinary_pdf),
        blind=(_badge_group_html([6, 6, 5]), blind_pdf))
    run = artifact.parents[3]
    matrix._write_review(run, {"pairs_requested": ["E:B"], "pairs": [result]})

    generation = artifact.parents[1] / "generation"
    for name, response in (("candidate_profile.html", writes["generate"]),
                           ("candidate_profile.pdf", writes["generate"]),
                           ("blind/candidate_profile.html", writes["generate_blind"]),
                           ("blind/candidate_profile.pdf", writes["generate_blind"])):
        digest = hashlib.sha256((generation / name).read_bytes()).hexdigest()
        assert digest == response[name.rsplit(".", 1)[1] + "_sha256"]


def test_enrich_measures_wide_padded_and_square_chips(tmp_path: Path) -> None:
    """A chip is a label with padding: 200x18 with a 9 pt inset, 100x18 with a
    25 pt inset, and an 18x18 rounded square labelled R are all chips."""
    pdf = build_wide_padded_and_square_chip_pdf(tmp_path / "chips.pdf")

    enriched = enrich_badges_from_local_pdf(_evidence(), pdf)

    assert enriched.warnings == []
    assert len(enriched.badge_clusters) == 1
    cluster = enriched.badge_clusters[0]
    assert cluster.badge_count == 3  # the three pills this probe draws
    assert cluster.items_per_line == [3]
    assert cluster.fill_color_hex == "#99F6E4"
    assert cluster.text_color_hex == "#334155"
    assert cluster.height_pt == pytest.approx(18.0, abs=0.1)


def test_enrich_rejects_section_banners_as_containers(tmp_path: Path) -> None:
    """Two 200x24 pt banners labelled EXPERIENCE at a 9 pt inset are a column
    container, not a chip, and each banner is named in the warning."""
    import re

    pdf = build_section_banner_pdf(tmp_path / "banners.pdf")

    enriched = enrich_badges_from_local_pdf(_evidence(), pdf)

    assert enriched.badge_clusters == []
    warnings = " ".join(enriched.warnings)
    assert "2 filled shape(s)" in warnings
    assert warnings.count("200x24 pt shape is more than 6x wider than it is tall") == 2
    assert len(set(re.findall(r"\(at (x=[^)]*)\)", warnings))) == 2
    assert "unmeasured pending inspection" in warnings


def test_enrich_rejects_a_chip_whose_label_hangs_over_its_edge(
    tmp_path: Path,
) -> None:
    """Two 60x18 pt pills whose label starts 2 pt outside the left edge are
    warned about, never measured with the negative inset clamped to 0."""
    pdf = build_overflowing_chip_pdf(tmp_path / "overflow.pdf")

    enriched = enrich_badges_from_local_pdf(_evidence(), pdf)

    assert enriched.badge_clusters == []
    warnings = " ".join(enriched.warnings)
    assert "2 filled shape(s)" in warnings
    assert warnings.count("60x18 pt shape has its text run 2 pt outside its own edge") == 2
    assert "unmeasured pending inspection" in warnings


def test_enrich_warns_instead_of_raising_on_a_label_inset_past_the_ceiling(
    tmp_path: Path,
) -> None:
    """Two 300x24 pt bars whose centred label spans 51% of the width are inset
    73.3 pt, past the badge padding bound: a warning, never a ValidationError."""
    import re

    pdf = build_deep_label_inset_bars_pdf(tmp_path / "deep-inset.pdf")

    enriched = enrich_badges_from_local_pdf(_evidence(), pdf)

    assert enriched.badge_clusters == []
    warnings = " ".join(enriched.warnings)
    assert "2 filled shape(s)" in warnings
    reason = (
        "300x24 pt shape holds a label inset 73.3 pt, "
        "past the 72 pt badge padding ceiling"
    )
    assert warnings.count(reason) == 2
    assert len(set(re.findall(r"\(at (x=[^)]*)\)", warnings))) == 2
    assert "unmeasured pending inspection" in warnings


def test_enrich_names_an_outlined_in_band_shape(tmp_path: Path) -> None:
    """A 120x20 pt rounded outline carries a stroke and no fill, so it is named
    as unmeasured instead of being skipped silently."""
    pdf = build_outlined_chip_pdf(tmp_path / "outline.pdf")

    enriched = enrich_badges_from_local_pdf(_evidence(), pdf)

    assert enriched.badge_clusters == []
    warnings = " ".join(enriched.warnings)
    assert "1 shape(s) between 10 and 30 pt high carry no fill colour" in warnings
    assert "120x20 pt outline" in warnings
    assert "unmeasured pending inspection" in warnings
