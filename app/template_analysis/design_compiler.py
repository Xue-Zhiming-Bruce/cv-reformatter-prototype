"""Deterministic compilation of a validated DesignProposal into the
provider-neutral ``LayoutTemplateSpec`` (schema 2.0).

Only this module may promote a proposal into a stored product template.
Claude output is evidence; every value here is either copied from measured
evidence, derived from proposal references, or produced by deterministic rules.
"""

from __future__ import annotations

import hashlib
import json

from app.template_analysis.design_schemas import (
    DESIGN_COMPILER_VERSION,
    SUPPORTED_SEMANTIC_PLACEMENTS,
    DesignProposal,
    DesignRequest,
    SectionMapping,
    StyleRoleReference,
    TargetLayoutEvidence,
)
from app.template_analysis.schemas import (
    HeaderLayoutStyle,
    LayoutTemplateSpec,
    SectionLayoutSpec,
    TemplateStyleSpec,
    TextStyle,
)

#: Canonical candidate roles that map to the renderer's list-section vocabulary.
_LIST_SOURCE_ROLES = {"skills", "languages", "certifications"}
_RENDERABLE_SOURCE_ROLES = {
    "contact", "summary", "skills", "languages", "work_experience",
    "education", "certifications", "additional_details",
}


class DesignCompilerError(RuntimeError):
    """Raised when a validated proposal cannot be compiled."""


class UnsupportedPlacementError(DesignCompilerError):
    """Raised when a validated placement cannot be represented by the current
    renderer vocabulary. The design state becomes explicit ``unsupported``; a
    Claude placement decision is never silently replaced with a default."""


def compile_layout_template_spec(
    request: DesignRequest,
    proposal: DesignProposal,
    evidence: TargetLayoutEvidence,
    style_spec: TemplateStyleSpec,
) -> LayoutTemplateSpec:
    """Compile a validated proposal into a versioned LayoutTemplateSpec."""
    if proposal.declared_unsupported:
        raise DesignCompilerError(
            "declared-unsupported proposals are not compiled into templates"
        )

    sections = _compile_sections(request, proposal, evidence, style_spec)
    header = _compile_header(evidence)
    unrenderable_preserved = sorted({
        mapping.source_role for mapping in proposal.mappings
        if mapping.action in {"preserve_as_additional", "flag_unmatched", "needs_review"}
        and mapping.source_role not in _RENDERABLE_SOURCE_ROLES
    })
    payload = style_spec.model_dump(exclude={"schema_version"})
    payload.update(
        {
            "schema_version": "2.0",
            "template_name": f"designed_{proposal.proposal_id[:8]}",
            "template_version": f"design-{_proposal_checksum(proposal)[:10]}",
            "structure_contract": (
                "measured"
                if evidence.header_structure is not None
                and evidence.section_structure
                else "limited_capability"
            ),
            "columns": _columns_for_layout(proposal.layout_class, evidence),
            "sections": sections,
            "header": header,
            "unsupported_features": [
                *proposal.unsupported_features,
                *[f"preserved_unrenderable_source:{role}" for role in unrenderable_preserved],
            ],
            "warnings": [
                *style_spec.warnings,
                *proposal.warnings,
                *[
                    f"Inter-section gap unmeasured for section '{section.label}'; "
                    "renderer falls back to global section spacing pending review."
                    for section in sections
                    if section.show_heading
                    and section.mapping_action == "map"
                    and section.spacing is None
                ],
                *[
                    f"Source section '{role}' is preserved in the mapping plan but "
                    "cannot render until CandidateProfile supports that source role."
                    for role in unrenderable_preserved
                ],
                "Design proposal compiled deterministically; no geometry was invented.",
            ],
        }
    )
    return LayoutTemplateSpec.model_validate(payload)


_DEFAULT_SECTION_ORDER: list[str] = [
    "summary", "work_experience", "education", "skills",
    "languages", "certifications", "additional_details",
]


