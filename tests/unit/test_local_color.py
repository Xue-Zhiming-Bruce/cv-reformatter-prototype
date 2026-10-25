"""Unit tests for local-PDF text-fill-color measurement."""

from __future__ import annotations

from pathlib import Path

from app.template_analysis.commercial.local_color import (
    _hex_from_color,
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
    _body_size_failure,
    _measure_dominant_text_size,
    _measure_pdf_rules,
    _rule_geometry_failures,
    _structure_failures,
)
from tests.helpers.synthetic_pdf import (
    build_partial_heading_rules_target_pdf,
    build_pipe_contact_header_target_pdf,
    build_styled_target_pdf,
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
        assert step == "generate"
        return {**dict.fromkeys(("profile_revision_id", "profile_sha256", "layout_template_version",
                 "layout_spec_sha256", "html_sha256", "pdf_sha256"), "synthetic"), "client_ready": False}

    monkeypatch.setattr(matrix, "_request", receipts)
    result = matrix._run_pair(matrix.TestClient(matrix.app), "E", "B")
    assert result["steps"]["generate"] == {"status": "pass", "http_status": 200, "elapsed_seconds": 0.25}
    assert result["fidelity_diagnostics"]["issues"]
    assert result["fidelity_diagnostics"]["human_review"] == "pending"
    assert result["preview_identity"]["client_ready"] is False
