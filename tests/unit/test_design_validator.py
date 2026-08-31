"""Deterministic design validation: rejection of unsafe proposals."""

from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path

import pytest

from app.extraction.candidate_schema import CandidateProfile
from app.template_analysis.design_evidence import (
    build_design_request,
    build_target_layout_evidence,
    candidate_sections_from_profile,
    target_checksum_from_bytes,
)
from app.template_analysis.design_schemas import (
    CandidateSectionRole,
    DesignProposal,
    DesignRequest,
    SectionMapping,
)
from app.template_analysis.design_validator import validate_design_proposal
from tests.helpers.adobe_evidence import build_synthetic_target_analysis
from tests.helpers.synthetic_pdf import build_styled_target_pdf


@pytest.fixture(scope="module")
def evidence():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pdf = build_styled_target_pdf(tmp_path / "styled.pdf")
        analysis = build_synthetic_target_analysis(pdf, tmp_path)
        return build_target_layout_evidence(
            analysis,
            target_checksum=target_checksum_from_bytes(pdf.read_bytes()),
            target_format="pdf",
        )


@pytest.fixture(scope="module")
def style_spec():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pdf = build_styled_target_pdf(tmp_path / "styled.pdf")
        analysis = build_synthetic_target_analysis(pdf, tmp_path)
        return analysis.style_spec


def _request(evidence, profile: CandidateProfile | None = None) -> DesignRequest:
    profile = profile or CandidateProfile(
        full_name="Jane",
        email="jane@example.com",
        professional_summary="Engineer.",
        skills=["Python"],
        languages=[{"name": "English"}],
        work_experience=[{"company": "X", "title": "E"}],
        education=[{"institution": "U"}],
    )
    return build_design_request(evidence, candidate_sections_from_profile(profile))


def _proposal_with_mappings(request: DesignRequest, mappings: list[SectionMapping]) -> DesignProposal:
    return DesignProposal(
        proposal_id="p_test",
        layout_class=request.layout_class_hint,
        section_order=[mapping.mapping_id for mapping in mappings],
        mappings=mappings,
        style_role_refs=["body", "title", "heading"],
        overflow_policy="continue_next_page",
        confidence=0.95,
    )


def _valid_mappings(request: DesignRequest) -> list[SectionMapping]:
    candidates = [
        region for region in request.measured_regions
        if region.semantic_role == "heading" and region.typography_class
    ]
    dominant = Counter(region.typography_class for region in candidates).most_common(1)
    headings = [
        region for region in candidates
        if dominant and region.typography_class == dominant[0][0]
    ]
    mappings: list[SectionMapping] = []
    for index, section in enumerate(request.available_candidate_sections):
        region = headings[index] if index < len(headings) else None
        mappings.append(SectionMapping(
            mapping_id=f"m_{index}",
            source_role=section.role,
            target_region_id=region.region_id if region else None,
            target_semantic_role="full_width",
            target_label=section.section_label,
            action="map" if region else "preserve_as_additional",
            confidence=0.95,
        ))
    return mappings


def _copy_mapping(mapping: SectionMapping, **updates: object) -> SectionMapping:
    return mapping.model_copy(update=updates)


def test_valid_proposal_accepts(evidence) -> None:
    request = _request(evidence)
    result = validate_design_proposal(request, _proposal_with_mappings(request, _valid_mappings(request)), evidence)
    assert result.ok, result.errors
    assert result.state in {"ready", "ready_with_review"}


def test_invalid_region_reference_rejected(evidence) -> None:
    request = _request(evidence)
    mappings = _valid_mappings(request)
    mappings[0] = _copy_mapping(mappings[0], target_region_id="page.99.block.999")
    result = validate_design_proposal(request, _proposal_with_mappings(request, mappings), evidence)
    assert not result.ok
    assert result.state == "invalid_proposal"
    assert any("page.99.block.999" in error for error in result.errors)


def test_unknown_style_role_rejected(evidence) -> None:
    request = _request(evidence)
    proposal = _proposal_with_mappings(request, _valid_mappings(request))
    proposal = proposal.model_copy(update={"style_role_refs": ["body", "magenta_glow"]})
    result = validate_design_proposal(request, proposal, evidence)
    assert not result.ok
    assert any("magenta_glow" in error for error in result.errors)


def test_invented_geometry_rejected(evidence) -> None:
    request = _request(evidence)
    proposal = _proposal_with_mappings(request, _valid_mappings(request))
    # Inject coordinate-like text into a free field (the strict model already
    # rejects coordinate *fields*; the validator additionally scans values).
    proposal = proposal.model_copy(
        update={"warnings": ["left margin should be 24pt at x0=40"]}
    )
    result = validate_design_proposal(request, proposal, evidence)
    assert not result.ok
    assert any("geometry" in error for error in result.errors)


def test_target_candidate_facts_rejected(evidence) -> None:
    request = _request(evidence)
    proposal = _proposal_with_mappings(request, _valid_mappings(request))
    contaminated = proposal.model_copy(
        update={"warnings": ["mapped Alex Example into the heading"]}
    )
    result = validate_design_proposal(request, contaminated, evidence)
    assert not result.ok
    assert result.target_fact_contamination


def test_unmatched_source_sections_flagged(evidence) -> None:
    request = _request(evidence)
    mappings = _valid_mappings(request)
    # Drop every mapping; every available candidate section is then unmatched.
    proposal = DesignProposal(
        proposal_id="p_test",
        layout_class=request.layout_class_hint,
        section_order=[],
        mappings=[],
        style_role_refs=["body"],
        overflow_policy="continue_next_page",
        confidence=0.5,
    )
    result = validate_design_proposal(request, proposal, evidence)
    assert not result.ok
    assert result.unmatched_source_sections