def compile_measured_layout_template_spec(
    evidence: TargetLayoutEvidence,
    style_spec: TemplateStyleSpec,
) -> LayoutTemplateSpec:
    """Compile the artifact-root template directly from measured structure.

    When section structure is not detected, falls back to limited_capability:
    the measured visual style (fonts, colours, spacing) is applied but section
    structure uses standard defaults. Structure contract is set to
    ``limited_capability`` so callers can distinguish the two cases.
    """
    if evidence.header_structure is not None and evidence.section_structure:
        proposal = DesignProposal(
            proposal_id=f"measured-{evidence.target_checksum[:16]}",
            layout_class=evidence.layout_class_hint,
            confidence=1.0,
            reason_codes=["measured_structure"],
            warnings=["Artifact-root template compiled from measured target structure."],
        )
        request = DesignRequest(
            target_evidence_version=evidence.evidence_version,
            target_checksum=evidence.target_checksum,
            target_format=evidence.target_format,
            layout_class_hint=evidence.layout_class_hint,
            supported_style_roles=[role.role_id for role in evidence.style_roles],
            measured_regions=evidence.regions,
            measured_style_roles=evidence.style_roles,
            evidence_warnings=evidence.warnings,
            evidence_unsupported_features=evidence.unsupported_features,
        )
        compiled = compile_layout_template_spec(request, proposal, evidence, style_spec)
        return compiled.model_copy(update={"structure_contract": "measured"})

    # Visual data is available but structural detection failed: produce a
    # limited_capability spec with default sections and measured visual style.
    header = _compile_header(evidence)
    # When header_structure is missing, _compile_header returns HeaderLayoutStyle()
    # with contact_fields=None. The renderer then uses contact_items (empty for
    # synthetic proofs) and renders nothing, causing a count mismatch against the
    # validation's contact-fallback expectation. Explicitly set standard fields so
    # the renderer can render contact lines and pass structural validation.
    if not header.contact_fields:
        header = HeaderLayoutStyle(
            **{**header.model_dump(), "contact_fields": ["email", "phone", "location", "linkedin_url", "portfolio_url"]}
        )
    default_sections: list[SectionLayoutSpec] = []
    for source in _DEFAULT_SECTION_ORDER:
        default_sections.append(
            SectionLayoutSpec(
                section_id=f"default-{source}",
                source=source,  # type: ignore[arg-type]
                label=_default_label(source, style_spec),
                layout=_default_layout(source, style_spec),
                placement="full_width",
                optional=True,
            )
        )
    payload = style_spec.model_dump(exclude={"schema_version"})
    payload.update(
        {
            "schema_version": "2.0",
            "template_name": f"visual-only-{evidence.target_checksum[:16]}",
            "template_version": "limited",
            "structure_contract": "limited_capability",
            "columns": evidence.columns.model_copy(deep=True, update={"count": 1}).model_dump(),
            "sections": [s.model_dump() for s in default_sections],
            "header": header.model_dump(),
            "unsupported_features": ["section_structure_undetected"],
            "warnings": [
                *style_spec.warnings,
                "Section structure was not detected from the target PDF; "
                "standard section layout is used with measured visual style.",
            ],
        }
    )
    return LayoutTemplateSpec.model_validate(payload)


def proposal_checksum(proposal: DesignProposal) -> str:
    return _proposal_checksum(proposal)


def compiler_version() -> str:
    return DESIGN_COMPILER_VERSION


def _compile_sections(
    request: DesignRequest,
    proposal: DesignProposal,
    evidence: TargetLayoutEvidence,
    style_spec: TemplateStyleSpec,
) -> list[SectionLayoutSpec]:
    if evidence.section_structure:
        return _compile_measured_sections(evidence, style_spec, proposal)
    by_id = {mapping.mapping_id: mapping for mapping in proposal.mappings}
    roles_by_id = {role.role_id: role for role in evidence.style_roles}
    regions_by_id = {
        key: region
        for region in evidence.regions
        for key in (region.region_id, f"element.{region.region_id}")
    }
    sections: list[SectionLayoutSpec] = []
    ordered_ids = proposal.section_order or [mapping.mapping_id for mapping in proposal.mappings]
    seen: set[str] = set()
    for mapping_id in ordered_ids:
        mapping = by_id.get(mapping_id)
        if mapping is None:
            raise DesignCompilerError(f"section_order references unknown mapping '{mapping_id}'")
        if mapping.mapping_id in seen:
            raise DesignCompilerError(f"duplicate mapping '{mapping_id}' in section_order")
        seen.add(mapping.mapping_id)
        if mapping.action == "hide_empty":
            continue
        preserve = mapping.action in {
            "preserve_as_additional", "flag_unmatched", "needs_review"
        }
        if mapping.source_role not in _RENDERABLE_SOURCE_ROLES:
            if preserve:
                continue
            raise DesignCompilerError(
                f"mapped source role '{mapping.source_role}' is not renderable"
            )
        placement = "full_width" if preserve else _placement_for(mapping)
        label = mapping.target_label or _default_label(mapping.source_role, style_spec)
        if preserve:
            heading_style = _required_style("global.heading", roles_by_id)
        else:
            region = regions_by_id.get(mapping.target_region_id or "")
            if region is None or not region.style_role_ref:
                raise DesignCompilerError(
                    f"mapping '{mapping.mapping_id}' lacks a measured heading style reference"
                )
            heading_style = _required_style(region.style_role_ref, roles_by_id)
        sections.append(
            SectionLayoutSpec(
                section_id=mapping.source_role,
                source=mapping.source_role,
                label=label,
                layout=_default_layout(mapping.source_role, style_spec),
                placement=placement,
                heading_style=heading_style,
                mapping_action="preserve_as_additional" if preserve else "map",
            )
        )
    return sections


