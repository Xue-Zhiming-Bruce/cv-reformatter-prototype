"""Presentation labels and content-free Builder evidence for stage 5."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from pydantic import Field, model_validator
from app.pipeline_e.shared import EvidenceModel, PresentationLabel, TargetStructureDraft, DefectFinding, _sha256_file
from app.pipeline_e.candidate_boundary import _candidate_layout_footprint, _slot_shape_contract
from app.pipeline_e.resume_layout.text import _norm

def _presentation_label_catalog(
    derived: dict[str, Any], state: Any | None = None
) -> list[PresentationLabel]:
    """PROPOSE presentation labels from the measured sidebar-label evidence
    that already feeds the StructureDraft and Lane B evidence package.

    Every entry is `proposed` and stays proposed: the catalog is purely
    evidence-derived and NEVER approves anything by itself. Approval is an
    owner INPUT (`PresentationLabelApproval`) bound to this exact target and
    this exact catalog identity — not a property the shell can infer from
    text, file name, or hash. Measurement only establishes provenance, not
    label-vs-person-fact classification, so an unapproved entry must never
    become renderable or privacy-excluded.

    Deliberately minimal: ``section_heading`` is the only label type with
    reliable target structure evidence today. Contact ``field_label`` entries
    are NOT implemented — the two-column compile records those rows as a
    capability gap, so emitting them would fabricate evidence. No file-name or
    hash branch: the text is exactly what the measurement returned. A
    duplicate measured id fails closed."""
    catalog: list[PresentationLabel] = []
    seen: set[str] = set()
    for record in derived.get("sidebar_labels") or []:
        page = int(record["page"])
        top = float(record["top"])
        label_id = f"label.section.p{page}.top{top:.1f}"
        if label_id in seen:
            raise ValueError(
                f"duplicate presentation label id from the measurement: {label_id!r}"
            )
        seen.add(label_id)
        text = str(record.get("text") or "")
        catalog.append(
            PresentationLabel(
                label_id=label_id,
                text=text,
                kind="section_heading",
                evidence_ids=[f"local_pdf.sidebar_label.p{page}.top{top:.1f}"],
            )
        )
    if catalog or state is None:
        return catalog
    for node in state.nodes:
        if node.kind != "heading" or not node.label:
            continue
        catalog.append(
            PresentationLabel(
                label_id=f"label.{node.node_id}",
                text=node.label,
                kind="section_heading",
                evidence_ids=list(node.evidence_ids),
            )
        )
    return catalog


_SAFE_SECTION_TITLES = frozenset({
    "contact", "contact info", "about me", "summary", "professional summary",
    "profile", "highlights", "experience", "work experience",
    "employment", "employment history", "work history", "business experience", "education",
    "education certifications", "certifications", "skills", "key skills",
    "technical skills", "skills pool", "languages", "projects", "awards",
    "achievements", "publications", "conferences", "references",
    "volunteering", "volunteer experience", "interests",
    "additional information", "additional details", "another section",
})


def _safe_section_title(text: str) -> bool:
    """Conservative semantic gate: never exempt arbitrary target text from privacy."""
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip() in _SAFE_SECTION_TITLES


def _automatic_presentation_labels(
    catalog: list[PresentationLabel], state: Any
) -> list[PresentationLabel]:
    """Qualify safe headings with matching measured section evidence;
    require a matching rule only when the target heading has one."""
    nodes = {node.node_id: node for node in state.nodes}
    rules = {rule.rule_id for rule in state.rules}
    qualified: list[PresentationLabel] = []
    for label in catalog:
        if not _safe_section_title(label.text):
            continue
        for node in state.nodes:
            if node.kind != "heading" or node.label != label.text:
                continue
            section_id = node.node_id.removesuffix(".heading")
            if (node.node_id == section_id or section_id not in nodes
                    or nodes[section_id].kind != "section"
                    or (node.rule_id is not None and node.rule_id not in rules)
                    or not set(label.evidence_ids) & set(node.evidence_ids)):
                continue
            qualified.append(label.model_copy(update={"status": "evidence_qualified"}))
            break
    return qualified


class PresentationLabelApproval(EvidenceModel):
    """OWNER APPROVAL INPUT for ONE proposed presentation-label catalog of
    ONE target (owner-approved mechanism 2026-09-22; the owner has NOT yet
    approved any concrete label). Approval is identity-bound, never inferred:

    - ``target_sha256`` must equal the SHA-256 of the run's exact target PDF;
    - ``catalog_sha256`` must equal the canonical identity hash of the
      evidence-derived proposed catalog (label_id/text/kind/evidence_ids);
    - ``approved_label_ids`` must be non-empty, unique, and every id must
      exist in that proposed catalog.

    The model carries NO text, kind, or evidence: approval can never submit
    or override label content. The same wording in another target yields a
    different target/catalog identity, so approval cannot be inherited."""

    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_label_ids: list[str]

    @model_validator(mode="after")
    def ids_nonempty_and_unique(self) -> "PresentationLabelApproval":
        if not self.approved_label_ids:
            raise ValueError("presentation-label approval requires at least one label id")
        if any(not label_id.strip() for label_id in self.approved_label_ids):
            raise ValueError("presentation-label approval label ids must be non-empty")
        if len(set(self.approved_label_ids)) != len(self.approved_label_ids):
            raise ValueError("presentation-label approval label ids must be unique")
        return self


def presentation_label_catalog_sha256(catalog: list[PresentationLabel]) -> str:
    """Deterministic identity of the EVIDENCE-DERIVED proposed catalog:
    canonical JSON (labels sorted by label_id, object keys sorted, UTF-8) over
    label_id / text / kind / evidence_ids. `status` is deliberately EXCLUDED:
    status comes from the owner approval, not from the evidence."""
    payload = [
        {
            "label_id": label.label_id,
            "text": label.text,
            "kind": label.kind,
            "evidence_ids": list(label.evidence_ids),
        }
        for label in sorted(catalog, key=lambda label: label.label_id)
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _apply_presentation_label_approval(
    catalog: list[PresentationLabel],
    approval: PresentationLabelApproval | None,
    *,
    target_sha256: str,
) -> list[PresentationLabel]:
    """Validate an owner approval against THIS run's target and the proposed
    catalog, and project the approved view (the approved ids' entries with
    status flipped to `approved`; content is copied from the catalog, never
    from the approval). ``approval=None`` is the default and means ZERO
    approved labels. Any identity mismatch (target, catalog, or label id)
    fails closed."""
    if approval is None:
        return []
    if approval.target_sha256 != target_sha256:
        raise ValueError(
            "presentation-label approval target_sha256 does not match the current target"
        )
    if approval.catalog_sha256 != presentation_label_catalog_sha256(catalog):
        raise ValueError(
            "presentation-label approval catalog_sha256 does not match the current proposed catalog"
        )
    known = {label.label_id: label for label in catalog}
    unknown = [label_id for label_id in approval.approved_label_ids if label_id not in known]
    if unknown:
        raise ValueError(
            f"presentation-label approval references unknown label ids: {unknown}"
        )
    unsafe = [label_id for label_id in approval.approved_label_ids
              if not _safe_section_title(known[label_id].text)]
    if unsafe:
        raise ValueError(f"presentation-label approval contains target-person or ambiguous text: {unsafe}")
    return [
        known[label_id].model_copy(update={"status": "approved"})
        for label_id in approval.approved_label_ids
    ]




def _e5_label_semantics(
    gate: dict[str, Any], renderable: list[PresentationLabel]
) -> dict[str, Any]:
    """Whether this lane's privacy gate excluded exactly the renderable labels."""

    excluded = {_norm(item) for item in (gate.get("excluded_labels") or [])}
    renderable_texts = {_norm(label.text) for label in renderable}
    return {
        "renderable_label_ids": [label.label_id for label in renderable],
        "symmetric": excluded == renderable_texts,
        "excluded_labels": sorted(excluded),
        "excluded_outside_renderable": sorted(excluded - renderable_texts),
        "renderable_texts_never_excluded": sorted(renderable_texts - excluded),
    }




