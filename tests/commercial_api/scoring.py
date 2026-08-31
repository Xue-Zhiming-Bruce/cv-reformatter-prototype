from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.extraction.candidate_schema import CandidateProfile
from app.validation.missing_fields import apply_missing_field_detection
from tests.commercial_api.corpus import CORPUS_DIR
from tests.commercial_api.models import CorpusCase, GateDecision


def evaluate_extraction(
    actual_json: dict[str, Any],
    case: CorpusCase,
) -> tuple[dict[str, Any], list[GateDecision]]:
    """Score one extracted candidate profile against the expected profile.

    Gates are independent: content preservation, null safety (no invented
    facts), and missing-field accuracy. There is no blended total score.
    """
    if not case.expected_profile:
        raise ValueError(f"Case {case.case_id} declares no expected_profile.")
    expected = apply_missing_field_detection(
        CandidateProfile.model_validate_json(
            (CORPUS_DIR / case.expected_profile).read_text(encoding="utf-8")
        )
    )
    actual = CandidateProfile.model_validate(actual_json)
    actual = apply_missing_field_detection(actual)

    expected_payload = expected.model_dump(mode="json")
    actual_payload = actual.model_dump(mode="json")
    ignored_roots = {"missing_fields", "client_display_rules"}
    expected_non_null = {
        path: value
        for path, value in _flatten_scalars(expected_payload).items()
        if path.split(".", 1)[0] not in ignored_roots and value is not None
    }
    expected_null = {
        path
        for path, value in _flatten_scalars(expected_payload).items()
        if path.split(".", 1)[0] not in ignored_roots and value is None
    }
    actual_scalars = _flatten_scalars(actual_payload)
    matched = [
        path
        for path, expected_value in expected_non_null.items()
        if _normalized(actual_scalars.get(path)) == _normalized(expected_value)
    ]
    invented = [
        path
        for path in expected_null
        if actual_scalars.get(path) not in (None, "", [])
    ]
    expected_missing = {item.field_name for item in expected.missing_fields}
    actual_missing = {item.field_name for item in actual.missing_fields}
    union = expected_missing | actual_missing
    missing_accuracy = len(expected_missing & actual_missing) / len(union) if union else 1.0
    field_accuracy = len(matched) / max(len(expected_non_null), 1)
    null_safety = 1.0 - len(invented) / max(len(expected_null), 1)

    metrics: dict[str, Any] = {
        "expected_supported_scalar_count": len(expected_non_null),
        "matched_supported_scalar_count": len(matched),
        "field_accuracy": round(field_accuracy, 4),
        "expected_null_scalar_count": len(expected_null),
        "invented_null_fields": sorted(invented),
        "null_safety": round(null_safety, 4),
        "expected_missing_fields": sorted(expected_missing),
        "actual_missing_fields": sorted(actual_missing),
        "missing_field_accuracy": round(missing_accuracy, 4),
    }
    thresholds = case.thresholds
    gates = [
        _gate(
            "content_preservation",
            field_accuracy,
            thresholds.get("field_accuracy_min", 1.0),
        ),
        _gate("null_safety", null_safety, thresholds.get("null_safety_min", 1.0)),
        _gate(
            "missing_field_detection",
            missing_accuracy,
            thresholds.get("missing_field_accuracy_min", 1.0),
        ),
    ]
    return metrics, gates