def _compile_header(evidence: TargetLayoutEvidence) -> HeaderLayoutStyle:
    measured = evidence.header_structure
    if measured is None:
        return HeaderLayoutStyle()
    roles_by_id = {role.role_id: role for role in evidence.style_roles}
    contact_style = (
        _required_style(measured.contact_style_role_ref, roles_by_id)
        if measured.contact_style_role_ref
        else None
    )
    return HeaderLayoutStyle(
        show_candidate_subheading=measured.show_candidate_subheading,
        contact_fields=measured.contact_fields,
        contact_separator=measured.contact_separator,
        contact_style=contact_style,
        contact_line_height_pt=measured.contact_line_height_pt,
        name_gap_pt=measured.name_gap_pt,
    )


def _compile_measured_sections(
    evidence: TargetLayoutEvidence,
    style_spec: TemplateStyleSpec,
    proposal: DesignProposal,
) -> list[SectionLayoutSpec]:
    roles_by_id = {role.role_id: role for role in evidence.style_roles}
    regions_by_id = {
        key: region
        for region in evidence.regions
        for key in (region.region_id, f"element.{region.region_id}")
    }
    entry_title_style = (
        _required_style(evidence.entry_title_style_role_ref, roles_by_id)
        if evidence.entry_title_style_role_ref
        else None
    )
    metadata_style = (
        _required_style(
            evidence.header_structure.contact_style_role_ref,
            roles_by_id,
        )
        if evidence.header_structure is not None
        and evidence.header_structure.contact_style_role_ref
        else None
    )
    sections: list[SectionLayoutSpec] = []
    for index, measured in enumerate(evidence.section_structure):
        source = (
            "additional_details"
            if measured.source_hint == "additional_section"
            else measured.source_hint
        )
        region = regions_by_id.get(measured.region_id)
        heading_style = (
            _required_style(region.style_role_ref, roles_by_id)
            if region is not None and region.style_role_ref
            else _required_style("global.heading", roles_by_id)
        )
        heading_style = heading_style.model_copy(
            update={
                "character_spacing_pt": style_spec.heading.character_spacing_pt,
                "horizontal_scale_percent": style_spec.heading.horizontal_scale_percent,
            }
        )
        measured_entry_title = (
            _required_style(measured.entry_title_style_role_ref, roles_by_id)
            if measured.entry_title_style_role_ref else entry_title_style
        )
        measured_metadata = (
            _required_style(measured.entry_metadata_style_role_ref, roles_by_id)
            if measured.entry_metadata_style_role_ref else metadata_style
        )
        spacing = (
            style_spec.spacing.model_copy(
                update={
                    "section_before_pt": region.spacing_before_pt,
                    "section_after_pt": (
                    region.spacing_after_pt
                    if region.spacing_after_pt is not None
                    else style_spec.spacing.section_after_pt
                    ),
                }
            )
            if region is not None and region.spacing_before_pt is not None
            else None
        )
        sections.append(
            SectionLayoutSpec(
                section_id=f"target-{index}-{source}",
                source=source,
                label=measured.label or "",
                layout=measured.layout or _default_layout(source, style_spec),
                placement="full_width",
                heading_style=heading_style,
                spacing=spacing,
                entry_title_style=(
                    measured_entry_title
                    if source in {"work_experience", "additional_details"}
                    else None
                ),
                entry_metadata_style=(
                    measured_metadata
                    if source in {"work_experience", "additional_details"}
                    else None
                ),
                split_entry_rows=(source == "work_experience"),
                page_break_before=measured.page_break_before,
                entry_gap_pt=measured.entry_gap_pt,
                entry_title_to_metadata_gap_pt=measured.entry_title_to_metadata_gap_pt,
                skill_group_gap_pt=measured.skill_group_gap_pt,
                skill_first_row_adjustment_pt=measured.skill_first_row_adjustment_pt,
                entry_secondary_style=(
                    _required_style(measured.entry_secondary_style_role_ref, roles_by_id)
                    if measured.entry_secondary_style_role_ref else None
                ),
                skill_label_style=(
                    _required_style(measured.skill_label_style_role_ref, roles_by_id)
                    if measured.skill_label_style_role_ref else None
                ),
                education_institution_style=(
                    _required_style(measured.education_institution_style_role_ref, roles_by_id)
                    if measured.education_institution_style_role_ref else None
                ),
                show_heading=measured.show_heading,
                contact_fields=measured.contact_fields,
                additional_section_heading=(
                    measured.label
                    if measured.source_hint == "additional_section"
                    else None
                ),
                badge_style=measured.badge_style,
            )
        )
    represented = {
        measured.source_hint
        for measured in evidence.section_structure
        if measured.source_hint != "additional_section"
    }
    if evidence.header_structure and evidence.header_structure.contact_fields:
        represented.add("contact")
    for mapping in proposal.mappings:
        source = mapping.source_role
        if (
            mapping.action == "hide_empty"
            or source in represented
            or source not in _RENDERABLE_SOURCE_ROLES
        ):
            continue
        sections.append(
            SectionLayoutSpec(
                section_id=f"preserved-{source}",
                source=source,
                label=mapping.target_label or _default_label(source, style_spec),
                layout=_default_layout(source, style_spec),
                placement="full_width",
                heading_style=_required_style("global.heading", roles_by_id),
                mapping_action="preserve_as_additional",
            )
        )
        represented.add(source)
    return sections