def _initial_builder_draft(draft: TargetStructureDraft) -> dict[str, Any]:
    """Keep measured claims intact; candidate shape is a separate typed input."""
    return {
        "schema_version": draft.schema_version,
        "target_id": draft.target_id,
        "investigator": draft.investigator,
        "structure": [claim.model_dump(mode="json") for claim in draft.structure],
        "unresolved": [item.model_dump(mode="json") for item in draft.unresolved],
        "self_reported": draft.self_reported.model_dump(mode="json"),
    }


def _lane_b_repair_boundary(
    finding: DefectFinding,
    *,
    candidate: Any,
    missing_leaf_ids: list[str] | None = None,
    body_color_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing = list(missing_leaf_ids or [])
    by_id = {leaf.leaf_id: leaf for leaf in candidate.leaves}
    kinds = sorted({by_id[leaf_id].kind for leaf_id in missing if leaf_id in by_id})
    accounting = finding.suspected_dimension == "hard_gate_content"
    unreadable = finding.suspected_dimension == "unreadable_body"
    font_mismatch = finding.suspected_dimension == "font_role_mismatch"
    orphan = finding.suspected_dimension == "orphan_tail_page"
    orphan_heading = finding.suspected_dimension == "orphan_heading"
    return {
        "selected_region": finding.region,
        "affected_page": finding.page,
        "allowed_changes": (
            ["bind the specified missing leaves through the correct existing slot token"]
            if accounting else
            ["restore actual PDF body size to at least 10pt through page width and typography without dropping content"]
            if unreadable else
            ["set each mismatched role to the required font family, weight, and size from target_font_roles without dropping content"]
            if font_mismatch else
            ["reduce section, entry, and list spacing or repair breaks without shrinking body type"]
            if orphan else
            ["keep the heading and first entry together with a section page break; preserve content and type size"]
            if orphan_heading else [f"repair only {finding.region}:{finding.suspected_dimension}"]
        ),
        "preserve_unless_measured": [
            *([] if unreadable or font_mismatch else ["typography"]), "colors",
            *([] if orphan else ["spacing"]), "header",
            "unrelated_sections_and_layout",
            {"ordinary_body_color": body_color_context} if body_color_context else {},
        ],
        "affected_leaf_kinds": kinds,
        "missing_leaf_ids": missing,
        "representation_limits": _slot_shape_contract(),
    }


def _e5_builder_evidence_package(
    *,
    draft: TargetStructureDraft,
    state: Any,
    derived: dict[str, Any],
    page_size: tuple[float, float],
    current_render_version: str | None = None,
    findings: list[DefectFinding] | None = None,
    measurements: list[MeasurementResult] | None = None,
    current_gates: dict[str, Any] | None = None,
    last_rejection: str | None = None,
    target_images: list[Path] | None = None,
    current_render_images: list[Path] | None = None,
    current_representation: Any | None = None,
    selected_attribution: AttributionRecord | None = None,
    action_fingerprint: str | None = None,
    presentation_labels: list[PresentationLabel] | None = None,
    candidate_review_scope: dict[str, Any] | None = None,
    candidate: Any | None = None,
    initial_compact: bool = False,
    candidate_compatible_labels: dict[str, str] | None = None,
    body_color_context: dict[str, Any] | None = None,
    font_roles: dict[str, Any] | None = None,
    missing_leaf_ids: list[str] | None = None,
    compact: bool = False,
) -> dict[str, Any]:
    """The Builder evidence package.

    Repair calls additionally receive
    the current render images outside this JSON package and the exact validated
    representation that produced them. `presentation_labels` is the SAME
    shell-owned catalog for Lane B: the only visible fixed text a template
    may carry, referenceable by `label_id` only.
    """
    package = {
        "schema_version": "e5-builder-evidence/2",
        "presentation_labels": [
            label.model_dump(mode="json") for label in presentation_labels or []
        ],
        "candidate_review_scope": candidate_review_scope or {},
        "slot_shape_contract": _slot_shape_contract(),
        "current_render_version": current_render_version,
        "findings": [finding.model_dump(mode="json") for finding in findings or []],
        "measurements": [result.model_dump(mode="json") for result in measurements or []],
        "current_gates": current_gates or {},
        "last_rejection": last_rejection,
        "target_images": [
            {"name": image.name, "sha256": _sha256_file(image)} for image in target_images or []
        ],
        "current_render_images": [
            {"name": image.name, "sha256": _sha256_file(image)}
            for image in current_render_images or []
        ],
        "current_representation": (
            current_representation.model_dump(mode="json")
            if current_representation is not None
            else None
        ),
        "selected_attribution": (
            selected_attribution.model_dump(mode="json") if selected_attribution else None
        ),
        "action_fingerprint": action_fingerprint,
    }
    if candidate is not None:
        package["candidate_layout_footprint"] = _candidate_layout_footprint(candidate)
    if candidate_compatible_labels is not None:
        package["candidate_compatible_labels"] = candidate_compatible_labels
    if body_color_context is not None:
        package["target_body_text_color_context"] = body_color_context
    if font_roles is not None:
        package["target_font_roles"] = font_roles
    if not compact:
        package["structure_draft"] = (
            _initial_builder_draft(draft)
            if initial_compact else draft.model_dump(mode="json")
        )
        package["underlying_evidence_summary"] = {
            "page_size_pt": list(page_size),
            "state_sections": [
                {
                    "node_id": node.node_id,
                    "kind": node.kind,
                    "sources": getattr(node.binding, "sources", None) if node.binding else None,
                }
                for node in state.nodes
                if node.kind == "section"
            ],
            "sidebar_rules": derived.get("sidebar_rules"),
            "sidebar_labels": derived.get("sidebar_labels"),
        }
    elif findings and findings[0].requested_measurement.metric == "header_vertical_rule_geometry":
        header_claim = next(
            (
                claim for claim in draft.structure
                if "header" in claim.statement.casefold()
                and len(re.findall(r"(\d+(?:\.\d+)?)\s*pt[^.;]*?weight\s*(\d+)", claim.statement, re.I)) >= 2
            ),
            None,
        )
        typography = (
            [
                {"line": index, "font_size_pt": float(size), "font_weight": int(weight)}
                for index, (size, weight) in enumerate(
                    re.findall(
                        r"(\d+(?:\.\d+)?)\s*pt[^.;]*?weight\s*(\d+)",
                        header_claim.statement,
                        re.I,
                    )[:2],
                    1,
                )
            ]
            if header_claim else []
        )
        package["region_repair_context"] = {
            "selected_region": "header_name",
            "affected_page": findings[0].page,
            "structure_claim": (
                {
                    "claim_id": header_claim.claim_id,
                    "relation": header_claim.relation,
                    "evidence_ids": [ref.evidence_id for ref in header_claim.evidence],
                }
                if header_claim else None
            ),
            "target_name_typography": typography,
            "region_images": {
                "target": "target_images",
                "current_render": "current_render_images",
                "scope": "page 1 top 25% original-resolution crop",
            },
            "allowed_changes": ["header_vertical_rule_dom_css"],
            "preserve_unless_measured": [
                "current_name_font_size",
                "current_name_font_weight",
                "current_name_color",
                "current_name_letter_spacing",
                "unrelated_sections_and_layout",
                {"ordinary_body_color": body_color_context} if body_color_context else {},
            ],
            "affected_leaf_kinds": ["header_field"],
            "representation_limits": {
                "candidate:name": (
                    "atomic full name; use typed given/family slots together only when verified by CandidateProfile"
                )
            },
        }
    elif compact and findings and candidate is not None:
        package["region_repair_context"] = _lane_b_repair_boundary(
            findings[0], candidate=candidate, missing_leaf_ids=missing_leaf_ids,
            body_color_context=body_color_context,
        )
    return package
