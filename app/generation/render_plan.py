from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.extraction.candidate_document_analyzer import (
    CandidateBlockBindingPlan,
    CandidateBlockSource,
    NormalizedCandidateDocument,
    build_candidate_block_binding_plan,
)
from app.generation.template_mapper import ClientFacingRenderContext
from app.template_analysis.schemas import LayoutTemplateSpec


class RenderPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProductionSectionBinding(RenderPlanModel):
    section_id: str
    candidate_source: CandidateBlockSource
    status: Literal["filled", "hidden_empty", "review_required"]
    approved_content_count: int = Field(ge=0)
    source_block_ids: list[str] = Field(default_factory=list)
    reason: str


class ProductionRenderPlan(RenderPlanModel):
    """Inspectable production bridge between approved content and layout.

    Candidate source blocks contribute provenance and review suggestions only.
    The embedded client-facing context is derived exclusively from the current
    immutable approved profile version.
    """

    schema_version: Literal["production-render-plan/1.0"] = (
        "production-render-plan/1.0"
    )
    approved_profile_version_id: str
    profile_sha256: str
    source_text_sha256: str
    layout_schema_version: str
    layout_template_version: str
    layout_spec_sha256: str
    context: ClientFacingRenderContext
    section_bindings: list[ProductionSectionBinding] = Field(default_factory=list)
    review_required_block_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def build_production_render_plan(
    *,
    approved_profile_version_id: str,
    profile_sha256: str,
    document: NormalizedCandidateDocument,
    context: ClientFacingRenderContext,
    layout_spec: LayoutTemplateSpec,
    warnings: list[str] | None = None,
) -> ProductionRenderPlan:
    evidence_plan = build_candidate_block_binding_plan(document, layout_spec)
    evidence_by_section = {
        binding.section_id: binding for binding in evidence_plan.bindings
    }
    bindings: list[ProductionSectionBinding] = []
    for section in layout_spec.sections:
        content_count = _approved_content_count(context, section.source)
        evidence = evidence_by_section[section.section_id]
        if content_count:
            status: Literal["filled", "hidden_empty", "review_required"] = "filled"
            reason = (
                "The section is populated exclusively from the approved "
                "client-facing render context."
            )
        elif evidence.status == "review_required":
            status = "review_required"
            reason = (
                "Preserved optional or custom source content could map here, but "
                "it is excluded until a recruiter promotes it into approved data."
            )
        else:
            status = "hidden_empty"
            reason = (
                "No approved client-facing content is available; the optional "
                "section remains hidden and no value is invented."
            )
        bindings.append(
            ProductionSectionBinding(
                section_id=section.section_id,
                candidate_source=section.source,
                status=status,
                approved_content_count=content_count,
                source_block_ids=evidence.candidate_block_ids,
                reason=reason,
            )
        )

    review_required = _review_required_blocks(evidence_plan)
    plan_warnings = list(warnings or [])
    if review_required:
        plan_warnings.append(
            "Preserved optional/custom source blocks were excluded from generated "
            "content pending explicit recruiter approval."
        )
    if layout_spec.structure_contract == "legacy":
        plan_warnings.extend(layout_spec.warnings)
        plan_warnings.append(
            "LEGACY ARTIFACT: the layout spec carries the retired v1 structure "
            "contract; reads are limited to genuinely stored legacy artifacts."
        )
    return ProductionRenderPlan(
        approved_profile_version_id=approved_profile_version_id,
        profile_sha256=profile_sha256,
        source_text_sha256=document.source_text_sha256,
        layout_schema_version=layout_spec.schema_version,
        layout_template_version=layout_spec.template_version,
        layout_spec_sha256=layout_spec_sha256(layout_spec),
        context=context,
        section_bindings=bindings,
        review_required_block_ids=review_required,
        warnings=plan_warnings,
    )


def _approved_content_count(
    context: ClientFacingRenderContext,
    source: CandidateBlockSource,
) -> int:
    values: dict[CandidateBlockSource, object] = {
        "contact": [context.candidate_heading, *context.contact_lines],
        "summary": context.professional_summary,
        "skills": [*context.skills, *context.skill_groups],
        "languages": context.languages,
        "work_experience": context.work_experience,
        "education": context.education,
        "certifications": context.certifications,
        "additional_details": [
            *context.additional_details,
            *context.additional_sections,
        ],
    }
    value = values[source]
    if value is None:
        return 0
    if isinstance(value, str):
        return int(bool(value.strip()))
    if isinstance(value, list):
        return len(value)
    return 1


def _review_required_blocks(plan: CandidateBlockBindingPlan) -> list[str]:
    block_ids = {
        block_id
        for binding in plan.bindings
        if binding.status == "review_required"
        for block_id in binding.candidate_block_ids
    }
    block_ids.update(plan.unmatched_candidate_block_ids)
    return sorted(block_ids)


def layout_spec_sha256(layout_spec: LayoutTemplateSpec) -> str:
    payload = json.dumps(
        layout_spec.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
