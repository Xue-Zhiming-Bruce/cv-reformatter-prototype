"""Design evaluation lane helpers.

Builds a ``DesignRequest`` from a materialized synthetic target PDF plus the
case's candidate section inventory, runs a designer, validates deterministically,
and compiles the layout spec when supported. Used by both the offline
``mock_designer`` adapter and the live-gated ``claude_designer`` adapter.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.template_analysis.design_compiler import (
    UnsupportedPlacementError,
    compile_layout_template_spec,
)
from app.template_analysis.design_evidence import (
    build_design_request,
    build_target_layout_evidence,
    target_checksum_from_bytes,
)
from app.template_analysis.design_schemas import CandidateSectionRole
from app.template_analysis.design_validator import validate_design_proposal
from app.template_analysis.designer import TemplateDesigner
from tests.helpers.adobe_evidence import build_synthetic_target_analysis
from tests.commercial_api.corpus import sha256_file


def candidate_sections_from_case(case: Any) -> list[CandidateSectionRole]:
    """Candidate section inventory declared by a design corpus case."""
    raw_sections = (case.expected_layout or {}).get("candidate_sections") or []
    return [CandidateSectionRole.model_validate(item) for item in raw_sections]


def run_design_pipeline(
    case_input: Path,
    artifact_dir: Path,
    case: Any,
    designer: TemplateDesigner,
) -> dict[str, Any]:
    """Run the full design pipeline for one case and return normalized results."""
    started = time.perf_counter()
    analysis = build_synthetic_target_analysis(case_input, artifact_dir / "analysis")
    evidence = build_target_layout_evidence(
        analysis,
        target_checksum=sha256_file(case_input),
        target_format="pdf",
    )
    sections = candidate_sections_from_case(case)
    request = build_design_request(evidence, sections)
    proposal = designer.design(request)
    validation = validate_design_proposal(request, proposal, evidence)
    compiled = None
    if validation.ok and validation.state != "unsupported":
        try:
            compiled = compile_layout_template_spec(
                request, proposal, evidence, analysis.style_spec
            )
        except UnsupportedPlacementError as exc:
            # An unrepresentable placement is an explicit unsupported state;
            # it is never silently replaced with a default.
            validation = validation.model_copy(
                update={
                    "ok": False,
                    "state": "unsupported",
                    "errors": [*validation.errors, f"compilation failed: {exc}"],
                }
            )
    review_flags = [
        mapping.mapping_id for mapping in proposal.mappings if mapping.needs_review
    ]
    if proposal.human_review_required:
        review_flags.append("human_review_required")
    return {
        "support_state": validation.state,
        "validation_ok": validation.ok,
        "layout_class": proposal.layout_class,
        "declared_unsupported": proposal.declared_unsupported,
        "proposal": proposal.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json"),
        "invalid_references": list(validation.invalid_references),
        "target_fact_contamination": list(validation.target_fact_contamination),
        "unmatched_source_sections": list(validation.unmatched_source_sections),
        "fabricated_slots": list(validation.fabricated_slots),
        "review_flags": review_flags,
        "layout_spec_compiled": compiled is not None,
        "layout_spec": compiled.model_dump(mode="json") if compiled is not None else None,
        "latency_seconds": round(time.perf_counter() - started, 4),
        "usage": {
            "input_tokens": getattr(designer, "last_usage", {}).get("input_tokens"),
            "output_tokens": getattr(designer, "last_usage", {}).get("output_tokens"),
        },
    }
