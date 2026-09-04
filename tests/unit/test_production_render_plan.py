from app.extraction.candidate_schema import CandidateProfile
from app.generation.render_plan import build_production_render_plan
from app.generation.template_mapper import build_client_render_context
from app.profile_approval import profile_sha256
from app.template_analysis.schemas import (
    built_in_template_style_spec,
    migrate_template_style_spec,
)
from tests.helpers.llm_first import llm_first_document


def test_production_plan_excludes_unapproved_optional_source_content() -> None:
    document = llm_first_document(
        """Jane Candidate
SUMMARY
Backend engineer.
PROJECTS
Secret unapproved project text.
"""
    )
    profile = CandidateProfile(
        full_name="Jane Candidate",
        professional_summary="Backend engineer.",
    )
    context = build_client_render_context(profile)
    plan = build_production_render_plan(
        approved_profile_version_id="profile_v1",
        profile_sha256=profile_sha256(profile),
        document=document,
        context=context,
        layout_spec=migrate_template_style_spec(built_in_template_style_spec()),
    )

    assert "Secret unapproved project text." not in plan.model_dump_json()
    assert "candidate-block-003" in plan.review_required_block_ids
    additional = next(
        item
        for item in plan.section_bindings
        if item.candidate_source == "additional_details"
    )
    assert additional.status == "review_required"
    assert plan.context.additional_details == []


def test_production_plan_surfaces_legacy_contract_warning() -> None:
    document = llm_first_document(
        "Jane Candidate\nSUMMARY\nBackend engineer.\n"
    )
    profile = CandidateProfile(
        full_name="Jane Candidate",
        professional_summary="Backend engineer.",
    )
    legacy_spec = migrate_template_style_spec(built_in_template_style_spec())
    assert legacy_spec.structure_contract == "legacy"
    assert any(
        "LEGACY COMPATIBILITY" in warning for warning in legacy_spec.warnings
    )
    plan = build_production_render_plan(
        approved_profile_version_id="profile_v1",
        profile_sha256=profile_sha256(profile),
        document=document,
        context=build_client_render_context(profile),
        layout_spec=legacy_spec,
    )
    assert any("LEGACY ARTIFACT" in warning for warning in plan.warnings)