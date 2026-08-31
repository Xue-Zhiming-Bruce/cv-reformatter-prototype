"""Deterministic compilation: reproducibility and content safety."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.extraction.candidate_schema import CandidateProfile
from app.template_analysis.design_compiler import (
    DesignCompilerError,
    UnsupportedPlacementError,
    compile_layout_template_spec,
    compiler_version,
    proposal_checksum,
)
from app.template_analysis.design_evidence import (
    build_design_request,
    build_target_layout_evidence,
    candidate_sections_from_profile,
    target_checksum_from_bytes,
)
from app.template_analysis.design_schemas import (
    CandidateSectionRole,
    DesignProposal,
    SectionMapping,
)
from app.template_analysis.design_validator import validate_design_proposal
from tests.helpers.adobe_evidence import build_synthetic_target_analysis
from tests.helpers.synthetic_pdf import build_styled_target_pdf


def _pipeline():
    import tempfile as _tmp

    with _tmp.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pdf = build_styled_target_pdf(tmp_path / "styled.pdf")
        analysis = build_synthetic_target_analysis(pdf, tmp_path)
        evidence = build_target_layout_evidence(
            analysis,
            target_checksum=target_checksum_from_bytes(pdf.read_bytes()),
            target_format="pdf",
        )
        profile = CandidateProfile(
            full_name="Jane",
            email="jane@example.com",
            professional_summary="Engineer.",
            skills=["Python"],
            languages=[{"name": "English"}],
            work_experience=[{"company": "X", "title": "E"}],
            education=[{"institution": "U"}],
        )
        request = build_design_request(evidence, candidate_sections_from_profile(profile))
        return analysis, evidence, request


def test_compile_is_deterministic() -> None:
    analysis, evidence, request = _pipeline()
    from app.template_analysis.designer import MockTemplateDesigner

    proposal = MockTemplateDesigner().design(request)
    validation = validate_design_proposal(request, proposal, evidence)
    assert validation.ok, validation.errors
    spec_a = compile_layout_template_spec(request, proposal, evidence, analysis.style_spec)
    spec_b = compile_layout_template_spec(request, proposal, evidence, analysis.style_spec)
    assert spec_a.model_dump(mode="json") == spec_b.model_dump(mode="json")
    assert spec_a.template_version == spec_b.template_version


def test_badge_contract_bumps_compiler_cache_version() -> None:
    assert compiler_version() == "7"


def test_proposal_checksum_is_stable() -> None:
    analysis, evidence, request = _pipeline()
    from app.template_analysis.designer import MockTemplateDesigner

    proposal = MockTemplateDesigner().design(request)
    assert proposal_checksum(proposal) == proposal_checksum(proposal.model_copy())
    assert len(proposal_checksum(proposal)) == 64


def test_sections_follow_proposal_order_and_labels() -> None:
    analysis, evidence, request = _pipeline()
    from app.template_analysis.designer import MockTemplateDesigner

    proposal = MockTemplateDesigner().design(request)
    validation = validate_design_proposal(request, proposal, evidence)
    assert validation.ok, validation.errors
    # Reorder: skills and contact first; the compiler must follow exactly.
    by_id = {mapping.mapping_id: mapping for mapping in proposal.mappings}
    ordered_ids = [
        next(m.mapping_id for m in proposal.mappings if m.source_role == "skills"),
        next(m.mapping_id for m in proposal.mappings if m.source_role == "contact"),
    ] + [
        m.mapping_id
        for m in proposal.mappings
        if m.source_role not in {"skills", "contact"}
    ]
    reordered = proposal.model_copy(update={"section_order": ordered_ids})
    spec = compile_layout_template_spec(request, reordered, evidence, analysis.style_spec)
    sources = [section.source for section in spec.sections]
    assert sources[0] == "skills"
    assert sources[1] == "contact"
    assert spec.sections[0].label == "Skills"


def test_two_column_layout_compiles_with_two_columns() -> None:
    analysis, evidence, request = _pipeline()
    from app.template_analysis.designer import MockTemplateDesigner

    proposal = MockTemplateDesigner().design(request).model_copy(
        update={"layout_class": "sidebar"}
    )
    spec = compile_layout_template_spec(request, proposal, evidence, analysis.style_spec)
    assert spec.columns.count == 2


def test_declared_unsupported_never_compiles() -> None:
    analysis, evidence, request = _pipeline()
    proposal = DesignProposal(
        proposal_id="p_unsupported",
        layout_class="one_column",
        declared_unsupported=True,
        section_order=[],
        mappings=[],
        style_role_refs=[],
        overflow_policy=None,
        unsupported_features=["brochure"],
        confidence=0.3,
    )
    with pytest.raises(DesignCompilerError):
        compile_layout_template_spec(request, proposal, evidence, analysis.style_spec)


def test_compiled_spec_has_no_renderer_properties() -> None:
    analysis, evidence, request = _pipeline()
    from app.template_analysis.designer import MockTemplateDesigner

    proposal = MockTemplateDesigner().design(request)
    spec = compile_layout_template_spec(request, proposal, evidence, analysis.style_spec)
    payload = json.dumps(spec.model_dump(mode="json"))
    assert "python-docx" not in payload
    assert "wml" not in payload.lower()
    assert "render_plan" not in payload


def _with_placement(proposal, source_role: str, placement: str):
    return proposal.model_copy(
        update={
            "mappings": [
                (
                    mapping.model_copy(update={"target_semantic_role": placement})
                    if mapping.source_role == source_role
                    else mapping
                )
                for mapping in proposal.mappings
            ]
        }
    )


def test_main_column_to_sidebar_changes_compiled_spec() -> None:
    """A validated placement decision must change the compiled
    provider-neutral template contract."""
    analysis, evidence, request = _pipeline()
    from app.template_analysis.designer import MockTemplateDesigner

    proposal = MockTemplateDesigner().design(request)
    spec_main = compile_layout_template_spec(
        request,
        _with_placement(proposal, "summary", "main_column"),
        evidence,
        analysis.style_spec,
    )
    spec_sidebar = compile_layout_template_spec(
        request,
        _with_placement(proposal, "summary", "sidebar"),
        evidence,
        analysis.style_spec,
    )
    summary_main = next(section for section in spec_main.sections if section.source == "summary")
    summary_sidebar = next(
        section for section in spec_sidebar.sections if section.source == "summary"
    )
    assert summary_main.placement == "main_column"
    assert summary_sidebar.placement == "sidebar"
    assert summary_main.placement != summary_sidebar.placement
    assert spec_main.model_dump(mode="json") != spec_sidebar.model_dump(mode="json")


def test_compiled_sections_always_carry_measured_style() -> None:
    """Every compiled section carries measured typography without defaults."""
    analysis, evidence, request = _pipeline()
    from app.template_analysis.designer import MockTemplateDesigner

    proposal = MockTemplateDesigner().design(request)
    spec = compile_layout_template_spec(request, proposal, evidence, analysis.style_spec)
    for section in spec.sections:
        assert section.heading_style is not None
        assert section.heading_style.font_family
        assert section.heading_style.font_size_pt > 0
        assert section.heading_style.color_hex


def test_unsupported_placement_never_silently_compiles() -> None:
    """An unrepresentable placement raises an explicit error; it is never
    silently replaced with a default placement."""
    analysis, evidence, request = _pipeline()
    from app.template_analysis.designer import MockTemplateDesigner

    proposal = MockTemplateDesigner().design(request)
    bad = _with_placement(proposal, "summary", "none")
    with pytest.raises(UnsupportedPlacementError):
        compile_layout_template_spec(request, bad, evidence, analysis.style_spec)


def test_compiled_spec_placement_has_no_coordinates() -> None:
    """Compiled placement is semantic only; exact geometry still comes from
    measured evidence, never from the proposal. (Measured page dimensions may
    legitimately appear in the spec; section placement never carries them.)"""
    analysis, evidence, request = _pipeline()
    from app.template_analysis.designer import MockTemplateDesigner

    proposal = MockTemplateDesigner().design(request)
    spec = compile_layout_template_spec(
        request,
        _with_placement(proposal, "summary", "sidebar"),
        evidence,
        analysis.style_spec,
    )
    placements = {section.source: section.placement for section in spec.sections}
    assert placements["summary"] == "sidebar"
    for section in spec.sections:
        assert section.placement in {"full_width", "main_column", "sidebar", "header", "footer"}
    sections_payload = json.dumps(
        [section.model_dump(mode="json") for section in spec.sections]
    )
    for geometry_marker in ("x0", "top", "bbox", "left_pt", "width_pt"):
        assert geometry_marker not in sections_payload


def test_measured_additional_sections_carry_entry_tiers() -> None:
    """Entry-type additional zones reuse the measured work-entry tiers."""
    from app.template_analysis.design_compiler import (
        compile_measured_layout_template_spec,
    )
    from app.template_analysis.design_schemas import (
        MeasuredHeaderStructure,
        MeasuredSectionStructure,
        RegionReference,
        StyleRoleReference,
        TargetLayoutEvidence,
    )
    from app.template_analysis.schemas import ColumnStyle
    from app.template_analysis.schemas import built_in_template_style_spec

    heading_role = StyleRoleReference(
        role_id="role.heading", label="Heading", font_family="Helvetica",
        font_size_pt=12.0, bold=True, color_hex="#1F4E79",
    )
    entry_role = StyleRoleReference(
        role_id="role.entry", label="Entry title", font_family="Helvetica",
        font_size_pt=11.5, bold=True, color_hex="#0F172A",
    )
    metadata_role = StyleRoleReference(
        role_id="role.metadata", label="Metadata", font_family="Helvetica",
        font_size_pt=9.5, bold=False, color_hex="#64748B",
    )
    evidence = TargetLayoutEvidence(
        evidence_version="layout/evidence/1",
        target_checksum="a" * 64,
        target_format="pdf",
        page_count=1,
        text_block_count=1,
        style_roles=[heading_role, entry_role, metadata_role],
        columns=ColumnStyle(count=1),
        regions=[
            RegionReference(
                region_id="r1", page_number=1, label="PROJECTS",
                style_role_ref="role.heading",
            )
        ],
        header_structure=MeasuredHeaderStructure(contact_style_role_ref="role.metadata"),
        section_structure=[
            MeasuredSectionStructure(
                region_id="r1", label="PROJECTS", source_hint="additional_section"
            )
        ],
        entry_title_style_role_ref="role.entry",
    )

    spec = compile_measured_layout_template_spec(
        evidence, built_in_template_style_spec()
    )
    projects = next(
        section for section in spec.sections if section.source == "additional_details"
    )
    assert projects.entry_title_style is not None
    assert projects.entry_title_style.font_size_pt == 11.5
    assert projects.entry_title_style.bold is True
    assert projects.entry_metadata_style is not None
    assert projects.entry_metadata_style.font_size_pt == 9.5