def _placement_for(mapping: SectionMapping) -> str:
    placement = mapping.target_semantic_role
    if placement not in SUPPORTED_SEMANTIC_PLACEMENTS:
        raise UnsupportedPlacementError(
            f"placement '{placement}' for mapping '{mapping.mapping_id}' cannot be "
            "represented by the current renderer; the design is unsupported"
        )
    return placement


def _required_style(
    role_id: str, roles_by_id: dict[str, StyleRoleReference]
) -> TextStyle:
    role = roles_by_id.get(role_id)
    if role is None:
        raise DesignCompilerError(f"missing measured style role '{role_id}'")
    return _text_style_from_role(role)


def _text_style_from_role(role: StyleRoleReference) -> TextStyle:
    missing = [
        field for field, value in (
            ("font_family", role.font_family),
            ("font_size_pt", role.font_size_pt),
            ("color_hex", role.color_hex),
        ) if value is None
    ]
    if missing:
        raise DesignCompilerError(
            f"style role '{role.role_id}' lacks measured {', '.join(missing)}"
        )
    return TextStyle(
        font_family=role.font_family,
        font_size_pt=role.font_size_pt,
        bold=bool(role.bold),
        color_hex=role.color_hex,
        line_height_pt=role.line_height_pt,
    )


def _default_layout(source: str, style_spec: TemplateStyleSpec) -> str:
    if source in _LIST_SOURCE_ROLES:
        return getattr(style_spec.lists, f"{source}_layout")
    return "full_width"


def _default_label(source: str, style_spec: TemplateStyleSpec) -> str:
    labels = style_spec.section_labels
    return (
        getattr(labels, source)
        if labels is not None and hasattr(labels, source)
        else source.replace("_", " ").title()
    )


def _columns_for_layout(
    layout_class: str, evidence: TargetLayoutEvidence
) -> dict[str, object]:
    if layout_class in {"two_column", "sidebar"}:
        columns = evidence.columns.model_copy(
            deep=True,
            update={"count": 2, "sidebar_side": evidence.columns.sidebar_side},
        )
        return columns.model_dump()
    return evidence.columns.model_copy(deep=True, update={"count": 1}).model_dump()


def _proposal_checksum(proposal: DesignProposal) -> str:
    payload = json.dumps(proposal.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
