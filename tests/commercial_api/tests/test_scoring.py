from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.commercial_api.corpus import case_by_id, load_corpus_manifest
from tests.commercial_api.scoring import (
    evaluate_extraction,
    evaluate_layout,
    evaluate_rendering,
    render_context_terms,
)
from tests.commercial_api.corpus import CORPUS_DIR


@pytest.fixture(scope="module")
def manifest():
    return load_corpus_manifest()


def _expected_profile_json() -> dict:
    return json.loads(
        (CORPUS_DIR / "expected_candidate_profile.json").read_text(encoding="utf-8")
    )


def test_extraction_perfect_profile_passes_all_gates(manifest) -> None:
    case = case_by_id(manifest, "synthetic_candidate_profile")
    metrics, gates = evaluate_extraction(_expected_profile_json(), case)
    assert metrics["field_accuracy"] == 1.0
    assert metrics["null_safety"] == 1.0
    assert metrics["missing_field_accuracy"] == 1.0
    assert all(g.outcome == "pass" for g in gates)


def test_extraction_invented_unsupported_fact_fails_null_safety_gate(manifest) -> None:
    case = case_by_id(manifest, "synthetic_candidate_profile")
    tampered = _expected_profile_json()
    tampered["salary_expectation"] = "SGD 999,999"
    metrics, gates = evaluate_extraction(tampered, case)
    by_name = {g.gate: g for g in gates}
    assert "salary_expectation" in metrics["invented_null_fields"]
    assert by_name["null_safety"].outcome == "fail"
    assert by_name["content_preservation"].outcome == "pass"


def test_layout_perfect_evidence_passes_deterministic_gates(manifest) -> None:
    case = case_by_id(manifest, "plain_one_column")
    evidence = {
        "provider": "synthetic",
        "page_count": 1,
        "full_text": "\n".join(case.expected_content),
        "text_blocks": [
            {
                "text": text,
                "page_number": 1,
                "bbox": {"x0": 0.05, "top": 0.1, "x1": 0.8, "bottom": 0.12},
                "role": "heading" if text.isupper() else "body",
                "font_size": 11.0,
            }
            for text in case.expected_content
        ]
        + [
            {
                "text": f"body {index}",
                "page_number": 1,
                "bbox": {"x0": 0.05, "top": 0.2, "x1": 0.8, "bottom": 0.22},
                "role": "body",
                "font_size": 11.0,
            }
            for index in range(3)
        ],
        "style_record_count": 1,
    }
    metrics, gates = evaluate_layout(evidence, case)
    assert metrics["required_text_recall"] == 1.0
    assert metrics["inferred_columns"] == 1
    assert all(g.outcome == "pass" for g in gates)


def test_layout_missing_required_text_fails_text_recall_gate(manifest) -> None:
    case = case_by_id(manifest, "styled_two_column")
    evidence = {
        "provider": "synthetic",
        "page_count": 1,
        "full_text": "unrelated content only",
        "text_blocks": [
            {"text": "unrelated", "page_number": 1, "bbox": {"x0": 0.1, "top": 0.1, "x1": 0.3, "bottom": 0.2}, "role": "body", "font_size": 10.0}
        ],
        "style_record_count": 0,
    }
    metrics, gates = evaluate_layout(evidence, case)
    by_name = {g.gate: g for g in gates}
    assert by_name["text_recall"].outcome == "fail"
    assert metrics["required_text_recall"] < 1.0


def test_layout_scores_semantic_roles_from_paragraphs(manifest) -> None:
    case = case_by_id(manifest, "styled_two_column")
    headings = [text for text in case.expected_content if text.isupper()]
    evidence = {
        "provider": "synthetic",
        "page_count": 1,
        "full_text": "\n".join(case.expected_content),
        "text_blocks": [
            {
                "text": text,
                "page_number": 1,
                "bbox": {"x0": 0.1, "top": 0.1, "x1": 0.4, "bottom": 0.2},
                "role": None,
            }
            for text in case.expected_content
        ],
        "paragraphs": [
            {"text": text, "role": "sectionHeading"}
            for text in headings
        ],
        "style_record_count": 1,
    }

    metrics, _ = evaluate_layout(evidence, case)

    assert metrics["semantic_heading_recall"] == 1.0
    assert metrics["role_coverage"] == 1.0


def test_rendering_gates_content_privacy_and_structure(manifest, tmp_path: Path) -> None:
    case = case_by_id(manifest, "controlled_docx_to_pdf")
    pdf_path = tmp_path / "out.pdf"
    # Minimal valid PDF with one text page.
    from tests.helpers.synthetic_pdf import build_plain_target_pdf

    build_plain_target_pdf(pdf_path)
    expected_terms = {"Jordan Example", "Synthetic Systems"}
    metrics, gates = evaluate_rendering(
        pdf_path,
        case,
        expected_terms=expected_terms,
        privacy_exclusions=["Alex Example"],
        visual_similarity=0.9,
    )
    by_name = {g.gate: g for g in gates}
    assert by_name["content_preservation"].outcome == "fail"  # terms absent
    assert by_name["privacy"].outcome == "pass"  # no leak
    assert by_name["structural_validity"].outcome == "pass"
    assert by_name["visual_similarity"].outcome == "not_evaluated"
    assert by_name["visual_similarity"].kind == "comparative"


def test_rendering_privacy_gate_fails_on_target_fact_leak(manifest, tmp_path: Path) -> None:
    case = case_by_id(manifest, "controlled_docx_to_pdf")
    pdf_path = tmp_path / "leak.pdf"
    from tests.helpers.synthetic_pdf import build_styled_target_pdf

    build_styled_target_pdf(pdf_path)  # contains Alex Example etc.
    metrics, gates = evaluate_rendering(
        pdf_path,
        case,
        expected_terms={"Jordan Example"},
        privacy_exclusions=list(case.privacy_exclusions),
    )
    by_name = {g.gate: g for g in gates}
    assert by_name["privacy"].outcome == "fail"
    assert metrics["privacy_leaks"]
    # A content pass cannot rescue a privacy failure: gates are independent.
    assert by_name["content_preservation"].outcome == "fail"


def test_render_context_terms_flattens_nested_values() -> None:
    terms = render_context_terms(
        {"a": {"b": "hello world"}, "c": ["one", "two"], "d": 5, "e": ""}
    )
    assert terms == {"hello world", "one", "two"}