def test_low_confidence_mapping_requires_review(evidence) -> None:
    request = _request(evidence)
    mappings = _valid_mappings(request)
    mappings[0] = _copy_mapping(mappings[0], confidence=0.5, needs_review=False)
    result = validate_design_proposal(request, _proposal_with_mappings(request, mappings), evidence)
    assert not result.ok
    assert result.low_confidence_without_review
    # Same mapping with review requested is fine.
    mappings[0] = _copy_mapping(mappings[0], confidence=0.5, needs_review=True, action="needs_review")
    result = validate_design_proposal(request, _proposal_with_mappings(request, mappings), evidence)
    assert result.ok
    assert result.state == "ready_with_review"


def test_nonempty_source_cannot_be_hidden_as_empty(evidence) -> None:
    request = _request(evidence)
    mappings = _valid_mappings(request)
    languages_region = next(region.region_id for region in request.measured_regions)
    mappings.append(
        SectionMapping(
            mapping_id="m_hide_languages",
            source_role="languages",
            target_region_id=languages_region,
            target_label="LANGUAGES",
            action="hide_empty",
            confidence=0.9,
        )
    )
    result = validate_design_proposal(request, _proposal_with_mappings(request, mappings), evidence)
    assert not result.ok
    assert any("hides a non-empty" in error for error in result.errors)


def test_layout_class_outside_supported_vocabulary_rejected(evidence) -> None:
    request = _request(evidence)
    proposal = _proposal_with_mappings(request, _valid_mappings(request))
    proposal = proposal.model_copy(update={"layout_class": "brochure"})
    result = validate_design_proposal(request, proposal, evidence)
    assert not result.ok
    assert any("layout_class" in error for error in result.errors)


def test_overflow_policy_outside_vocabulary_rejected(evidence) -> None:
    request = _request(evidence)
    proposal = _proposal_with_mappings(request, _valid_mappings(request))
    proposal = proposal.model_copy(update={"overflow_policy": "overlap_everything"})
    result = validate_design_proposal(request, proposal, evidence)
    assert not result.ok
    assert any("overflow" in error for error in result.errors)


def test_trim_to_page_overflow_policy_rejected(evidence) -> None:
    """A policy that silently discards approved content is rejected, even
    when the request vocabulary is explicitly widened to include it."""
    request = _request(evidence)
    proposal = _proposal_with_mappings(request, _valid_mappings(request))
    proposal = proposal.model_copy(update={"overflow_policy": "trim_to_page"})
    result = validate_design_proposal(request, proposal, evidence)
    assert not result.ok
    assert any("overflow" in error for error in result.errors)
    assert result.state == "invalid_proposal"


def test_safe_overflow_policies_accepted(evidence) -> None:
    """Controlled continuation/reflow and explicit review remain accepted;
    none of them silently drops approved candidate content."""
    for policy in ("shrink_to_fit", "continue_next_page", "require_review"):
        request = _request(evidence)
        proposal = _proposal_with_mappings(request, _valid_mappings(request))
        proposal = proposal.model_copy(update={"overflow_policy": policy})
        result = validate_design_proposal(request, proposal, evidence)
        assert result.ok, f"policy {policy} should be accepted: {result.errors}"


def test_unsupported_layout_produces_explicit_state(evidence) -> None:
    request = _request(evidence)
    proposal = _proposal_with_mappings(request, _valid_mappings(request))
    proposal = proposal.model_copy(
        update={"declared_unsupported": True, "unsupported_features": ["brochure panels"]}
    )
    unsupported_evidence = evidence.model_copy(
        update={"warnings": [*evidence.warnings, "large image regions are not reproduced"]}
    )
    result = validate_design_proposal(request, proposal, unsupported_evidence)
    assert result.ok
    assert result.state == "unsupported"


def test_declared_unsupported_without_evidence_rejected(evidence) -> None:
    request = _request(evidence)
    proposal = _proposal_with_mappings(request, _valid_mappings(request))
    proposal = proposal.model_copy(
        update={
            "declared_unsupported": True,
            "unsupported_features": [],
            "warnings": [],
        }
    )
    result = validate_design_proposal(request, proposal, evidence)
    assert not result.ok
    assert any("declares the target unsupported" in error for error in result.errors)


def test_candidate_content_cannot_flow_from_target_evidence(evidence, style_spec) -> None:
    """The compiled spec must never contain target body text."""
    from app.template_analysis.design_compiler import compile_layout_template_spec
    from app.template_analysis.design_evidence import target_body_texts

    request = _request(evidence)
    proposal = _proposal_with_mappings(request, _valid_mappings(request))
    result = validate_design_proposal(request, proposal, evidence)
    assert result.ok
    spec = compile_layout_template_spec(request, proposal, evidence, style_spec)
    body_texts = {text.lower() for text in target_body_texts(evidence) if len(text) >= 4}
    spec_text = " ".join(section.label for section in spec.sections).lower()
    for body in body_texts:
        assert body not in spec_text, f"target body text leaked: {body}"


def test_external_request_excludes_target_body_text(evidence) -> None:
    request = _request(evidence)
    labels = [region.label for region in request.measured_regions if region.label]
    assert "Alex Example" not in labels
    assert "Platform Engineer" not in labels
    assert "Built resilient APIs" not in labels