def evaluate_layout(
    evidence_json: dict[str, Any],
    case: CorpusCase,
) -> tuple[dict[str, Any], list[GateDecision]]:
    """Score one normalized layout evidence record against the case.

    Deterministic gates: required-text recall, page count, and column
    classification. Semantic heading recall, bounds coverage, and style records
    are comparative metrics and never gates.
    """
    expected_layout = case.expected_layout
    required_text = case.expected_content
    full_text_normalized = _normalized(evidence_json.get("full_text") or "")
    matched_required = [
        text
        for text in required_text
        if _normalized(text) in full_text_normalized
    ]
    text_recall = len(matched_required) / max(len(required_text), 1)
    page_count = int(evidence_json.get("page_count") or 0)
    expected_page_count = int(expected_layout.get("page_count") or page_count)
    page_accuracy = 1.0 if page_count == expected_page_count else 0.0
    inferred_columns = _infer_column_count(evidence_json)
    expected_columns = int(expected_layout.get("columns") or 1)
    column_accuracy = 1.0 if inferred_columns == expected_columns else 0.0

    blocks = evidence_json.get("text_blocks") or []
    bounds_coverage = sum(bool(b.get("bbox")) for b in blocks) / max(len(blocks), 1)
    minimum_blocks = int(expected_layout.get("minimum_text_blocks") or 1)
    block_coverage = min(len(blocks) / max(minimum_blocks, 1), 1.0)
    distinct_font_sizes = {
        round(b["font_size"], 2) for b in blocks if b.get("font_size") is not None
    }
    minimum_font_sizes = int(expected_layout.get("minimum_distinct_font_sizes") or 1)
    measured_styles = max(len(distinct_font_sizes), int(evidence_json.get("style_record_count") or 0))
    style_coverage = min(measured_styles / max(minimum_font_sizes, 1), 1.0)
    role_blocks = evidence_json.get("paragraphs") or blocks
    role_coverage = sum(bool(b.get("role")) for b in role_blocks) / max(len(role_blocks), 1)
    expected_headings = [h for h in required_text if _looks_like_heading(h)]
    semantic_heading_recall = _semantic_heading_recall(role_blocks, expected_headings)

    metrics: dict[str, Any] = {
        "page_count": page_count,
        "expected_page_count": expected_page_count,
        "text_block_count": len(blocks),
        "required_text_recall": round(text_recall, 4),
        "matched_required_text": matched_required,
        "inferred_columns": inferred_columns,
        "expected_columns": expected_columns,
        "bounds_coverage": round(bounds_coverage, 4),
        "block_coverage": round(block_coverage, 4),
        "distinct_font_size_count": len(distinct_font_sizes),
        "style_record_count": int(evidence_json.get("style_record_count") or 0),
        "role_coverage": round(role_coverage, 4),
        "semantic_heading_recall": round(semantic_heading_recall, 4),
        "table_count": int(evidence_json.get("table_count") or 0),
        "figure_count": int(evidence_json.get("figure_count") or 0),
        "graphic_count": int(evidence_json.get("graphic_count") or 0),
    }
    thresholds = case.thresholds
    gates = [
        _gate("text_recall", text_recall, thresholds.get("required_text_recall_min", 1.0)),
        _gate("page_count", page_accuracy, thresholds.get("page_count_accuracy", 1.0)),
        _gate("column_classification", column_accuracy, thresholds.get("column_accuracy", 1.0)),
    ]
    return metrics, gates


def evaluate_rendering(
    pdf_path: Path,
    case: CorpusCase,
    *,
    expected_terms: set[str],
    privacy_exclusions: list[str],
    visual_similarity: float | None = None,
) -> tuple[dict[str, Any], list[GateDecision]]:
    """Score one rendered PDF.

    Deterministic gates: content recall against the approved render context and
    structural validity (readable PDF, non-empty text). Privacy gate: target
    candidate facts must not leak. Visual similarity is recorded as a
    comparative metric with a warning; it can never approve content or privacy.
    """
    reader = PdfReader(pdf_path)
    extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    normalized_text = _normalized(extracted_text)
    matched_terms = sorted(
        term for term in expected_terms if _normalized(term) in normalized_text
    )
    content_recall = len(matched_terms) / max(len(expected_terms), 1)
    leaked = [
        exclusion
        for exclusion in privacy_exclusions
        if _normalized(exclusion) and _normalized(exclusion) in normalized_text
    ]
    page_count = len(reader.pages)

    metrics: dict[str, Any] = {
        "page_count": page_count,
        "file_size_bytes": pdf_path.stat().st_size,
        "content_term_count": len(expected_terms),
        "matched_content_term_count": len(matched_terms),
        "content_recall": round(content_recall, 4),
        "privacy_leaks": leaked,
        "extracted_text_empty": not extracted_text.strip(),
    }
    if visual_similarity is not None:
        metrics["visual_similarity_to_baseline"] = round(visual_similarity, 4)

    thresholds = case.thresholds
    gates = [
        _gate("content_preservation", content_recall, thresholds.get("content_recall_min", 0.8)),
        _gate(
            "structural_validity",
            1.0 if page_count >= int(thresholds.get("page_count_min", 1)) and extracted_text.strip() else 0.0,
            1.0,
        ),
        _gate("privacy", 1.0 if not leaked else 0.0, 1.0),
    ]
    if visual_similarity is not None:
        gates.append(
            GateDecision(
                gate="visual_similarity",
                outcome="not_evaluated",
                kind="comparative",
                actual=f"{visual_similarity:.4f}",
                note=(
                    "Comparative metric only; visual similarity never approves "
                    "content or privacy correctness."
                ),
            )
        )
    return metrics, gates


