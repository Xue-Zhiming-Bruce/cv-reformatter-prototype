"""Strict validation of the provider-neutral design models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.template_analysis.design_schemas import (
    DESIGN_PROPOSAL_SCHEMA_VERSION,
    DESIGN_REQUEST_SCHEMA_VERSION,
    SUPPORTED_OVERFLOW_POLICIES,
    CandidateSectionRole,
    DesignProposal,
    DesignRequest,
    MappingPlan,
    RegionReference,
    SectionMapping,
    StyleRoleReference,
)


def _valid_request() -> DesignRequest:
    return DesignRequest(
        target_evidence_version="1.0",
        target_checksum="abc123",
        target_format="pdf",
        layout_class_hint="one_column",
        candidate_field_vocabulary=["contact", "summary", "skills"],
        available_candidate_sections=[
            CandidateSectionRole(role="contact", section_label="Contact", item_count=1),
        ],
        measured_regions=[
            RegionReference(
                region_id="page.1.block.1",
                page_number=1,
                semantic_role="heading",
                label="CONTACT",
            )
        ],
        measured_style_roles=[StyleRoleReference(role_id="heading", label="Heading")],
    )


def _valid_proposal() -> DesignProposal:
    return DesignProposal(
        proposal_id="p1",
        layout_class="one_column",
        section_order=["m_contact"],
        mappings=[
            SectionMapping(
                mapping_id="m_contact",
                source_role="contact",
                target_region_id="page.1.block.1",
                target_label="CONTACT",
                action="map",
                confidence=0.95,
            )
        ],
        style_role_refs=["heading"],
        overflow_policy="continue_next_page",
        confidence=0.95,
    )


def test_design_request_is_strict() -> None:
    request = _valid_request()
    assert request.schema_version == DESIGN_REQUEST_SCHEMA_VERSION
    with pytest.raises(ValidationError):
        DesignRequest.model_validate(
            {**request.model_dump(), "extra_field": "rejected"}
        )


def test_design_request_rejects_invalid_layout_hint() -> None:
    payload = _valid_request().model_dump()
    payload["layout_class_hint"] = "brochure"
    with pytest.raises(ValidationError):
        DesignRequest.model_validate(payload)


def test_design_request_rejects_invalid_target_format() -> None:
    payload = _valid_request().model_dump()
    payload["target_format"] = "ppt"
    with pytest.raises(ValidationError):
        DesignRequest.model_validate(payload)


def test_design_proposal_is_strict_and_versioned() -> None:
    proposal = _valid_proposal()
    assert proposal.schema_version == DESIGN_PROPOSAL_SCHEMA_VERSION
    with pytest.raises(ValidationError):
        DesignProposal.model_validate(
            {**proposal.model_dump(), "x0": 12.5}
        )


def test_design_proposal_rejects_unknown_fields_even_nested() -> None:
    payload = _valid_proposal().model_dump()
    payload["mappings"][0]["invented_geometry"] = "left: 10pt"
    with pytest.raises(ValidationError):
        DesignProposal.model_validate(payload)


def test_section_mapping_rejects_invalid_action() -> None:
    payload = _valid_proposal().model_dump()
    payload["mappings"][0]["action"] = "copy_target_text"
    with pytest.raises(ValidationError):
        DesignProposal.model_validate(payload)


def test_section_mapping_rejects_invalid_confidence() -> None:
    payload = _valid_proposal().model_dump()
    payload["mappings"][0]["confidence"] = 1.5
    with pytest.raises(ValidationError):
        DesignProposal.model_validate(payload)


def test_design_proposal_layout_class_is_vocabulary_constrained() -> None:
    """The model accepts strings; supported-class enforcement happens against
    the request vocabulary in the deterministic validator."""
    proposal = _valid_proposal()
    # A value outside the supported classes still models, but the validator
    # rejects it (covered in test_design_validator).
    DesignProposal.model_validate({**proposal.model_dump(), "layout_class": "brochure"})


def test_design_proposal_overflow_policy_is_vocabulary_constrained() -> None:
    proposal = _valid_proposal()
    DesignProposal.model_validate(
        {**proposal.model_dump(), "overflow_policy": "cram_everything"}
    )


def test_mapping_plan_is_strict() -> None:
    proposal = _valid_proposal()
    plan = MappingPlan(
        proposal_id=proposal.proposal_id,
        layout_class=proposal.layout_class,
        mappings=proposal.mappings,
        overflow_policy=proposal.overflow_policy,
    )
    assert plan.schema_version == "design/mapping-plan/1"
    with pytest.raises(ValidationError):
        MappingPlan.model_validate(
            {**plan.model_dump(), "renderer_specific": "python-docx"}
        )


def test_candidate_section_role_rejects_negative_count() -> None:
    with pytest.raises(ValidationError):
        CandidateSectionRole(role="skills", section_label="Skills", item_count=-1)


def test_region_reference_rejects_missing_page_number() -> None:
    with pytest.raises(ValidationError):
        RegionReference(region_id="page.1.block.1", semantic_role="heading", label="X")


def test_supported_overflow_policies_cannot_silently_trim_content() -> None:
    """Approved content must never be silently discarded: a trim-to-page
    policy is not part of the supported vocabulary nor of a default request."""
    assert "trim_to_page" not in SUPPORTED_OVERFLOW_POLICIES
    assert set(SUPPORTED_OVERFLOW_POLICIES) == {
        "shrink_to_fit",
        "continue_next_page",
        "require_review",
    }
    request = _valid_request()
    assert "trim_to_page" not in request.supported_overflow_policies