def evaluate_design(
    normalized: dict[str, Any],
    case: CorpusCase,
) -> tuple[dict[str, Any], list[GateDecision]]:
    """Score one design proposal. Gates are independent: mapping accuracy,
    unsupported-content preservation, target-fact contamination, invalid
    reference rate, review escalation, and deterministic compiler acceptance.
    There is deliberately no blended total score."""
    expected_layout = case.expected_layout or {}
    expected_support = expected_layout.get("expected_support_state")
    expected_mappings = expected_layout.get("expected_mappings") or {}
    preserved = expected_layout.get("preserved_sections") or []
    expected_review = bool(expected_layout.get("review_escalation", False))

    support_state = normalized.get("support_state")
    support_accuracy = 1.0 if support_state == expected_support else 0.0

    proposal = normalized.get("proposal") or {}
    mappings = proposal.get("mappings") or []
    by_role: dict[str, dict[str, Any]] = {}
    for mapping in mappings:
        by_role.setdefault(mapping.get("source_role"), mapping)
    matched = 0
    for role, expected in expected_mappings.items():
        actual = by_role.get(role)
        if not actual:
            continue
        action_ok = actual.get("action") == expected.get("action")
        expected_label = expected.get("target_label")
        actual_label = actual.get("target_label") or ""
        label_ok = expected_label is None or _normalized(actual_label) == _normalized(
            expected_label
        )
        if action_ok and label_ok:
            matched += 1
    mapping_accuracy = (
        matched / max(len(expected_mappings), 1) if expected_mappings else 1.0
    )

    preserved_matched = sum(
        1
        for role in preserved
        if role in by_role
        and by_role[role].get("action")
        in {"map", "hide_empty", "preserve_as_additional", "flag_unmatched"}
    )
    unsupported_preservation = (
        preserved_matched / max(len(preserved), 1) if preserved else 1.0
    )

    contamination = normalized.get("target_fact_contamination") or []
    fact_contamination = 0.0 if contamination else 1.0

    invalid_count = len(normalized.get("invalid_references") or [])
    invalid_reference_rate = 1.0 - invalid_count / max(len(mappings), 1)

    review_flags = set(normalized.get("review_flags") or [])
    review_escalation = 1.0 if bool(review_flags) == expected_review else 0.0
    if expected_review and support_state != "ready_with_review":
        review_escalation = 0.0

    compiled = bool(normalized.get("layout_spec_compiled"))
    expected_compiled = expected_support in {"ready", "ready_with_review"}
    compiler_acceptance = 1.0 if compiled == expected_compiled else 0.0
    if support_state == "invalid_proposal":
        compiler_acceptance = 0.0

    metrics: dict[str, Any] = {
        "support_state": support_state,
        "expected_support_state": expected_support,
        "support_accuracy": round(support_accuracy, 4),
        "mapping_accuracy": round(mapping_accuracy, 4),
        "matched_mappings": matched,
        "expected_mapping_count": len(expected_mappings),
        "unsupported_preservation": round(unsupported_preservation, 4),
        "fact_contamination": round(fact_contamination, 4),
        "invalid_reference_count": invalid_count,
        "invalid_reference_rate": round(invalid_reference_rate, 4),
        "review_flags": sorted(review_flags),
        "review_escalation_accuracy": round(review_escalation, 4),
        "compiler_acceptance": round(compiler_acceptance, 4),
        "layout_class": proposal.get("layout_class"),
        "declared_unsupported": proposal.get("declared_unsupported", False),
        "unmatched_source_sections": normalized.get("unmatched_source_sections") or [],
    }
    thresholds = case.thresholds
    gates = [
        _gate("mapping_accuracy", mapping_accuracy, thresholds.get("mapping_accuracy_min", 1.0)),
        _gate(
            "unsupported_content_preservation",
            unsupported_preservation,
            thresholds.get("unsupported_preservation_min", 1.0),
        ),
        _gate("fact_contamination", fact_contamination, thresholds.get("fact_contamination_min", 1.0)),
        _gate(
            "invalid_reference_rate",
            invalid_reference_rate,
            thresholds.get("invalid_reference_rate_min", 1.0),
        ),
        _gate("review_escalation", review_escalation, thresholds.get("review_escalation_min", 1.0)),
        _gate(
            "compiler_acceptance",
            compiler_acceptance,
            thresholds.get("compiler_acceptance_min", 1.0),
        ),
    ]
    return metrics, gates


def _gate(name: str, actual: float, threshold: float, *, kind: str = "deterministic") -> GateDecision:
    passed = actual >= threshold
    return GateDecision(
        gate=name,
        outcome="pass" if passed else "fail",
        kind=kind,
        threshold=f"{threshold:.4f}",
        actual=f"{actual:.4f}",
    )


def _semantic_heading_recall(blocks: list[dict[str, Any]], headings: list[str]) -> float:
    if not headings:
        return 1.0
    found = 0
    for heading in headings:
        normalized_heading = _normalized(heading)
        for block in blocks:
            normalized_block = _normalized(block.get("text") or "")
            if not (
                normalized_block == normalized_heading
                or normalized_block.startswith(f"{normalized_heading} ")
            ):
                continue
            if _is_semantic_heading_role(block.get("role")):
                found += 1
            break
    return found / len(headings)


def _is_semantic_heading_role(role: Any) -> bool:
    if not role:
        return False
    normalized = str(role).lower().split("[", 1)[0]
    return normalized in {
        "title",
        "heading",
        "sectionheading",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }


def _looks_like_heading(text: str) -> bool:
    return text.isupper() or text in {
        "Professional Summary",
        "Core Qualifications",
        "Experience",
        "Education",
        "Summary",
        "Highlights",
        "Additional Information",
    }


def _infer_column_count(evidence_json: dict[str, Any]) -> int:
    blocks = [
        block
        for block in evidence_json.get("text_blocks") or []
        if block.get("bbox") and block.get("text", "").strip()
    ]
    starts = sorted(block["bbox"]["x0"] for block in blocks)
    if len(starts) < 6:
        return 1
    candidates: list[tuple[float, int]] = []
    for index in range(2, len(starts) - 2):
        gap = starts[index] - starts[index - 1]
        candidates.append((gap, index))
    gap, split_index = max(candidates, default=(0.0, 0))
    if gap < 0.12 or split_index < 3 or len(starts) - split_index < 3:
        return 1
    # A genuine second column carries substantial content. A thin cluster of
    # right-aligned dates inside a one-column layout must not be classified as
    # a second column.
    smaller_side = min(split_index, len(starts) - split_index)
    if smaller_side / len(starts) < 0.3:
        return 1
    return 2


def render_context_terms(value: Any) -> set[str]:
    terms: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            terms.update(render_context_terms(item))
    elif isinstance(value, list):
        for item in value:
            terms.update(render_context_terms(item))
    elif isinstance(value, str) and value.strip():
        terms.add(value.strip())
    return terms


def _flatten_scalars(value: Any, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if isinstance(value, dict):
        if not value:
            flattened[prefix] = value
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten_scalars(item, child_prefix))
    elif isinstance(value, list):
        if not value:
            flattened[prefix] = value
        for index, item in enumerate(value):
            flattened.update(_flatten_scalars(item, f"{prefix}.{index}"))
    else:
        flattened[prefix] = value
    return flattened


def _normalized(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().rstrip("/")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()
