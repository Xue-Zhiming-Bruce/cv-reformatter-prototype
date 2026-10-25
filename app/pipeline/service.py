"""Shared pipeline service implementation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from pydantic import Field, model_validator
from app.pipeline.candidate_content import CandidateDocument
from app.template_analysis.schemas import LayoutTemplateSpec
from app.pipeline.rendering import ROOT, RUNS, _export_pinned_html_to_pdf, _render_pages
from app.pipeline.runtime import (
    BudgetExhausted,
    CheckpointBudgetExceeded,
    EvidenceStore,
    RunBudget,
    RunTrace,
    _record_usage,
)
from app.pipeline.measurement import _pdf_lines_and_marks, pinned_export_environment
from app.pipeline.evidence import (
    AttributionRecord,
    CandidateBindingLineage,
    DefectFinding,
    DualSourcePod,
    E4_PER_CALL_MAX_TOOL_CALLS,
    E4_TEMPERATURE,
    E5AgentAuditSpec,
    EvidenceModel,
    FrozenCase,
    LiveAttributionHypothesis,
    MeasurementRequest,
    MeasurementResult,
    PresentationLabel,
    RenderVersion,
    TargetStructureDraft,
    _call_limits,
    _e5_agent_audit_record,
    _e5_candidate_binding_lineage,
    _e5_live_attribution_record,
    _live_model_settings,
    _model_identity,
    _overview_pngs,
    _page_pt_size,
    _pdf_line_rows,
    _pod_tools,
    _sha256_file,
    _side_by_side,
    _traced_render_words,
    assert_no_signed_strings,
    freeze_cases,
    redact_signed_urls,
)
from app.pipeline.review import (
    E2_IMPROVEMENT_TOLERANCE_PT,
    MeasureController,
    SCRIPTED_OBSERVATION_BY_TARGET,
    SCRIPTED_REGION_BY_TARGET,
    SCRIPTED_REQUEST_BY_TARGET,
    ScriptedReviewer,
    _live_reviewer_findings,
    _two_column_bullet_tiers,
    _validate_finding_versions,
    compile_two_column_state,
    compile_two_column_state_for_scaffold,
    prepare_review_image,
)

E5_SCHEMA_VERSION = "pipeline-e-e5-state/1"

E5_LANE_MAX_MODEL_REQUESTS = 400

E5_LANE_MAX_TOOL_CALLS = 2000

E5_MAX_REPAIR_ROUNDS = 1

E5_BUILDER_RESERVE_REQUESTS = 40

E5_REVIEWER_MAX_REQUESTS = 4

E5_BUILDER_MAX_REQUESTS = 4

E5_ATTRIBUTION_MAX_REQUESTS = 8  # floor only; the batch envelope is derived below
HEADER_VERTICAL_RULE_MEASUREMENT_VERSION = "header_vertical_rule_geometry/2"



E5_REVIEWER_INSTRUCTIONS = (
    "You are the independent Visual Reviewer of a resume-layout experiment.\n"
    "You inspect the TARGET and the RENDERED page images you are given. You\n"
    "report LOCALIZED visible differences only.\n"
    "Phase 0 (E5): you report a SEMANTIC measurement intent, never exact\n"
    "verbatim PDF text anchors. Set the measurement request's `intent` to a\n"
    "plain-language description of WHAT to measure (which two elements, which\n"
    "property) — the shell resolves it against the actual final-PDF objects.\n"
    "You do NOT need to read exact text strings out of the image; leaving the\n"
    "four anchor fields empty is correct.\n"
    "Hard boundaries: you never decide the root cause layer; `proposed_cause`\n"
    "is a hypothesis for verification, never a verdict. You never approve\n"
    "delivery. You never modify anything. Every finding binds the exact\n"
    "target and render version strings you were given and carries one typed\n"
    "measurement request with `intent` set.\n"
    "Target person facts and values are diagnostic evidence only; they never\n"
    "become candidate content. The render's different name, contact values,\n"
    "employers, languages and synthetic placeholder tokens are expected\n"
    "candidate-owned content, not substitutions or missing target facts.\n"
    "Never report value inequality as a defect or request target-value copying.\n"
    "Compare layout, typography, spacing, alignment,\n"
    "rules, colors, and structure only where the candidate inventory carries\n"
    "the same content shape. If the target has a content shape absent from the\n"
    "candidate inventory, do not request that it be copied or recreated. A\n"
    "page-local review may say that a section appears on a different page, but\n"
    "must never claim the whole document lacks a section from one page image.\n"
    "Use metric='role_gap' only for entry/job/role spacing. Use\n"
    "metric='header_vertical_rule_geometry' only for a clearly visible vertical\n"
    "rule beside or through the name/header region. For every other property,\n"
    "use metric='unsupported' and describe the desired measurement in intent.\n"
    "Do not substitute role_gap or header_vertical_rule_geometry for alignment,\n"
    "font, color, contact-row styling, or other unsupported properties. These\n"
    "observations remain open warnings; no metric value or cause is established."
)

E5_LANE_A_BUILDER_INSTRUCTIONS = (
    "You are the Lane A Builder of a resume-layout experiment.\n"
    "Representation: a provider-neutral STRUCTURED LAYOUT interpreted by a\n"
    "FIXED renderer. You output ONLY typed, validated structure primitives —\n"
    "never executable code, never CSS, never candidate text. Allowed\n"
    "reusable primitives per section: heading_in_rail (the section heading\n"
    "renders in a left label rail beside its content), rail_label_width_pt\n"
    "(the label-rail column width), rail_label_align (left/right), and\n"
    "entry_meta_placement='title_row' (the entry meta column shares the\n"
    "first title line's row). Unknown or invalid fields fail validation.\n"
    "Hard boundaries: candidate facts are NEVER editable and never appear in\n"
    "your output; target person strings never appear in your output; no\n"
    "target-specific identifiers (no target hash, filename, or person\n"
    "branch). You never approve delivery. Base every primitive value on the\n"
    "evidence-linked structure draft and the recorded measurements you are\n"
    "given. On repair calls, `current_representation` is the exact validated\n"
    "proposal that produced the repair-base render; modify it and return one\n"
    "complete replacement proposal. It is null only for the initial build."
)

E5_LANE_B_BUILDER_INSTRUCTIONS = (
    "You are the Lane B Builder of a resume-layout experiment.\n"
    "Representation: a constrained AUTHORED HTML/CSS template with typed\n"
    "candidate slots. You output ONE typed AuthoredTemplateCandidate:\n"
    "HTML (slot tokens only, no values), CSS, and typed slots declared by\n"
    "CATEGORY only (no token strings, no free-text fields).\n"
    "There are NO free-text metadata fields (no rationale, no region lists,\n"
    "no pagination prose, no expected measurements, no slot descriptions).\n"
    "For this E5 lane evidence_refs must be the empty list []. The shell\n"
    "already persists the evidence package; do not copy its ids here.\n"
    "Slot vocabulary (exactly these tokens):\n"
    "{{candidate:name}}, {{candidate:contact}}, and repeating regions\n"
    "{{each:summary}}/{{each:experience}}/{{each:education}}/{{each:skills}}/"
    "{{each:languages}}/{{each:certifications}}/{{each:additional}} closed\n"
    "with {{/each}}; inside a region use {{item}}, {{item_head}},\n"
    "{{item_detail}}, {{item_meta}}, {{item_bullets}}, {{item_text}}.\n"
    "`{{item_bullets}}` already expands to <li> nodes whose source text owns\n"
    "its bullet glyph. Put it directly inside a <ul>, set list-style:none,\n"
    "and NEVER add a native, background, image, or pseudo-element marker.\n"
    "For header rules, preserve the measured target topology: separate rule\n"
    "segments above and below a name must keep the name-height gap. NEVER\n"
    "merge them into a continuous or page-spanning rail.\n"
    "When `target_body_text_color_context` is present, use its measured\n"
    "dominant_color_hex for ordinary body copy only. Do not use it to split\n"
    "or reinterpret the atomic candidate:name slot, or to overwrite distinct\n"
    "heading, name, or rule styles.\n"
    "Missing optional fields must not leave broken visual artifacts (empty\n"
    "regions render nothing). Long content must not disappear.\n"
    "The shell REJECTS: JavaScript, event handlers, iframe/object/embed/\n"
    "form/video/audio/canvas, remote or network URLs, src/href attributes,\n"
    "CSS imports, url() functions, data: URLs, HTML or CSS comments, the\n"
    "CSS content: property, any target-person literal, any hardcoded\n"
    "candidate fact, and any undeclared slot token.\n"
    "Fixed visible TEXT is not allowed at all: no letter or digit may appear\n"
    "as a literal text node (HTML tag/class/attribute names and CSS are\n"
    "fine). The only visible fixed text you may render is a presentation\n"
    "label, referenced as {{label:<label_id>}} using a label_id from the\n"
    "shell's presentation_labels catalog in your evidence package; the shell\n"
    "owns the text and escapes it. An id you were not given is rejected.\n"
    "You never see or edit candidate values; the shell fills your slots from\n"
    "the reviewed candidate render context. You never approve delivery.\n"
    "Base the design on the target page images and the structure draft you\n"
    "are given. On repair calls, `current_representation` is the exact\n"
    "validated template that produced the repair-base render; modify it and\n"
    "return one complete replacement template. Obey any `region_repair_context`:\n"
    "change only its allowed_changes, and preserve every listed neighboring\n"
    "property unless a corresponding measurement/evidence authorizes that\n"
    "change. It is null only for the initial build."
)

E5_ATTRIBUTION_INSTRUCTIONS = (
    "You are the independent Attribution Investigator of a resume-layout\n"
    "experiment. For each finding trace the chain: raw target evidence ->\n"
    "structure -> template slot -> candidate binding -> render plan ->\n"
    "DOM/CSS -> actual final PDF object. Return ONE typed hypothesis PER\n"
    "FINDING and set finding_id to that finding's EXACT id. Order does not\n"
    "matter because the shell binds by finding_id, but a missing, empty,\n"
    "duplicated, or foreign finding_id is rejected and adds nothing. The\n"
    "reviewer's proposed_cause is a hypothesis that may be wrong; verify from\n"
    "evidence. You may replace the causal hypothesis but never delete the\n"
    "observation, and you never promote or repair anything.\n"
    "Builder ownership is only valid for a confirmed compilation defect: use\n"
    "attribution='template_compilation' with hypothesis_status='confirmed' and\n"
    "repair_owner='builder'. Any other repair_owner='builder' claim is\n"
    "contradictory and is retained as unresolved/reviewer.\n"
    "PRODUCT CONTRACT: the target supplies the PRESENTATION and the candidate\n"
    "supplies the FACTS. The target person's name, contact details, employers\n"
    "and history are target evidence, never the expected content of the\n"
    "render. Target-person text differing from the candidate's text is the\n"
    "contract, NOT a defect: never propose rebinding a different target\n"
    "person, never ask for target-person facts to be copied into the\n"
    "candidate, and never claim attribution='candidate_binding' on the ground\n"
    "that the two people's values differ. candidate_binding is confirmed only\n"
    "by deterministic shell lineage proof — a rendered leaf identity the\n"
    "registered candidate does not have, a value substituted under a\n"
    "candidate leaf's identity, or a leaf owned under a destination that is\n"
    "not its own slot; the shell rejects any other confirmed candidate_binding\n"
    "claim and fails closed. A STRUCTURAL presentation difference (a missing\n"
    "label/value row, rule, or column) belongs to the presentation layer:\
"
    "attribute it to the template/plan/renderer, or record it as a\n"
    "representation capability gap when the shell's representation cannot\n"
    "express that structure — never to candidate binding."
)

E5_REPRESENTATION_CEILING_GATES = ("content_shapes_match_evidence",)

E5_LANE_REPAIR_SURFACE: dict[str, str] = {
    "a": (
        "Lane A — provider-neutral structured layout interpreted by a FIXED\n"
        "renderer. The Builder can change ONLY these per-section primitives:\n"
        "heading_in_rail, rail_label_width_pt, rail_label_align,\n"
        "entry_meta_placement. It can NOT change the header block, the contact\n"
        "rows, section rule geometry, or anything the fixed renderer does not\n"
        "consume. A plan-level structural difference that those primitives CAN\n"
        "express is a template_compilation defect owned by this Builder; a\n"
        "difference they cannot express is the shell's representation\n"
        "capability gap, owned by nobody the loop can call."
    ),
    "b": (
        "Lane B — constrained authored HTML/CSS template with typed candidate\n"
        "slots. The Builder owns the whole template, so which candidate leaves\n"
        "render (and how) is template_compilation owned by this Builder;\n"
        "no_target_candidate_facts failures are never builder-ownable. Template\n"
        "safety is enforced before rendering by the authored-template validator."
    ),
}

class E5LedgerEntry(EvidenceModel):
    """One persistent defect-ledger record (Phase 0): deduplicated by the
    stable key `target_version | region | dimension`. Re-observing an
    unchanged finding never reruns full attribution; an observation that
    CHANGED on a NEW render version reopens the entry. `deferred` marks an
    open finding deliberately not selected this round (scheduling), never a
    closure: deferred entries stay open and are re-eligible."""

    ledger_key: str
    finding_id: str
    first_seen_version: str
    last_seen_version: str
    status: Literal[
        "open", "attributed", "repaired", "resolved", "regressed", "deferred"
    ]
    attribution: AttributionRecord | None = None
    measurement_request_id: str | None = None
    observation_class: str = ""
    last_measured_version: str = ""

def _observation_class(observation: str) -> str:
    """Stable semantic observation class: the observation's first normalized
    words (NOT verbatim OCR text — Phase 0)."""
    words = re.findall(r"[a-z]+", observation.casefold())
    return " ".join(words[:8])

def _candidate_review_scope(candidate: Any, labels: list[PresentationLabel]) -> dict[str, Any]:
    """Content-free Reviewer boundary: which presentation regions have
    candidate content, and which target-only section labels must not be
    requested as candidate sections."""
    available = {section.source for section in candidate.sections}
    if any(leaf.kind == "header_field" for leaf in candidate.leaves):
        available.add("contact")
    aliases = {
        "contact": "contact", "contact info": "contact",
        "about me": "summary", "profile": "summary", "summary": "summary",
        "experience": "work_experience", "work experience": "work_experience",
        "education": "education", "skills": "skills", "languages": "languages",
        "certifications": "certifications",
    }
    candidate_headings = {
        re.sub(r"[^a-z0-9]+", " ", (section.heading or "").casefold()).strip()
        for section in candidate.sections if section.heading
    }
    unavailable = []
    for label in labels:
        if label.kind != "section_heading":
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", label.text.casefold()).strip()
        role = aliases.get(normalized)
        if normalized not in candidate_headings and (role is None or role not in available):
            unavailable.append(label.text)
    leaf_kinds_by_role: dict[str, set[str]] = {}
    for leaf in candidate.leaves:
        role = "contact" if leaf.kind == "header_field" else leaf.source
        if role:
            leaf_kinds_by_role.setdefault(role, set()).add(leaf.kind)
    content_kinds_by_role: dict[str, set[str]] = {}
    for section in candidate.sections:
        content_kinds_by_role.setdefault(section.source, set()).add(section.content_kind)
    return {
        "available_section_roles": sorted(available),
        "content_kinds_by_role": {
            role: sorted(kinds) for role, kinds in sorted(content_kinds_by_role.items())
        },
        "available_leaf_kinds_by_role": {
            role: sorted(kinds) for role, kinds in sorted(leaf_kinds_by_role.items())
        },
        "has_language_proficiency_entries": any(
            leaf.kind == "language" for leaf in candidate.leaves
        ),
        "skills_only_skill_group": set(leaf_kinds_by_role.get("skills", ())) == {"skill_group"},
        "target_labels_without_candidate_content": unavailable,
    }

def _slot_shape_contract() -> dict[str, Any]:
    """Content-free description of the authored shell's existing bindings."""
    return {
        "candidate:name": {"type": "string", "atomic": True},
        "candidate:contact": {
            "type": "string", "shape": "flat_shell_joined_string",
            "cannot_reconstruct": "label_value_table",
        },
        "experience": {
            "item_head": "work_entry", "item_detail": "entry_detail",
            "item_meta": "entry_meta",
            "item_bullets": {
                "leaf_kind": "work_bullet", "dom": "shell_generated_li",
                "text_owns_bullet_glyph": True,
            },
        },
        "education": {
            "item_head": "education_entry", "item_detail": "entry_detail",
            "item_meta": "entry_meta",
        },
        "skills": {
            "item": "skill_group", "item_head": "skill_group",
            "item_text": "skill_children_only",
        },
    }

def _length_band(length: int) -> str:
    return "short" if length < 80 else "medium" if length < 240 else "long"

def _candidate_layout_footprint(candidate: Any) -> dict[str, Any]:
    """Counts and lengths only; never candidate values."""
    by_id = {leaf.leaf_id: leaf for leaf in candidate.leaves}
    result: dict[str, Any] = {}
    for section in candidate.sections:
        section_leaves = [by_id[leaf_id] for leaf_id in section.leaf_ids]
        roots = [leaf for leaf in section_leaves if leaf.parent_leaf_id is None]
        items = []
        for root in roots:
            children = [leaf for leaf in section_leaves if leaf.parent_leaf_id == root.leaf_id]
            length = sum(len(leaf.text or "") for leaf in [root, *children])
            items.append({
                "child_count": len(children),
                "bullet_count": sum(child.kind == "work_bullet" for child in children),
                "character_length": length,
                "length_band": _length_band(length),
            })
        total = sum(item["character_length"] for item in items)
        result[section.source] = {
            "entry_count": len(roots), "items": items,
            "total_character_length": total,
            "may_cross_page": total > 1800 or len(roots) > 4,
        }
    return result

def _candidate_compatible_labels(
    labels: list[PresentationLabel], review_scope: dict[str, Any]
) -> dict[str, str]:
    unavailable = set(review_scope.get("target_labels_without_candidate_content", []))
    aliases = {
        "contact info": "contact", "about me": "summary", "profile": "summary",
        "summary": "summary", "experience": "work_experience",
        "work experience": "work_experience", "education": "education",
        "skills": "skills", "languages": "languages",
        "certifications": "certifications",
    }
    return {
        label.text: (
            "omit_entire_section" if label.text in unavailable
            else aliases.get(label.text.casefold(), "candidate_compatible")
        )
        for label in labels if label.kind == "section_heading"
    }

def _candidate_boundary_filter_reason(
    finding: DefectFinding, review_scope: dict[str, Any]
) -> str | None:
    text = re.sub(
        r"[^a-z0-9]+", " ",
        f"{finding.observation} {finding.requested_measurement.intent or ''}".casefold(),
    ).strip()
    missing = any(word in text for word in ("missing", "absent", "omitted", "not present", "lacks"))
    region = re.sub(r"[^a-z0-9]+", " ", finding.region.casefold()).strip()
    unavailable = [
        re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()
        for label in review_scope["target_labels_without_candidate_content"]
    ]
    if any(
        label and label in text and (
            label in region
            or missing
            or any(word in text for word in ("copy", "replicate", "populate"))
        )
        for label in unavailable
    ):
        return "target_section_without_candidate_content"

    if any(term in text for term in ("first name", "last name", "split name", "two-line name")):
        return "representation_limit:atomic_candidate_name"
    if "contact" in text and any(
        term in text for term in ("label value", "label/value", "contact table", "typed row")
    ):
        return "representation_limit:flat_candidate_contact"

    dimension = finding.suspected_dimension.casefold()
    if (
        not review_scope.get("has_language_proficiency_entries")
        and any(term in text for term in (
            "language proficiency", "proficiency dot", "language entr", "languages sub block",
            "dot rating", "dot glyph", "bulleted language", "klingon", "elvish",
        ))
    ):
        return "candidate_content_shape_absent:language_proficiency"

    # A page image proves page placement, not global document absence. Keep a
    # page/pagination residual, but reject a global missing-section assertion.
    available_names = [
        *review_scope.get("available_section_roles", []),
        *review_scope.get("available_candidate_headings", []),
    ]
    available_names = [
        re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip().replace("_", " ")
        for value in available_names
    ]
    page_local = any(term in text for term in (
        "page ", "pagination", "page break", "flow", "moved", "appears later",
        "position on this page", "current page",
    ))
    if missing and "section" in text and not page_local and any(
        name and name in text for name in available_names
    ):
        return "page_local_global_missing_section_assertion"
    if (
        any(word in dimension for word in ("completeness", "content_presence", "facts"))
        and missing
    ):
        return "target_person_content_presence"
    if (
        any(term in text for term in ("target person", "target candidate", "target s"))
        and missing
        and any(term in text for term in (
            " fact", " value", " name", " language", " proficiency", " employer",
            " school", " date", " contact", " text", " wording", " detail", " entr",
        ))
    ):
        return "target_person_fact_or_value"
    return None

def _candidate_aware_findings(
    findings: list[DefectFinding], review_scope: dict[str, Any]
) -> list[DefectFinding]:
    """Keep presentation residuals; remove candidate-boundary violations."""
    return [
        finding for finding in findings
        if _candidate_boundary_filter_reason(finding, review_scope) is None
    ]

def _residual_type(finding: DefectFinding) -> str:
    text = f"{finding.suspected_dimension} {finding.observation}".casefold()
    for residual_type, words in (
        ("spacing", ("gap", "spacing", "distance", "margin", "padding")),
        ("alignment", ("align", "position", "anchor", "column")),
        ("typography", ("font", "type", "weight", "size", "tracking", "leading")),
        ("rule_or_shape", ("rule", "line", "border", "shape", "rail")),
        ("color", ("color", "fill", "contrast")),
        ("content_presence", ("missing", "absent", "omit", "content")),
    ):
        if any(word in text for word in words):
            return residual_type
    return "layout_other"

def _ledger_key(finding: DefectFinding) -> str:
    """Stable dedup key: target version + structural region + visual
    dimension (the observation CLASS is tracked separately so a changed
    finding REOPENS instead of forking a new key)."""
    return "|".join(
        (finding.target_version, finding.region, finding.suspected_dimension)
    )

class DefectLedger:
    """Persistent defect ledger (Phase 0). `observe` returns one of:
    'new' (first observation), 'dedup' (unchanged finding; existing
    attribution/measurement reused), or 'reopened' (the finding CHANGED after
    a repair — new observation class)."""

    def __init__(self) -> None:
        self.entries: dict[str, E5LedgerEntry] = {}

    def observe(
        self,
        finding: DefectFinding,
        *,
        resolved: bool = False,
    ) -> tuple[E5LedgerEntry, str]:
        key = _ledger_key(finding)
        existing = self.entries.get(key)
        if existing is None:
            entry = E5LedgerEntry(
                ledger_key=key,
                finding_id=finding.finding_id,
                first_seen_version=finding.render_version,
                last_seen_version=finding.render_version,
                status="resolved" if resolved else "open",
                observation_class=_observation_class(finding.observation),
                last_measured_version=finding.render_version,
            )
            self.entries[key] = entry
            return entry, "new"
        previous_version = existing.last_seen_version
        existing.last_seen_version = finding.render_version
        # A changed observation REOPENS only when the RENDER changed: the
        # reopen semantics mean "the defect changed after a repair". The
        # reviewer paraphrasing the SAME defect on the SAME render is a dedup
        # — the stored attribution/measurement stay valid; reopening here
        # would loop re-measurement/re-attribution on an unchanged render.
        if (
            _observation_class(finding.observation) != existing.observation_class
            and previous_version != finding.render_version
        ):
            # the finding CHANGED after repair: reopen with the new class,
            # bound to the LATEST finding id; the stale attribution and
            # measurement binding no longer describe this defect and are
            # cleared (never silently inherited).
            existing.observation_class = _observation_class(finding.observation)
            existing.finding_id = finding.finding_id
            existing.attribution = None
            existing.measurement_request_id = None
            existing.status = "open"
            return existing, "reopened"
        if resolved:
            existing.status = "resolved"
        return existing, "dedup"

E5_LEDGER_CLOSED_STATUSES = frozenset({"repaired", "resolved"})

def _open_ledger_finding_ids(entries: list["E5LedgerEntry"]) -> list[str]:
    """Open findings = the current finding id of every ledger entry whose
    status is not closed. Raw finding ids are NEVER scanned directly — a
    deduplicated re-observation of an already-repaired defect must not
    resurrect the defect as open under a new id."""
    return [entry.finding_id for entry in entries if entry.status not in E5_LEDGER_CLOSED_STATUSES]

def _record_ledger_attribution(
    ledger: "DefectLedger",
    finding: "DefectFinding",
    attribution: AttributionRecord,
    request_id: str,
) -> None:
    """Write an attribution back to the owning ledger entry (single source of
    defect state): the attribution record, the measurement request id, and
    the status. A confirmed measurement WITHIN tolerance (`no_defect`) closes
    the entry as resolved; every other attribution leaves it not-closed."""
    entry = ledger.entries.get(_ledger_key(finding))
    if entry is None:
        return
    entry.attribution = attribution
    entry.measurement_request_id = request_id
    entry.status = "resolved" if attribution.attribution == "no_defect" else "attributed"

class E5LaneASection(EvidenceModel):
    """Lane A reusable primitives for ONE section (provider-neutral; the
    fixed renderer interprets them). No target identifiers, no coordinates
    outside the declared rail width, no candidate text."""

    section_node_id: str
    heading_in_rail: bool = False
    rail_label_width_pt: float | None = Field(default=None, ge=20.0, le=400.0)
    rail_label_align: Literal["left", "right"] | None = None
    entry_meta_placement: Literal["title_row"] | None = None
    @model_validator(mode="after")
    def rail_fields_complete(self) -> "E5LaneASection":
        if self.heading_in_rail and not self.rail_label_width_pt:
            raise ValueError("heading_in_rail requires rail_label_width_pt")
        if not self.heading_in_rail and (self.rail_label_width_pt or self.rail_label_align):
            raise ValueError("rail width/align require heading_in_rail")
        return self

class LaneAStructureProposal(EvidenceModel):
    """The Lane A Builder's typed structure proposal (validated; unknown or
    invalid fields fail validation at construction — `extra="forbid"`)."""

    proposal_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    sections: list[E5LaneASection]
    agent: Literal["scripted", "llm"]

class E5LaneRecord(EvidenceModel):
    """One lane's resumable E5 state."""

    lane: Literal["a", "b"]
    representation: str
    render_versions: list[RenderVersion] = Field(default_factory=list)
    best_render_version: str | None = None
    # Local improvement state: a defect-level promoted version (promoted with
    # a known remaining red gate). Never labeled BEST; never the
    # hard-gate-valid best.
    best_defect_level_version: str | None = None
    active_render_version: str | None = None
    # QUARANTINED REPAIR BASE: the last repairable-but-not-passing candidate the
    # next Builder repairs FROM. It is never promoted, never best, and never the
    # lane's owner-facing output (see `_selected_lane_artifact`).
    quarantined_repair_base: str | None = None
    findings: list[DefectFinding] = Field(default_factory=list)
    measurement_results: list[MeasurementResult] = Field(default_factory=list)
    attributions: list[AttributionRecord] = Field(default_factory=list)
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)
    builder_candidates: list[dict[str, Any]] = Field(default_factory=list)
    attempted_strategies: list[str] = Field(default_factory=list)
    action_fingerprints: list[str] = Field(default_factory=list)
    ledger: list[E5LedgerEntry] = Field(default_factory=list)
    open_findings: list[str] = Field(default_factory=list)
    content_shape_probes_passed: bool = False
    pages_reviewed: list[int] = Field(default_factory=list)
    # The lane's stop state. `budget_exhausted` is claimed ONLY when a
    # configured ceiling was actually hit (model/tool requests, or the repair
    # round budget); `stalled_no_new_action` is the distinct state for "the
    # loop had nothing left to do" — every repairable action fingerprint was
    # already executed. Before 2026-09-23 both were collapsed into
    # `budget_exhausted`, so a stalled lane read as an exhausted one (live run
    # `e_pipeline_e5_20260923T095557Z` Lane B stopped after 11 of 400 requests).
    terminal_state: Literal[
        "ready_for_owner_review",
        "budget_exhausted",
        "stalled_no_new_action",
        "operational_abort",
    ] = "budget_exhausted"
    # WHICH ceiling or stall produced `terminal_state` (e.g.
    # "repair_round_budget", "model_request_budget", "reviewer_budget",
    # "attribution_budget", "builder_budget", "tool_call_budget",
    # "stalled_no_new_action", "no_new_render_no_pending_work"). Empty for a
    # lane that reached owner review.
    terminal_reason: str = ""
    budget_state: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)

class E5LoopRecord(EvidenceModel):
    """The E5 run record: per-lane state, frozen inputs, comparison metrics."""

    schema_version: Literal["pipeline-e-e5-state/1"] = E5_SCHEMA_VERSION
    target_id: str
    target_sha256: str
    lanes: dict[str, E5LaneRecord] = Field(default_factory=dict)
    budget_state: dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)

def _presentation_label_catalog(derived: dict[str, Any]) -> list[PresentationLabel]:
    """PROPOSE presentation labels from the measured sidebar-label evidence
    that already feeds the StructureDraft and both lanes' evidence package.

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
    return catalog

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
    return [
        known[label_id].model_copy(update={"status": "approved"})
        for label_id in approval.approved_label_ids
    ]

def _approved_presentation_labels(
    catalog: list[PresentationLabel],
) -> list[PresentationLabel]:
    """The ONLY renderable labels: the shell-recognized view.
    A proposed entry is deliberately excluded (non-renderable, and not
    privacy-excluded)."""
    return [label for label in catalog if label.status == "approved"]

def _heuristic_presentation_labels(catalog: list[PresentationLabel]) -> list[PresentationLabel]:
    """Recognize measured conventional headings using the product vocabulary.

    Exact aliases only: substring matches such as 'Skills Inc' must not turn
    target-person facts into reusable labels. Unknowns remain unrenderable.
    """
    from app.template_analysis.commercial.bridge import _section_source_hint

    return [
        label.model_copy(update={"status": "approved"})
        for label in catalog
        if (_section_source_hint(label.text)[0] != "additional_section"
            or " ".join(label.text.upper().split()) in {"PROJECTS", "ADDITIONAL SECTIONS"})
    ]

def _e5_label_semantics(
    gate: dict[str, Any], approved: list[PresentationLabel]
) -> dict[str, Any]:
    """Whether this lane's privacy gate excluded EXACTLY the owner-approved
    label set. Both lanes run the SAME common gate with the SAME approved set,
    so a subset (e.g. Lane A's plan-derived labels) is NOT symmetric — the two
    lanes' privacy decisions must be identical to be comparable."""
    from app.pipeline.quality import _norm

    excluded = {_norm(item) for item in (gate.get("excluded_labels") or [])}
    approved_texts = {_norm(label.text) for label in approved}
    return {
        "approved_label_ids": [label.label_id for label in approved],
        "symmetric": excluded == approved_texts,
        "excluded_labels": sorted(excluded),
        "excluded_outside_approved": sorted(excluded - approved_texts),
        "approved_texts_never_excluded": sorted(approved_texts - excluded),
    }

def _presentation_label_listing(
    catalog: list[PresentationLabel],
    lane_references: dict[str, list[str]],
    approved_label_ids: list[str] | None = None,
    *,
    target_sha256: str | None = None,
    catalog_sha256: str | None = None,
    approval_provided: bool | None = None,
    approval_validated: bool | None = None,
    recognition_method: str = "explicit_owner_input",
) -> dict[str, Any]:
    """The owner-visible label listing (label_id / text / kind / status /
    evidence / which lanes referenced it). Status comes from the shell-recognized
    ``approved_label_ids``; shell catalog entries
    are always proposed. `proposed` entries are listed for review but are NOT
    renderable. Audit material only — it does NOT mean the owner accepted
    T-v1."""
    approved: set[str] = set(approved_label_ids or [])
    return {
        "schema_version": "e5-presentation-labels/1",
        "note": (
            "Measured labels are renderable and privacy-excluded only after "
            "recognition by the recorded method. Conventional exact aliases "
            "are recognized automatically; unknown labels remain proposed and "
            "unrenderable. Legacy explicit approval remains bound to "
            "target_sha256 and catalog_sha256. "
            "Recognition is not human output acceptance or a lane decision."
        ),
        "recognition_method": recognition_method,
        "target_sha256": target_sha256,
        "catalog_sha256": catalog_sha256,
        "approval_provided": approval_provided,
        "approval_validated": approval_validated,
        "approved_label_ids": [
            label.label_id for label in catalog if label.label_id in approved
        ],
        "proposed_label_ids": [
            label.label_id for label in catalog if label.label_id not in approved
        ],
        "labels": [
            {
                "label_id": label.label_id,
                "text": label.text,
                "kind": label.kind,
                "status": (
                    "approved" if label.label_id in approved else label.status
                ),
                "evidence_ids": label.evidence_ids,
                "referenced_by_lanes": sorted(
                    lane_id
                    for lane_id, refs in lane_references.items()
                    if label.label_id in set(refs)
                ),
            }
            for label in catalog
        ],
    }

def _candidate_compatible_draft(
    draft: TargetStructureDraft, review_scope: dict[str, Any]
) -> dict[str, Any]:
    excluded = {
        str(label).casefold()
        for label in review_scope.get("target_labels_without_candidate_content", [])
    }
    def compatible(statement: str) -> bool:
        text = statement.casefold()
        if any(label in text for label in excluded):
            return False
        if review_scope.get("skills_only_skill_group") and any(
            phrase in text for phrase in ("dot rating", "filled/hollow dot", "languages th")
        ):
            return False
        if "contact info" in text and "table" in text and any(
            word in text for word in ("label", " th ", " td ")
        ):
            return False
        if "header block" in text and " above " in text:
            return False
        return True

    structure = [
        claim.model_dump(mode="json") for claim in draft.structure
        if compatible(claim.statement)
    ]
    return {
        "schema_version": draft.schema_version,
        "target_id": draft.target_id,
        "investigator": draft.investigator,
        "structure": structure,
        "unresolved": [],
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
    return {
        "selected_region": finding.region,
        "affected_page": finding.page,
        "allowed_changes": (
            ["bind the specified missing leaves through the correct existing slot token"]
            if accounting else [f"repair only {finding.region}:{finding.suspected_dimension}"]
        ),
        "preserve_unless_measured": [
            "typography", "colors", "spacing", "header",
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
    missing_leaf_ids: list[str] | None = None,
    compact: bool = False,
) -> dict[str, Any]:
    """One representation-neutral Builder evidence package.

    Both lanes receive this exact data shape and the same target images. Only
    their output schema/instructions differ. Repair calls additionally receive
    the current render images outside this JSON package and the exact validated
    representation that produced them. `presentation_labels` is the SAME
    shell-owned catalog for both lanes: the only visible fixed text a template
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
    if not compact:
        package["structure_draft"] = (
            _candidate_compatible_draft(draft, candidate_review_scope or {})
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
                    "one atomic slot; do not simulate separate first/last-name typography"
                )
            },
        }
    elif compact and findings and candidate is not None:
        package["region_repair_context"] = _lane_b_repair_boundary(
            findings[0], candidate=candidate, missing_leaf_ids=missing_leaf_ids,
            body_color_context=body_color_context,
        )
    return package

def _e5_gate_classification(
    lane: str,
    gates: dict[str, Any],
    *,
    privacy_labels_symmetric: bool,
    gate_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify only what the implemented gates can establish.

    ``gates`` is the version's BOOLEAN gate map; ``gate_details`` is that
    version's recorded per-gate evidence (optional: without it the Lane A
    classification falls back to "no per-property record", never to a claim).
    """
    result: dict[str, Any] = {}
    if lane == "a" and not gates.get("content_shapes_match_evidence", True):
        result["representation_ceiling"] = "unverified"
        # The reason must state what the shell's OWN gate record failed on.
        # Before 2026-09-23 it asserted a rail-geometry gap the record never
        # named: in live run `e_pipeline_e5_20260923T095557Z` the failing
        # property was `section.04.entry_typography` ("measured entry
        # title/meta/detail typography tiers not consumed by the renderer"),
        # i.e. the classification contradicted its own evidence.
        gaps = _e5_content_shape_capability_gaps(
            (gate_details or {}).get("content_shapes_match_evidence") or {}
        )
        if gaps:
            result["reason"] = (
                "content_shape_verification failed on this lane's declared "
                "content shapes: the shell's own renderer does not consume "
                + ", ".join(gaps)
                + " (recorded capability gaps). No agent — Builder included — "
                "can change a property the fixed renderer does not consume, so "
                "this failure is ownerless by design: it is reported here and "
                "never routed to a repair owner."
            )
        else:
            result["reason"] = (
                "no per-property record was supplied for the content-shape "
                "failure, so the shell cannot claim an owning layer; the "
                "historical ceiling note (rail_heading, rail_label_width_pt, "
                "rail_label_align, rendered rail geometry) is not verified here"
            )
    if lane == "b" and not gates.get("no_target_candidate_facts", True):
        result["privacy_failure"] = (
            "unresolved" if privacy_labels_symmetric else "gate_false_positive_or_boundary_unresolved"
        )
        result["privacy_labels_symmetric"] = privacy_labels_symmetric
    return result

def _e5_hard_gate_record(
    render_version: str,
    gates: dict[str, bool],
    details: dict[str, Any],
    *,
    blocking_gates: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Version-bound E5 gate summary.

    E5 is an exploratory comparison. Candidate truth and privacy block owner
    review; template safety is enforced before rendering. Other checks remain
    visible as diagnostics. The optional argument preserves the legacy
    all-blocking behavior for callers that do not opt into the E5 policy.
    """
    blocking = blocking_gates or tuple(gates)
    blocking_failures = [name for name in blocking if gates.get(name) is not True]
    diagnostics = details.get("diagnostics") or {}
    return {
        "render_version": render_version,
        "passed": not blocking_failures,
        "blocking_gates": list(blocking),
        "blocking_failures": blocking_failures,
        "diagnostic_warnings": [
            *(
                name for name, passed in gates.items()
                if not passed and name not in blocking
            ),
            *(
                name for name, detail in diagnostics.items()
                if detail.get("passed") is not True
            ),
        ],
        "gates": gates,
        "details": details,
    }

# Presentation consumption remains mandatory; repeat-render identity is diagnostic.
LANE_A_OUTPUT_GATES = (
    "candidate_content_accounting", "no_target_candidate_facts", "content_gate",
    "section_order_matches_state", "no_blank_page", "content_shapes_match_evidence",
)

def _e5_blocking_gates(lane: str) -> tuple[str, ...]:
    """The experiment's minimal safety boundary; all other gates are warnings."""
    del lane
    return ("candidate_content_accounting", "no_target_candidate_facts")

def _e5_blocking_gates_passed(lane: str, gates: dict[str, bool]) -> bool:
    return all(gates.get(name) is True for name in _e5_blocking_gates(lane))

def _e5_builder_candidate_record(
    *,
    lane: str,
    attempt: int,
    stage: str,
    input_render_version: str | None,
    candidate_output: Any,
    validation: dict[str, Any],
    outcome: str,
    reason: str,
    attribution: AttributionRecord | None = None,
    action_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Local audit record for one parsed typed Builder output."""
    return {
        "schema_version": "e5-builder-candidate-audit/1",
        "lane": lane,
        "builder_attempt": attempt,
        "stage": stage,
        "input_render_version": input_render_version,
        "typed_candidate": candidate_output.model_dump(mode="json"),
        "validation": validation,
        "candidate_render_version": None,
        "outcome": outcome,
        "reason": reason,
        "trigger_attribution": attribution.model_dump(mode="json") if attribution else None,
        "action_fingerprint": action_fingerprint,
        "artifact": f"builder_candidate_{attempt:02d}.json",
    }

E5_MAX_ROUND_ATTRIBUTION_FINDINGS = 3

E5_NON_QUARANTINABLE_GATES = ("no_target_candidate_facts",)

def _e5_review_this_round(reviewed_render_fingerprints: set[str], pdf_sha256: str) -> bool:
    """Review scheduling gate (2026-09-22 owner correction): the fingerprint
    is the FINAL PDF's sha256, not the version id. One fingerprint is FULLY
    reviewed at most once — no new render -> NO review this round; a promoted
    render with a new PDF hash is reviewed once (scoped to changed regions by
    the round scope). Both lanes run the SAME gate."""
    return pdf_sha256 not in reviewed_render_fingerprints

def _e5_content_shape_capability_gaps(details: dict[str, Any]) -> list[str]:
    """The capability-gap property paths of ONE Lane A
    `content_shapes_match_evidence` record.

    This gate fails EXACTLY when a declared-shape property is not both measured
    and consumed by the renderer: `c2_renderer.content_shape_verification`
    computes `capability_gap = None if not required or (measured and consumed)`
    and `consistent = all(capability_gap is None)`, with
    `passed = all(row["consistent"])`. So Lane A's content-shape failure IS the
    shell's representation capability gap: there is no second, repairable defect
    hiding in the same record, and no agent (Builder included) can change a
    property the fixed renderer does not consume. The walker therefore reports
    the gaps only, and an unreadable record reports nothing (fail closed)."""
    gaps: list[str] = []
    for row in details.get("rows") or []:
        if not isinstance(row, dict) or row.get("consistent"):
            continue
        section = str(row.get("section") or "")
        for prop in row.get("properties") or []:
            if not isinstance(prop, dict):
                continue
            if (
                str(prop.get("capability_gap") or "").strip()
                or prop.get("renderer_consumes") is not True
            ):
                gaps.append(f"{section}.{prop.get('property')}")
    return gaps

def _candidate_role_page(candidate: Any, role: str, current_pdf: Path) -> int:
    """Locate a role from its existing rendered leaves/heading; no new metric."""
    import pdfplumber

    texts = [
        leaf.text for leaf in candidate.leaves
        if leaf.source == role and leaf.text
    ]
    texts.extend(
        section.heading for section in candidate.sections
        if section.source == role and section.heading
    )
    with pdfplumber.open(current_pdf) as document:
        for page_number, page in enumerate(document.pages, 1):
            page_text = re.sub(r"\s+", " ", page.extract_text() or "").casefold()
            if any(re.sub(r"\s+", " ", text).casefold() in page_text for text in texts):
                return page_number
    return 1

def _e5_gate_repair_items(
    lane: str,
    target_id: str,
    render_version: str,
    current_gates: dict[str, Any],
    *,
    candidate: Any | None = None,
    current_pdf: Path | None = None,
) -> list[tuple[DefectFinding, MeasurementResult, MeasurementRequest, AttributionRecord]]:
    """Deterministic repair input for exact Lane B slot-accounting failures.

    PDF presence is observational in this route-finding experiment because
    letter spacing and multi-column reading order make flattened-text matching
    unreliable. Lane A is NEVER converted: its
    `content_shapes_match_evidence` failure IS a recorded capability gap (see
    `_e5_content_shape_capability_gaps`), so a builder input would hand the
    Builder a property the fixed renderer does not consume — an unreachable
    repair. That ownerless-by-design state is reported through
    `_e5_gate_classification`, never fabricated into an owning layer. The Lane
    B accounting evidence is reused verbatim with an explicit
    `AttributionRecord` (template_compilation / confirmed / builder) — no second
    defect, attribution, or state model. Privacy failures and observational
    quality warnings are never converted automatically."""
    if lane != "b":
        return []
    items: list[tuple[DefectFinding, MeasurementResult, MeasurementRequest, AttributionRecord]] = []
    # PDF text presence is deliberately observational in E5: letter spacing
    # and multi-column reading order can split text that is visibly present.
    # Only the exact authored-slot accounting may create an automatic repair.
    for gate_name, missing_key in (("candidate_content_accounting", "missing_leaves"),):
        details = current_gates.get(gate_name) or {}
        missing = [str(item) for item in (details.get(missing_key) or [])]
        if details.get("passed") is not False or not missing:
            continue
        leaf_by_id = {
            leaf.leaf_id: leaf for leaf in getattr(candidate, "leaves", [])
        }
        roles = {
            leaf_by_id[leaf_id].source
            for leaf_id in missing if leaf_id in leaf_by_id and leaf_by_id[leaf_id].source
        }
        region = next(iter(roles)) if len(roles) == 1 else gate_name
        affected_page = (
            _candidate_role_page(candidate, region, current_pdf)
            if candidate is not None and current_pdf is not None and len(roles) == 1
            else 1
        )
        request_id = f"gate-{gate_name}-{render_version}"
        request = MeasurementRequest(
            request_id=request_id,
            metric="role_gap",
            page=affected_page,
            intent=(
                f"verbatim presence of the candidate leaves recorded missing by "
                f"the {gate_name} gate in the final PDF text"
            ),
        )
        finding = DefectFinding(
            finding_id=f"gate-{gate_name}-{render_version}",
            target_version=target_id,
            render_version=render_version,
            page=affected_page,
            region=region,
            observation=(
                f"{gate_name} failed on this render: {len(missing)} candidate "
                f"leaves missing from the final PDF ({', '.join(missing[:6])})"
            ),
            suspected_dimension="hard_gate_content",
            requested_measurement=request,
            severity="high",
            confidence=1.0,
            reviewer="scripted",
        )
        result = MeasurementResult(
            request_id=request_id,
            status="confirmed",
            target_value_pt=0.0,
            current_value_pt=float(len(missing)),
            delta_pt=float(len(missing)),
            method="candidate_content_accounting_missing/1",
            warnings=[
                "repair input derived from the shell's OWN gate record, not from "
                "a reviewer claim; the same gate is re-measured on any candidate",
            ],
        )
        attribution = AttributionRecord(
            finding_id=finding.finding_id,
            render_version=render_version,
            measurement_request_id=request_id,
            attribution="template_compilation",
            hypothesis_status="confirmed",
            repair_owner="builder",
            evidence=[request_id],
            reason=(
                f"the {gate_name} hard gate failed with recorded missing leaves; "
                "the authored template owns which leaves render, so the repair "
                "input is builder-owned WITHOUT live attribution"
            ),
        )
        items.append((finding, result, request, attribution))
    return items

def _e5_quarantine_eligibility(
    candidate_version: RenderVersion,
    candidate_gates: dict[str, Any],
    *,
    representation: Any | None,
    binding_lineage: CandidateBindingLineage | None,
    candidate_audit: dict[str, Any] | None,
) -> tuple[bool, str]:
    """MAY this hard-gate-red candidate become the NEXT repair base?

    Only a *repairable but not passing* candidate may: every safety gate green,
    its own representation identity verifiable, and its own leaf lineage free of
    any deterministic candidate-binding contradiction (rewritten or
    unknown-origin candidate facts fail closed). Nothing is relaxed here — a
    quarantined candidate is never promoted, never best, and never the lane's
    owner-facing output; it is only the representation the next Builder edits."""
    unsafe = [
        gate for gate in E5_NON_QUARANTINABLE_GATES
        if candidate_gates.get(gate) is not True
    ]
    if unsafe:
        return False, f"non-quarantinable safety gate(s) failed: {unsafe}"
    if representation is None:
        return False, "the candidate's representation identity is not recorded for this version"
    if (
        not candidate_audit
        or candidate_audit.get("candidate_render_version") != candidate_version.version_id
    ):
        return False, (
            "the builder-candidate audit does not bind this render version to "
            "its validated input"
        )
    if binding_lineage is None or binding_lineage.render_version != candidate_version.version_id:
        return False, "no candidate-leaf lineage audit is bound to this render version"
    if binding_lineage.proofs:
        return False, (
            "deterministic candidate-binding lineage contradiction(s) recorded: "
            + ", ".join(f"{proof.kind}:{proof.leaf_id}" for proof in binding_lineage.proofs)
        )
    return True, (
        "repairable-but-not-passing candidate: safety gates green, representation "
        "identity verified, candidate leaf lineage clean"
    )

def _e5_select_round_work(
    actionable: list[DefectFinding],
    gate_findings: list[DefectFinding],
    deferred_candidates: list[DefectFinding],
    *,
    limit: int = E5_MAX_ROUND_ATTRIBUTION_FINDINGS,
) -> tuple[list[DefectFinding], list[DefectFinding]]:
    """Bounded per-round scheduling (repair starvation fix):

    1. hard-gate-derived repair inputs first (they are pre-attributed; the
       run loop normally consumes them via the Builder-first scan and passes
       an empty list here);
    2. this round's NEW findings in review order;
    3. previously deferred open findings (oldest first).

    At most ``limit`` findings enter this round's attribution backlog; the
    rest stay in the ledger as `deferred` (open, never lost, re-eligible).
    The SAME function is used by BOTH lanes."""
    ordered: list[DefectFinding] = []
    seen: set[str] = set()
    for finding in [*gate_findings, *actionable, *deferred_candidates]:
        if finding.finding_id in seen:
            continue
        seen.add(finding.finding_id)
        ordered.append(finding)
    return ordered[:limit], ordered[limit:]

def _next_e5_repair_finding(
    actionable: list[DefectFinding],
    measured: dict[str, tuple[MeasurementResult, MeasurementRequest]],
    attributions: list[AttributionRecord],
    fingerprints: list[str],
    *,
    layout_trial_regions: set[str] | None = None,
) -> tuple[
    tuple[DefectFinding, MeasurementResult, MeasurementRequest, AttributionRecord, str] | None,
    bool,
]:
    """Prefer measured Builder repairs; explicitly eligible A regions may try hypotheses.

    Uncertain trials retain their original attribution and cannot establish improvement.
    The boolean says repairable findings existed but every fingerprint was
    already executed, so the lane must stop instead of burning another round.
    """
    repairable_seen = False
    trial = None
    for finding in actionable:
        result, bound = measured[finding.finding_id]
        if result.status == "falsified" or (
            result.status == "confirmed" and abs(result.delta_pt or 0.0) <= E2_IMPROVEMENT_TOLERANCE_PT
        ):
            continue
        attribution = next(
            (
                item
                for item in reversed(attributions)
                if item.finding_id == finding.finding_id
                and item.render_version == finding.render_version
                and item.measurement_request_id == bound.request_id
            ),
            None,
        )
        if attribution is None:
            continue
        proven_builder = (
            result.status == "confirmed"
            and attribution.hypothesis_status == "confirmed"
            and attribution.repair_owner == "builder"
        )
        allow_trial = (
            finding.region in (layout_trial_regions or set())
            and not (attribution.attribution == "candidate_binding" and attribution.hypothesis_status == "confirmed")
        )
        if not proven_builder and not allow_trial:
            continue
        repairable_seen = True
        # The repair BASE version is part of the action identity (owner work
        # order 2026-09-22): the identical action on the SAME input version is
        # still deduplicated, while continuing the remaining defects on a NEW
        # quarantined repair version is never mistaken for a repeat of the old
        # action. The base comes from the finding's own bound version, so a
        # reviewer finding (active version) and a hard-gate input (quarantined
        # repair base) can never collide.
        fingerprint = (
            f"{finding.render_version}:{finding.region}:"
            f"{finding.suspected_dimension}:{round(result.delta_pt, 3) if result.delta_pt is not None else result.status}"
        )
        if fingerprint not in fingerprints:
            item = (finding, result, bound, attribution, fingerprint)
            if proven_builder:
                return item, False
            if trial is None:
                trial = item
    if trial is not None:
        return trial, False
    return None, repairable_seen

def _e5_attribution_batches(
    actionable: list[DefectFinding],
    measured: dict[str, tuple[MeasurementResult, MeasurementRequest]],
) -> list[list[DefectFinding]]:
    """Batch measurable or unbound findings; known unsupported metrics stay warnings."""
    groups: dict[str, list[DefectFinding]] = {}
    for finding in actionable:
        result, _ = measured[finding.finding_id]
        if result.status == "not_measurable":
            continue
        if result.status == "confirmed" and abs(result.delta_pt or 0.0) <= E2_IMPROVEMENT_TOLERANCE_PT:
            continue
        groups.setdefault(finding.region, []).append(finding)
    return [part[i : i + 3] for part in groups.values() for i in range(0, len(part), 3)]

def _e5_default_attribution(
    finding: DefectFinding,
    result: MeasurementResult,
    request: MeasurementRequest,
) -> AttributionRecord:
    """Explicit non-live/fallback state without inventing a causal owner."""
    if result.status == "confirmed" and result.delta_pt is not None:
        if abs(result.delta_pt) <= E2_IMPROVEMENT_TOLERANCE_PT:
            return AttributionRecord(
                finding_id=finding.finding_id,
                render_version=finding.render_version,
                measurement_request_id=request.request_id,
                attribution="no_defect",
                hypothesis_status="rejected",
                repair_owner="none",
                evidence=[result.request_id],
                reason="measured final-PDF property matches the target within the documented tolerance",
            )
        return AttributionRecord(
            finding_id=finding.finding_id,
            render_version=finding.render_version,
            measurement_request_id=request.request_id,
            attribution="unresolved",
            hypothesis_status="unresolved",
            repair_owner="reviewer",
            evidence=[result.request_id],
            reason=(
                "the measured final-PDF role gap differs beyond tolerance, but the "
                "measurement alone does not establish the owning layer"
            ),
        )
    return AttributionRecord(
        finding_id=finding.finding_id,
        render_version=finding.render_version,
        measurement_request_id=request.request_id,
        attribution="not_measurable",
        hypothesis_status="unresolved",
        repair_owner="reviewer" if result.status in {"ambiguous", "not_measurable"} else "binding",
        evidence=[result.request_id],
        reason=(
            (result.reason + "; " if result.reason else "") +
            "the semantic measurement intent did not bind to final-PDF objects this "
            "round; the finding stays open for re-verification"
        ),
    )

def _e5_lane_b_attribution(
    finding: DefectFinding,
    result: MeasurementResult,
    request: MeasurementRequest,
) -> AttributionRecord:
    """Lane B owns its whole presentation template; a confirmed visual
    mismatch can go straight back to its Builder."""
    if (
        result.status == "confirmed"
        and result.delta_pt is not None
        and abs(result.delta_pt) > E2_IMPROVEMENT_TOLERANCE_PT
    ):
        return AttributionRecord(
            finding_id=finding.finding_id,
            render_version=finding.render_version,
            measurement_request_id=request.request_id,
            attribution="template_compilation",
            hypothesis_status="confirmed",
            repair_owner="builder",
            evidence=[result.request_id],
            reason="Lane B's Builder owns the complete authored presentation template.",
        )
    return _e5_default_attribution(finding, result, request)

class LiveAttributionBindingError(ValueError):
    """The live attribution batch did not return exactly ONE hypothesis per
    batch finding id. The shell fails closed: no positional fallback, no
    guessing, no auto-fill."""

def _bind_live_attribution_batch(
    findings: list[DefectFinding],
    hypotheses: list[LiveAttributionHypothesis],
) -> list[tuple[DefectFinding, LiveAttributionHypothesis]]:
    """Bind each live hypothesis to its finding BY ``finding_id``.

    Fail closed on an empty/missing/duplicate/foreign finding_id, or on a
    batch finding with no returned hypothesis — a batch whose counts merely
    look equal is rejected too, because the identity SET must be equal.
    Reordering the same identity set is valid and changes nothing."""
    expected = [finding.finding_id for finding in findings]
    if len(set(expected)) != len(expected):
        raise LiveAttributionBindingError(f"batch findings carry duplicate ids: {expected}")
    known = set(expected)
    by_id: dict[str, LiveAttributionHypothesis] = {}
    for hypothesis in hypotheses:
        finding_id = (hypothesis.finding_id or "").strip()
        if not finding_id:
            raise LiveAttributionBindingError("hypothesis without a finding_id")
        if finding_id not in known:
            raise LiveAttributionBindingError(
                f"hypothesis for finding id {finding_id!r} outside the batch {expected}"
            )
        if finding_id in by_id:
            raise LiveAttributionBindingError(
                f"duplicate hypothesis for finding id {finding_id!r}"
            )
        by_id[finding_id] = hypothesis
    unreturned = [finding_id for finding_id in expected if finding_id not in by_id]
    if unreturned:
        raise LiveAttributionBindingError(
            f"no hypothesis returned for finding id(s) {unreturned}"
        )
    return [(finding, by_id[finding.finding_id]) for finding in findings]

def _e5_findings_needing_fallback_attribution(
    actionable: list[DefectFinding],
    measured: dict[str, tuple[MeasurementResult, MeasurementRequest]],
    attributions: list[AttributionRecord],
) -> list[DefectFinding]:
    """Findings with no attribution for the CURRENT identity
    ``finding_id + render_version + measurement_request_id``. An older
    measurement's attribution NEVER satisfies a new measurement."""
    attributed_keys = {
        (item.finding_id, item.render_version, item.measurement_request_id)
        for item in attributions
    }
    return [
        finding
        for finding in actionable
        if (
            finding.finding_id,
            finding.render_version,
            measured[finding.finding_id][1].request_id,
        )
        not in attributed_keys
    ]

def _builder_reserve_intact(
    budget: RunBudget, reserve_requests: int = E5_BUILDER_RESERVE_REQUESTS,
) -> bool:
    """Phase 0 Builder budget reservation: live attribution may only run when
    the lane budget still covers the Builder reserve (diagnosis must not eat
    the budget before the Builder can act)."""
    return budget.remaining_model_requests() > reserve_requests

def _best_valid_version_id(
    versions: list[RenderVersion], excluded: set[str] | None = None
) -> str | None:
    """Explicit best-valid selection rule (never 'first promoted' and never
    'first hard-gate-valid', and never `versions[-1]`):

    1. the LATEST HARD-GATE-VALID version that was never explicitly ROLLED
       BACK. A defect-level promotion (promoted with a known remaining red
       gate, e.g. content_shapes_match_evidence) is NOT hard-gate-valid and
       must never masquerade as the best-valid render — it is recorded as
       the active/defect-level state instead (see
       `_best_defect_level_version_id`);
    2. otherwise None (no best-valid render exists)."""
    excluded = excluded or set()
    for version in reversed(versions):
        if version.hard_gates_passed and version.version_id not in excluded:
            return version.version_id
    return None

def _best_defect_level_version_id(
    versions: list[RenderVersion], excluded: set[str] | None = None
) -> str | None:
    """The latest defect-level promoted version (promoted=True with a known
    remaining red hard gate) that was never rolled back. This is LOCAL
    improvement state — the active render may rest on it — but it is never
    the hard-gate-valid best and is never labeled BEST."""
    excluded = excluded or set()
    for version in reversed(versions):
        if version.promoted and not version.hard_gates_passed and version.version_id not in excluded:
            return version.version_id
    return None

def _bind_role_gap_anchors(
    request: MeasurementRequest,
    *,
    target_pdf: Path,
    current_pdf: Path,
    region_span: tuple[float, float] | None,
    render_leaf_texts: list[str],
    render_non_head_texts: list[str] | None = None,
) -> MeasurementRequest | MeasurementResult:
    """Phase 0 measurement binding: resolve a SEMANTIC measurement intent
    into actual final-PDF objects (exact line-prefix anchors on BOTH sides).
    The render side binds against the render's own candidate entry-head
    texts; the target side binds against the target's measured content rows
    within the region span. A Reviewer OCR mismatch never becomes an
    automatic measurement_failure: an unresolvable intent returns
    `evidence_missing` for re-verification, with the binding method recorded."""
    intent = (request.intent or "").casefold()
    if not (
        any(word in intent for word in ("gap", "distance", "spacing"))
        and any(word in intent for word in ("entry", "role", "job"))
        and not any(word in intent for word in (
            "contact", "typography", "font", "vertical rule",
        ))
    ):
        return MeasurementResult(
            request_id=request.request_id,
            status="not_measurable",
            reason="the current deterministic metric measures entry-head spacing only",
            method="role_gap_binding/3",
            warnings=["a different visual dimension requires its own deterministic metric"],
        )
    if region_span is None:
        return MeasurementResult(
            request_id=request.request_id, status="evidence_missing",
            reason="entry-head spacing requires a measured owning-section boundary",
            method="role_gap_binding/3",
        )
    render_rows = _pdf_line_rows(current_pdf, request.page)

    def _norm_row(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()

    prefixes = [
        _norm_row(text)[:12]
        for text in render_leaf_texts
        if text and len(text.strip()) >= 6
    ]
    render_anchors: list[str] = []
    render_head_rows: list[dict[str, Any]] = []
    for row in render_rows:
        row_text = _norm_row(row["text"])
        for prefix in prefixes:
            if row_text.startswith(prefix) and prefix not in render_anchors:
                render_anchors.append(prefix)
                render_head_rows.append(row)
                break
        if len(render_anchors) == 2:
            break
    if len(render_anchors) < 2:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            reason=(
                "the semantic intent could not be bound to two render entry-head "
                "rows in the final PDF (binding, not OCR, decides)"
            ),
            method="role_gap_binding/3",
            warnings=["binding resolves the Reviewer's semantic intent against actual PDF rows"],
        )
    target_rows = _pdf_line_rows(target_pdf, request.page)
    if region_span is not None:
        span_top, span_bottom = region_span
        target_rows = [
            row for row in target_rows if span_top < row["top"] < span_bottom
        ]
    import pdfplumber

    def row_styles(path: Path) -> dict[float, tuple[float, bool, Any]]:
        with pdfplumber.open(path) as document:
            words = document.pages[request.page - 1].extract_words(
                extra_attrs=["fontname", "size", "non_stroking_color"])
        styles = {}
        for word in sorted(words, key=lambda item: item["x0"]):
            styles.setdefault(round(float(word["top"]), 1), (
                float(word["size"]), "bold" in word["fontname"].casefold(),
                word["non_stroking_color"],
            ))
        return styles

    render_styles, target_styles = row_styles(current_pdf), row_styles(target_pdf)
    anchor_styles = [render_styles.get(row["top"]) for row in render_head_rows]

    def same_role(style: tuple | None, reference: tuple | None) -> bool:
        return bool(style and reference and abs(style[0] - reference[0]) < .5
                    and style[1:] == reference[1:])

    other_prefixes = [_norm_row(text)[:12] for text in render_non_head_texts or [] if text.strip()]
    if any(any(_norm_row(row["text"]).startswith(prefix) for prefix in other_prefixes)
           and any(same_role(render_styles.get(row["top"]), style) for style in anchor_styles)
           for row in render_rows):
        return MeasurementResult(
            request_id=request.request_id, status="ambiguous",
            reason="entry-head typography also belongs to other declared entry fields",
            method="role_gap_binding/3",
        )

    # Bind the same measured typographic role, rather than adjacent title,
    # company or date rows. Unsupported style differences stay unmeasured.
    head_rows = [row for row in target_rows
                 if not row["text"].startswith(("•", "-", "–"))
                 and any(character.isalpha() for character in row["text"])
                 and all(same_role(target_styles.get(row["top"]), style)
                         for style in anchor_styles)]
    if len(head_rows) < 2:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            reason="the region's target content rows could not yield two head anchors",
            method="role_gap_binding/3",
            warnings=["binding resolves the Reviewer's semantic intent against actual PDF rows"],
        )
    if len(head_rows) > 2:
        return MeasurementResult(
            request_id=request.request_id, status="ambiguous",
            reason="matching typography does not identify a unique pair of target entry-head rows",
            method="role_gap_binding/3",
            target_evidence=[{"page": request.page, "top": row["top"], "text": row["text"]} for row in head_rows],
        )
    return request.model_copy(
        update={
            "from_text": _norm_row(head_rows[0]["text"])[:16],
            "to_text": _norm_row(head_rows[1]["text"])[:16],
            "render_from_text": render_anchors[0],
            "render_to_text": render_anchors[1],
        }
    )

def _header_vertical_rule_evidence(
    pdf: Path, page_number: int, *, name_text: str | None = None
) -> dict[str, Any] | None:
    """Measure header rules against the exact candidate-name row when known.

    Target PDFs have no candidate lineage, so their bounded fallback is the
    single tallest header line. This stays one metric, not a visual framework.
    """
    import pdfplumber

    page_width, page_height = _page_pt_size(pdf, page_number)
    lines = [
        line for line in _pdf_lines_and_marks(pdf)[0]
        if line["page"] == page_number and line["top"] < page_height * 0.25
    ]
    if not lines:
        return None
    name_lines: list[dict[str, Any]] = []
    exact_name_match = False
    if name_text:
        expected = re.sub(r"\s+", " ", name_text).strip().casefold()
        for start in range(len(lines)):
            selected: list[dict[str, Any]] = []
            for line in lines[start:start + 3]:
                selected.append(line)
                observed = re.sub(
                    r"\s+", " ", " ".join(item["text"] for item in selected)
                ).strip().casefold()
                if observed == expected:
                    name_lines = selected
                    exact_name_match = True
                    break
                if not expected.startswith(observed + " "):
                    break
            if name_lines:
                break
    if not name_lines:
        max_height = max(float(line["bottom"]) - float(line["top"]) for line in lines)
        name_lines = [
            line for line in lines
            if math.isclose(
                float(line["bottom"]) - float(line["top"]),
                max_height,
                abs_tol=0.25,
            )
        ]
    name = {
        "x0": min(float(line["x0"]) for line in name_lines),
        "x1": max(float(line["x1"]) for line in name_lines),
        "top": min(float(line["top"]) for line in name_lines),
        "bottom": max(float(line["bottom"]) for line in name_lines),
    }
    header_top = max(0.0, name["top"] - 80.0)
    header_bottom = min(page_height, name["bottom"] + 80.0)
    rules: list[dict[str, Any]] = []
    with pdfplumber.open(pdf) as document:
        page = document.pages[page_number - 1]
        objects = [*page.rects, *page.curves, *(getattr(page, "lines", None) or [])]
        seen: set[tuple[float, float, float]] = set()
        for mark in objects:
            x0, x1 = float(mark["x0"]), float(mark["x1"])
            top, bottom = float(mark["top"]), float(mark["bottom"])
            width, height = abs(x1 - x0), abs(bottom - top)
            center_x = (x0 + x1) / 2
            if width > 4.0 or height < 8.0 or bottom < header_top or top > header_bottom:
                continue
            horizontal_gap = max(name["x0"] - x1, x0 - name["x1"], 0.0)
            if horizontal_gap > 80.0 or center_x < 12.0 or center_x > page_width - 12.0:
                continue
            key = (round(center_x, 2), round(top, 2), round(bottom, 2))
            if key in seen:
                continue
            seen.add(key)
            dx = horizontal_gap
            dy = max(name["top"] - bottom, top - name["bottom"], 0.0)
            overlap = max(0.0, min(bottom, name["bottom"]) - max(top, name["top"]))
            rules.append({
                "x": round(center_x, 3),
                "top": round(top, 3),
                "bottom": round(bottom, 3),
                "height": round(height, 3),
                "relative_x_to_name_left": round(center_x - name["x0"], 3),
                "relative_top_to_name_top": round(top - name["top"], 3),
                "relative_bottom_to_name_top": round(bottom - name["top"], 3),
                "nearest_distance_to_name_bbox": round(math.hypot(dx, dy), 3),
                "name_vertical_overlap_pt": round(overlap, 3),
            })
    rules.sort(key=lambda rule: (rule["relative_top_to_name_top"], rule["relative_x_to_name_left"]))
    return {
        "page": page_number,
        "name_anchor_source": (
            "candidate_name_text" if exact_name_match else "tallest_header_line"
        ),
        "name_region": {key: round(value, 3) for key, value in name.items()},
        "vertical_rule_count": len(rules),
        "vertical_rules": rules,
    }

def _header_vertical_rule_target_context(pdf: Path) -> dict[str, Any] | None:
    """Small Builder context for the already-supported header-rule metric."""
    evidence = _header_vertical_rule_evidence(pdf, 1)
    if evidence is None:
        return None
    rules = evidence["vertical_rules"]
    return {
        "scope": "header_name",
        "target_final_pdf_evidence": evidence,
        "preserve_separate_name_gap": (
            len(rules) == 2
            and all(rule["name_vertical_overlap_pt"] == 0 for rule in rules)
        ),
        "forbidden_interpretation": "continuous_or_page_spanning_vertical_rail",
    }

def _target_body_text_color_context(pdf: Path) -> dict[str, Any] | None:
    """Measured dominant text fill by visible-character volume."""
    import pdfplumber

    colors: Counter[tuple[float, float, float]] = Counter()
    with pdfplumber.open(pdf) as document:
        for page in document.pages:
            for char in page.chars:
                color = char.get("non_stroking_color")
                if not str(char.get("text") or "").strip() or not isinstance(color, (list, tuple)) or len(color) != 3:
                    continue
                rgb = tuple(round(float(channel), 4) for channel in color)
                if all(0.0 <= channel <= 1.0 for channel in rgb):
                    colors[rgb] += 1
    if not colors:
        return None
    rgb, count = colors.most_common(1)[0]
    return {
        "scope": "ordinary_body_copy",
        "source": "target_final_pdf_character_fill",
        "selection": "dominant_non_whitespace_character_color",
        "dominant_color_rgb": list(rgb),
        "dominant_color_hex": "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb),
        "supporting_character_count": count,
        "excluded_roles": ["header_name", "section_labels", "rules"],
    }

def _measure_header_vertical_rule_geometry(
    request: MeasurementRequest,
    *,
    target_pdf: Path,
    current_pdf: Path,
    current_name_text: str | None = None,
) -> MeasurementResult:
    """One transparent scalar error over the complete detected rule sets.

    Count and name-overlap penalties prevent an extra retained rule from being
    ignored; raw geometry remains in evidence for owner inspection.
    """
    intent = (request.intent or "").casefold()
    if not (
        any(word in intent for word in ("header", "name"))
        and "vertical" in intent
        and any(word in intent for word in ("rule", "line", "bar"))
    ):
        return MeasurementResult(
            request_id=request.request_id,
            status="not_measurable",
            reason="header_vertical_rule_geometry requires an explicit header/name vertical-rule intent",
            method=HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
        )
    target = _header_vertical_rule_evidence(target_pdf, request.page)
    current = _header_vertical_rule_evidence(
        current_pdf, request.page, name_text=current_name_text
    )
    if target is None or current is None:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            reason="the name/header region could not be located in one of the final PDFs",
            method=HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
        )
    target_rules = target["vertical_rules"]
    current_rules = current["vertical_rules"]
    if bool(target_rules) != bool(current_rules):
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            target_evidence=[target],
            current_evidence=[current],
            reason="one header vertical-rule set is empty; deletion is not automatic improvement",
            method=HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
        )
    paired_error = sum(
        abs(current_rule["relative_x_to_name_left"] - target_rule["relative_x_to_name_left"])
        + abs(current_rule["relative_top_to_name_top"] - target_rule["relative_top_to_name_top"])
        + abs(current_rule["relative_bottom_to_name_top"] - target_rule["relative_bottom_to_name_top"])
        for target_rule, current_rule in zip(target_rules, current_rules)
    )
    target_overlap = sum(rule["name_vertical_overlap_pt"] > 0 for rule in target_rules)
    current_overlap = sum(rule["name_vertical_overlap_pt"] > 0 for rule in current_rules)
    error = (
        100.0 * abs(len(current_rules) - len(target_rules))
        + 100.0 * abs(current_overlap - target_overlap)
        + paired_error
    )
    formula = (
        "error_pt = 100*abs(rule_count-target_rule_count) + "
        "100*abs(name_overlap_count-target_name_overlap_count) + "
        "sum paired abs(relative_x, relative_top, relative_bottom) deltas; "
        "all detected header vertical rules are included"
    )
    target["error_calculation"] = formula
    current["error_calculation"] = formula
    return MeasurementResult(
        request_id=request.request_id,
        status="confirmed",
        target_value_pt=0.0,
        current_value_pt=round(error, 3),
        delta_pt=round(error, 3),
        target_evidence=[target],
        current_evidence=[current],
        method=HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
        warnings=["coordinates are page-local pt; geometry is relative to the detected name region"],
    )

def _measurement_improvement(
    before: MeasurementResult, after: MeasurementResult
) -> tuple[bool, str]:
    if before.method != after.method:
        return False, "measurement methods differ; improvement is not comparable"
    if after.status != "confirmed":
        return False, f"identical re-measurement was {after.status}"
    if any(value is None for value in (
        before.current_value_pt, before.target_value_pt,
        after.current_value_pt, after.target_value_pt,
    )):
        return False, "identical re-measurement left scalar evidence missing"
    if before.method == after.method and before.method in {
        "header_vertical_rule_geometry/1", HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
    }:
        before_rules = (before.current_evidence[0].get("vertical_rules", []) if before.current_evidence else [])
        after_rules = (after.current_evidence[0].get("vertical_rules", []) if after.current_evidence else [])
        if len(after_rules) > len(before_rules):
            retained_crossing_rule = any(
                old.get("name_vertical_overlap_pt", 0) > 0
                and any(
                    new.get("name_vertical_overlap_pt", 0) > 0
                    and abs(new.get("height", 0) - old.get("height", 0)) <= 2.0
                    for new in after_rules
                )
                for old in before_rules
            )
            if retained_crossing_rule:
                return False, "added a vertical rule while retaining the original long name-crossing rule"
    improved = abs(after.current_value_pt - after.target_value_pt) < abs(
        before.current_value_pt - before.target_value_pt
    )
    return improved, (
        "identical deterministic error decreased" if improved
        else "identical deterministic error did not decrease"
    )

def _region_spans_from_draft(draft: TargetStructureDraft, page_height: float) -> dict[str, tuple[int, float, float]]:
    """node_id -> (page, top, bottom) from the draft's measured section
    boundary claims (each cites its sidebar label row + rule bbox)."""
    claims = []
    for claim in draft.structure:
        if claim.relation != "section_boundary":
            continue
        rule_top = None
        page_number = None
        for ref in claim.evidence:
            if ref.bbox_pt and len(ref.bbox_pt) == 4 and ref.page_number is not None:
                page_number = ref.page_number
                if ref.bbox_pt[1] > 0 or ref.bbox_pt[3] > ref.bbox_pt[1]:
                    rule_top = float(ref.bbox_pt[1])
        if page_number is not None:
            claims.append((page_number, rule_top, claim.child))
    spans: dict[str, tuple[int, float, float]] = {}
    by_page: dict[int, list[tuple[float, str]]] = {}
    for page_number, rule_top, child in claims:
        by_page.setdefault(page_number, []).append((rule_top, child))
    for page_number, rows in by_page.items():
        rows.sort()
        for index, (rule_top, child) in enumerate(rows):
            bottom = rows[index + 1][0] if index + 1 < len(rows) else page_height
            spans[child] = (page_number, rule_top + 1.0, bottom)
    return spans

def _apply_lane_a_structure(plan: Any, proposal: LaneAStructureProposal | None) -> Any:
    """Map the Lane A typed primitives onto the compiled plan (the fixed
    renderer interprets them; unknown section ids fail validation)."""
    if proposal is None:
        return plan
    updated_sections = []
    for section in plan.sections:
        match = next(
            (item for item in proposal.sections if item.section_node_id == section.node_id),
            None,
        )
        if match is not None:
            section = section.model_copy(
                update={
                    "rail_heading": match.heading_in_rail or None,
                    "rail_label_width_pt": match.rail_label_width_pt,
                    "rail_label_align": match.rail_label_align,
                    "entry_meta_placement": match.entry_meta_placement,
                }
            )
        updated_sections.append(section)
    updated_appended = []
    for section in plan.appended_sections:
        match = next(
            (item for item in proposal.sections if item.section_node_id == section.node_id),
            None,
        )
        if match is not None:
            section = section.model_copy(
                update={
                    "rail_heading": match.heading_in_rail or None,
                    "rail_label_width_pt": match.rail_label_width_pt,
                    "rail_label_align": match.rail_label_align,
                    "entry_meta_placement": match.entry_meta_placement,
                }
            )
        updated_appended.append(section)
    # The compiled plan is a SUBSET of the state's sections (contentless
    # sections render nothing); primitives for plan-absent sections are
    # recorded, not applied. Unknown state ids are rejected earlier by
    # `_validate_lane_a_proposal` (against the state node vocabulary).
    proposed_ids = {item.section_node_id for item in proposal.sections}
    skipped = sorted(
        proposed_ids - {s.node_id for s in [*updated_sections, *updated_appended]}
    )
    notes = [*plan.notes, *(
        f"lane A structure proposal cites {node_id!r}, which has no compiled plan section (skipped)"
        for node_id in skipped
    )]
    return plan.model_copy(
        update={"sections": updated_sections, "appended_sections": updated_appended, "notes": notes}
    )

def _validate_lane_a_proposal(
    proposal: LaneAStructureProposal, state: Any
) -> str | None:
    """Shell-side Lane A validation: section ids must exist in the compiled
    state; no candidate text may appear anywhere in the proposal (candidate
    facts are never editable); rail widths are bounded by the model."""
    known = {node.node_id for node in state.nodes}
    unknown = [item.section_node_id for item in proposal.sections if item.section_node_id not in known]
    if unknown:
        return f"proposal cites unknown state nodes: {unknown}"
    payload = proposal.model_dump_json()
    for leaf in getattr(state, "candidate_texts", []) or []:
        if leaf and leaf in payload:  # candidate facts can never enter (defensive)
            return "proposal carries candidate text"
    return None

def _scripted_lane_a_proposal(state: Any, derived: dict[str, Any]) -> LaneAStructureProposal:
    """Offline verification-path Lane A Builder: rails every mapped section
    using the GENERIC two-column derivation's measured rail extent (never a
    target constant)."""
    rules = derived.get("sidebar_rules") or {}
    widths = [
        float(rule["x1_pt"]) - float(rule["x0_pt"])
        for rule in rules.values()
        if rule.get("x1_pt") and rule.get("x0_pt")
    ]
    rail_width = round(sum(widths) / len(widths), 3) if widths else 110.0
    sections = [
        E5LaneASection(
            section_node_id=node.node_id,
            heading_in_rail=node.kind == "section",
            rail_label_width_pt=round(rail_width, 3) if node.kind == "section" else None,
            rail_label_align="right" if node.kind == "section" else None,
        )
        for node in state.nodes
        if node.kind == "section"
    ]
    return LaneAStructureProposal(
        proposal_id="scripted-rail-1",
        sections=sections,
        agent="scripted",
    )

def _scripted_lane_b_template(base: AuthoredTemplateCandidate, finding: DefectFinding, result: MeasurementResult) -> AuthoredTemplateCandidate:
    """Offline verification-path Lane B Builder: one bounded deterministic
    template mutation for the measured defect class (entry-gap compression)."""
    css = base.css
    if result.delta_pt is not None and result.delta_pt > 0 and "margin-top: 6pt" in css:
        css = css.replace("margin-top: 6pt", f"margin-top: {max(1.0, 6.0 - abs(result.delta_pt) / 2):.1f}pt")
    return base.model_copy(update={"template_id": base.template_id + "-r", "css": css})

def _header_crop_from_overview_source(overview: Path, output: Path) -> Path:
    """Crop the header from the original 2x page PNG behind an overview."""
    from PIL import Image

    source_stem = overview.stem.split("_overview_", 1)[-1]
    source = overview.with_name(f"{source_stem}.png")
    if not source.exists():
        raise FileNotFoundError(f"full-resolution page behind overview is missing: {source}")
    with Image.open(source) as image:
        image.crop((0, 0, image.width, round(image.height * 0.25))).save(output)
    return output

def _review_scope_payload(
    round_no: int,
    changed_regions: list[str],
    findings: list["DefectFinding"],
    *,
    ledger_statuses: dict[str, str] | None = None,
    changed_pages: list[int] | None = None,
) -> dict[str, Any]:
    """Phase 0 review scope (E5 work order): round 1 reviews the whole
    document; later rounds focus on changed regions, dependent regions, and
    unresolved findings with ONE bounded global sweep — the Reviewer never
    regenerates the entire defect list after a local change."""
    return {
        "round": round_no,
        "whole_document": round_no == 1,
        "changed_regions": list(changed_regions),
        "affected_pages": list(changed_pages or []),
        "open_findings": [
            {
                "finding_id": finding.finding_id,
                "page": finding.page,
                "region": finding.region,
                "suspected_dimension": finding.suspected_dimension,
                "observation": finding.observation[:240],
                "status": (ledger_statuses or {}).get(finding.finding_id, "open"),
            }
            for finding in findings
            if (ledger_statuses or {}).get(finding.finding_id, "open")
            not in {"resolved", "repaired"}
        ],
        "instruction": (
            "Round 1: whole-document review. Later rounds: focus on the "
            "changed regions and their dependent regions, restate only "
            "unresolved findings, and run ONE bounded global sweep — do "
            "NOT regenerate the entire defect list."
        ),
    }

def _owning_section_id(state: Any, region_id: str | None) -> str | None:
    nodes = {node.node_id: node for node in state.nodes}
    current = nodes.get(region_id)
    visited = set()
    while current is not None and current.node_id not in visited:
        if current.kind == "section":
            return current.node_id
        visited.add(current.node_id)
        current = nodes.get(current.parent_id)
    return None


def _e5_region_catalog(state: Any, derived: dict[str, Any]) -> list[dict[str, Any]]:
    label_pages = {
        str(item.get("text") or "").casefold(): int(item["page"])
        for item in derived.get("sidebar_labels") or [] if item.get("page")
    }
    nodes = {node.node_id: node for node in state.nodes}

    def page_for(node: Any, section: Any | None) -> int | None:
        if node.kind == "header_row":
            return 1
        labels = [
            child.label for child in state.nodes
            if child.kind == "heading"
            and child.parent_id == getattr(section, "node_id", None)
            and child.label
        ]
        return next((label_pages[label.casefold()] for label in labels if label.casefold() in label_pages), None)

    catalog = []
    for node in state.nodes:
        section = nodes.get(_owning_section_id(state, node.node_id))
        sources = list(getattr(getattr(section, "binding", None), "sources", []) or [])
        catalog.append({
            "region_id": node.node_id,
            "kind": node.kind,
            "semantic_role": sources[0] if len(sources) == 1 else ("/".join(sources) or node.kind),
            "source": sources,
            "page": page_for(node, section),
        })
    return catalog

def _reviewer_presentation_facts(
    target_pdf: Path, current_pdf: Path, derived: dict[str, Any]
) -> dict[str, Any]:
    labels = derived.get("sidebar_labels") or []
    right_edges = [round(float(item["x1"]), 3) for item in labels if item.get("x1") is not None]
    return {
        "header_vertical_rule_target_context": _header_vertical_rule_target_context(target_pdf),
        "sidebar_labels": {
            "alignment": "right",
            "common_right_edge_pt": round(sum(right_edges) / len(right_edges), 3) if right_edges else None,
            "max_right_edge_spread_pt": round(max(right_edges) - min(right_edges), 3) if right_edges else None,
        },
        "target_ordinary_body_color": _target_body_text_color_context(target_pdf),
        "current_render_ordinary_body_color": _target_body_text_color_context(current_pdf),
    }

def _e5_reviewer_message(
    *,
    target_version: str,
    render_version: str,
    page: int,
    region_catalog: list[dict[str, Any]],
    review_scope: dict[str, Any],
    candidate_review_scope: dict[str, Any],
    lane: str,
    deterministic_presentation_facts: dict[str, Any],
    image_pages: dict[str, int | None] | None = None,
) -> str:
    context = {
        "target_version": target_version,
        "render_version": render_version,
        "page": page,
        "region_catalog": region_catalog,
        "review_scope": review_scope,
        "candidate_review_scope": candidate_review_scope,
        "content_comparison_policy": {
            "target_values": "style_reference_only",
            "render_values": "candidate_owned_including_synthetic_placeholders",
            "value_equality_required": False,
        },
        "representation_capabilities": E5_LANE_REPAIR_SURFACE[lane],
        "deterministic_presentation_facts": deterministic_presentation_facts,
    }
    if image_pages is not None:
        context["image_pages"] = image_pages
        context["pagination_note"] = (
            "Candidate content may reflow beyond target pages. Extra render pages use the last "
            "target page as a style reference; page-count differences alone are not defects."
        )
    return (
        "Compare the TARGET and RENDER images for exactly this page. Use one "
        "region_id from region_catalog per finding and exactly one suspected "
        "dimension. Carry the exact target_version, render_version, and page "
        "below. Do not combine ABOUT ME with contact, target-only content with "
        "candidate presentation, or multiple regions/dimensions. role_gap is "
        "only for entry/job/role spacing; header_vertical_rule_geometry is only "
        "for the header/name vertical rule. Atomic candidate:name and flat "
        "candidate:contact cannot create Builder-repairable split-name or "
        "label/value-table residuals. Candidate values are omitted from this JSON,\n"
        "not from the rendered document. Different values and synthetic proof\n"
        "tokens are expected; compare their presentation, not their wording.\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )

def _validate_reviewer_regions(
    findings: list[DefectFinding], region_ids: set[str], page: int | set[int]
) -> list[DefectFinding]:
    pages = page if isinstance(page, set) else {page}
    valid = []
    for finding in findings:
        if finding.page not in pages or finding.region not in region_ids:
            continue
        valid.append(finding)
    return valid

def _apply_header_overflow_disposition(candidate: Any, state: Any) -> Any:
    """Generic E3 shell transition, shared by the main render and every
    content-shape probe: header fields with no measured home in the compiled
    state route through the EXPLICIT candidate-only header-overflow node
    (nothing silently dropped, nothing invented)."""
    from app.pipeline.candidate_content import UnroutableContent

    header_slots_in_state = {
        slot for node in state.nodes if node.kind == "header_row" for slot in node.slots
    }
    unhomed = [
        leaf for leaf in candidate.leaves
        if leaf.kind == "header_field" and leaf.slot not in header_slots_in_state
    ]
    if not unhomed:
        return candidate
    return candidate.model_copy(
        update={
            "unroutable": [
                *candidate.unroutable,
                *(
                    UnroutableContent(
                        text=leaf.text or "",
                        reason=(
                            "the compiled target header region carries no measured "
                            f"row with slot {leaf.slot!r}; routes through the explicit "
                            "candidate-only header-overflow node"
                        ),
                        slot=leaf.slot,
                    )
                    for leaf in unhomed
                ),
            ]
        }
    )

_PROBE_ROLE_ORDER = (
    "summary", "skills", "languages", "work_experience", "education",
    "certifications", "additional_details",
)

_PROBE_ROLE_KIND = {
    "summary": "paragraph",
    "work_experience": "entries",
    "education": "entries",
    "skills": "item_list",
    "languages": "item_list",
    "certifications": "item_list",
    "additional_details": "item_list",
}

_PROBE_ROLE_HEADING = {
    "skills": "SKILLS", "languages": "LANGUAGES", "certifications": "CERTIFICATIONS",
    "additional_details": "ADDITIONAL",
}

def _sectioned_probe_candidate(fixture: Any) -> Any:
    """Deterministic shell promotion of a bare C2-0a structural fixture into
    a sectioned render context, so the Lane A probe can run the REAL
    plan -> HTML -> Chrome PDF chain with the same routing rules
    `run_flow_probe` validates (generic role -> content-kind mapping; no
    target-specific identifier, no candidate text change).

    `additional_section` containers are structural only: the plan's mapped
    item-list merge consumes top-level item leaves, so the shell flattens
    the container (children re-parented as top-level items) — the same
    top-level-item shape every candidate render context uses."""
    from app.pipeline.candidate_content import CandidateSection

    containers = {leaf.leaf_id for leaf in fixture.leaves if leaf.kind == "additional_section"}
    flattened = [
        leaf.model_copy(update={"parent_leaf_id": None}) if leaf.parent_leaf_id in containers else leaf
        for leaf in fixture.leaves if leaf.kind != "additional_section"
    ]
    fixture = fixture.model_copy(update={"leaves": flattened})
    sections = []
    for role in _PROBE_ROLE_ORDER:
        leaves = [leaf for leaf in fixture.leaves if leaf.source == role]
        if not leaves:
            continue
        sections.append(
            CandidateSection(
                section_id=role,
                heading=_PROBE_ROLE_HEADING.get(role),
                source=role,
                content_kind=_PROBE_ROLE_KIND[role],
                leaf_ids=[leaf.leaf_id for leaf in leaves],
            )
        )
    return fixture.model_copy(update={"sections": sections})

def _probe_candidate_with_text(fixture: Any) -> Any:
    """Independent content-shape probe with deterministic synthetic values
    (the canonical C2 fixtures carry structure only). Values are unique per
    leaf and carry NO section/identifier words, so the privacy gate's
    line-granularity check cannot trip on the probe's own placeholder text.
    Synthetic placeholder text is authorized probe content — never a target
    or candidate fact."""
    leaves = [
        leaf.model_copy(update={"text": f"probe value {index:03d}"})
        for index, leaf in enumerate(fixture.leaves, 1)
    ]
    return fixture.model_copy(update={"leaves": leaves})

def _estimate_lane_cost(
    record: "E5LaneRecord", pricing: dict[str, Any] | None
) -> dict[str, Any]:
    """Provider accounting (E5 work order): raw usage is always recorded;
    a cost estimate exists ONLY when verified official pricing was supplied
    in the frozen config. No price is ever invented."""
    usage = record.budget_state.get("usage", {})
    raw = {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "live_model_calls": record.budget_state.get("calls_by_mode", {}).get("live", 0),
    }
    if not pricing or not pricing.get("verified"):
        return {**raw, "estimated_cost_usd": None, "pricing_source": None,
                "note": "exact billing unavailable; raw usage preserved"}
    in_price = pricing.get("input_per_million_usd")
    out_price = pricing.get("output_per_million_usd")
    if in_price is None or out_price is None:
        return {**raw, "estimated_cost_usd": None, "pricing_source": pricing.get("source_url")}
    estimate = (
        raw["input_tokens"] / 1_000_000 * in_price
        + raw["output_tokens"] / 1_000_000 * out_price
    )
    return {
        **raw,
        "estimated_cost_usd": round(estimate, 4),
        "pricing_source": pricing.get("source_url"),
        "assumptions": pricing.get("assumptions"),
    }

def _with_connection_retry(call, *, trace: RunTrace, what: str, attempts: int = 3):
    """Bounded retry for transient provider Connection errors (observed on
    long multimodal review calls); every retry is traced, and a persistent
    failure escalates as before — never silent, never unbounded."""
    import time as _time

    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as error:  # noqa: BLE001 - retried only when transient
            # The SDK wraps request-hook budget failures in APIConnectionError.
            cause = error.__cause__
            while cause is not None:
                if isinstance(cause, BudgetExhausted):
                    raise cause from None
                cause = cause.__cause__
            if "connection" not in str(error).casefold():
                raise
            last = error
            trace.add(
                agent="shell", phase="loop", action="connection_retry",
                note=f"{what}: attempt {attempt} failed ({str(error)[:120]})",
            )
            _time.sleep(10)
    assert last is not None
    raise last

def _live_lane_a_builder(
    budget: RunBudget,
    trace: RunTrace,
    *,
    payload: dict[str, Any],
    images: list[Any],
    proposal_id: str,
    audit: E5AgentAuditSpec | None = None,
) -> LaneAStructureProposal:
    """The LIVE Lane A Builder: one bounded request that outputs the typed
    provider-neutral structure primitives. The shell validates, applies and
    renders — the agent never promotes anything. ``audit`` records the full
    model-visible message history (owner decision 2026-09-22)."""
    from pydantic_ai import Agent, BinaryContent

    from app.pipeline.runtime import _live_model

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before lane A builder")
    agent = Agent(
        _live_model(budget, trace, "lane_a_builder"),
        output_type=LaneAStructureProposal,
        name="lane_a_builder",
        instructions=E5_LANE_A_BUILDER_INSTRUCTIONS,
    )
    user_messages = [
        json.dumps(payload, ensure_ascii=False, indent=1),
        *[BinaryContent(data=image.read_bytes(), media_type="image/png") for image in images],
    ]
    model_name = getattr(getattr(agent, "model", None), "model_name", None)
    try:
        run_result = _with_connection_retry(
            lambda: agent.run_sync(
                user_messages,
                usage_limits=_call_limits(budget, E5_BUILDER_MAX_REQUESTS),
                model_settings=_live_model_settings(),
            ),
            trace=trace, what="lane A builder",
        )
    except Exception as error:
        if audit is not None:
            _e5_agent_audit_record(
                trace, audit, input_messages=user_messages,
                run_result=None, error=f"{type(error).__name__}: {error}",
                model_name=model_name,
            )
        raise
    if audit is not None:
        _e5_agent_audit_record(
            trace, audit, input_messages=user_messages, run_result=run_result, error=None,
            model_name=model_name,
        )
    _record_usage(budget, trace, "lane_a_builder", "build", run_result)
    proposal = run_result.output
    return proposal.model_copy(update={"proposal_id": proposal_id, "agent": "llm"})

def _live_lane_b_builder(
    budget: RunBudget,
    trace: RunTrace,
    *,
    payload: dict[str, Any],
    images: list[Any],
    template_id: str,
    audit: E5AgentAuditSpec | None = None,
) -> AuthoredTemplateCandidate:
    """The LIVE Lane B Builder: one bounded request over target + current
    render images that authors the constrained HTML/CSS template candidate.
    The shell validates the safety boundary and fills typed slots — the agent
    never touches candidate values or the filesystem. ``audit`` records the
    full model-visible message history (owner decision 2026-09-22)."""
    from pydantic_ai import Agent, BinaryContent, PromptedOutput, capture_run_messages

    from app.pipeline.runtime import _live_model
    from app.pipeline.authored_template import AuthoredTemplateCandidate

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before lane B builder")
    agent = Agent(
        _live_model(budget, trace, "lane_b_builder"),
        # Prompted JSON avoids the forced final-result tool choice that
        # DeepSeek Chat Completions rejects while thinking is enabled.
        output_type=PromptedOutput(AuthoredTemplateCandidate),
        name="lane_b_builder",
        instructions=E5_LANE_B_BUILDER_INSTRUCTIONS,
    )
    user_messages = [
        json.dumps(payload, ensure_ascii=False, indent=1),
        *[BinaryContent(data=image.read_bytes(), media_type="image/png") for image in images],
    ]
    model_name = getattr(getattr(agent, "model", None), "model_name", None)
    captured_messages: list[Any] = []
    try:
        with capture_run_messages() as captured_messages:
            run_result = _with_connection_retry(
                lambda: agent.run_sync(
                    user_messages,
                    usage_limits=_call_limits(budget, E5_BUILDER_MAX_REQUESTS),
                    model_settings=_live_model_settings(),
                ),
                trace=trace, what="lane B builder",
            )
    except Exception as error:
        if audit is not None:
            _e5_agent_audit_record(
                trace, audit, input_messages=captured_messages or user_messages,
                run_result=None, error=f"{type(error).__name__}: {error}",
                model_name=model_name,
            )
        raise
    if audit is not None:
        _e5_agent_audit_record(
            trace, audit, input_messages=user_messages, run_result=run_result, error=None,
            model_name=model_name,
        )
    _record_usage(budget, trace, "lane_b_builder", "build", run_result)
    template = run_result.output
    return template.model_copy(update={"template_id": template_id})

def _lane_a_attribution_layout(plan: Any, state: Any, findings: list[DefectFinding]) -> dict[str, Any]:
    """Describe consumed A structure and primitive effects without candidate values."""
    sections = {_owning_section_id(state, finding.region) for finding in findings}
    return {
        "primitive_effects": {
            "heading_in_rail": "Places the heading in a label column beside the content.",
            "rail_label_width_pt": "Sets label-column width in pt; reducing it widens the content column and can reduce wrapping and entry-head spacing.",
            "rail_label_align": "Aligns text inside the label column; it does not move the content column.",
            "entry_meta_placement": "Only affects legacy entries without explicit rows. Explicit rows already define left/right placement and are not rearranged by this primitive.",
        },
        "sections": [
            {
                "section_node_id": section.node_id,
                "heading_in_rail": bool(section.rail_heading),
                "rail_label_width_pt": section.rail_label_width_pt,
                "entries": [
                    {"node_id": entry.node_id,
                     "entry_meta_placement_consumed": entry.rows is None and bool(entry.meta_lines),
                     "rows": [{"role": row.role, "left_field_count": len(row.left),
                               "right_field_count": len(row.right)} for row in entry.rows or []]}
                    for entry in section.entries
                ],
            }
            for section in [*plan.sections, *plan.appended_sections] if section.node_id in sections
        ] if plan is not None else [],
    }


def _live_attribution_batch(
    pod: "DualSourcePod",
    budget: RunBudget,
    trace: RunTrace,
    *,
    findings: list[DefectFinding],
    results: list[MeasurementResult],
    lane: str = "a",
    current_representation: Any | None = None,
    compiled_layout: dict[str, Any] | None = None,
    audit: E5AgentAuditSpec | None = None,
) -> list[LiveAttributionHypothesis]:
    """Phase 0 batched live attribution: findings that share the same region
    (and likely owning layer) are traced in ONE bounded call, ordered like
    the input findings list."""
    from pydantic_ai import Agent, Tool

    from app.pipeline.runtime import _live_model

    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before attribution batch")
    tools = _pod_tools(pod, budget, trace, agent_name="attribution_investigator")
    tools.append(_traced_render_words(pod, budget, trace))
    agent = Agent(
        _live_model(budget, trace, "attribution_investigator"),
        output_type=list[LiveAttributionHypothesis],
        name="attribution_investigator",
        instructions=E5_ATTRIBUTION_INSTRUCTIONS,
        # These tools share a mutable evidence pod and call PDFium. PDFium
        # forbids concurrent calls even for separate documents; sync tools
        # otherwise run in parallel worker threads under PydanticAI.
        tools=[Tool(tool, sequential=True) for tool in tools],
    )
    payload = {
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "measurements": [result.model_dump(mode="json") for result in results],
        "target_description": pod.describe_target(),
        "current_representation": (
            current_representation.model_dump(mode="json")
            if current_representation is not None else None
        ),
        "current_render_version": audit.render_version if audit is not None else None,
        "compiled_layout": compiled_layout,
        # The lane's ACTUAL repair surface: ownership of a confirmed defect may
        # only follow a surface that exists. Without it the role had to guess
        # the owning layer, and in live run `e_pipeline_e5_20260923T095557Z` it
        # attributed Lane A's plan-level structure defects to a renderer no
        # agent owns — unreachable by construction.
        "lane_repair_surface": E5_LANE_REPAIR_SURFACE.get(lane, ""),
        "budget_note": (
            "ONE bounded agent run for this whole batch; decide from the "
            "evidence you already have plus at most a few tool calls."
        ),
    }
    user_messages = [json.dumps(payload, ensure_ascii=False, indent=1)]
    model_name = getattr(getattr(agent, "model", None), "model_name", None)
    try:
        run_result = agent.run_sync(
            user_messages,
            usage_limits=_call_limits(
                budget,
                max(
                    E5_ATTRIBUTION_MAX_REQUESTS,
                    E4_PER_CALL_MAX_TOOL_CALLS + len(findings) + 1,
                ),
            ),
            model_settings=_live_model_settings(),
        )
    except Exception as error:
        if audit is not None:
            _e5_agent_audit_record(
                trace, audit, input_messages=user_messages,
                run_result=None, error=f"{type(error).__name__}: {error}",
                model_name=model_name,
            )
        raise
    if audit is not None:
        _e5_agent_audit_record(
            trace, audit, input_messages=user_messages, run_result=run_result, error=None,
            model_name=model_name,
        )
    _record_usage(budget, trace, "attribution_investigator", "attribute_batch", run_result)
    return list(run_result.output)

E5_SOURCE_FILES = (
    'app/pipeline/__init__.py',
    'app/pipeline/__main__.py',
    'app/pipeline/assets/fonts/charter-LICENSE.txt',
    'app/pipeline/assets/fonts/charter-bold.woff2',
    'app/pipeline/assets/fonts/charter-bolditalic.woff2',
    'app/pipeline/assets/fonts/charter-regular.woff2',
    'app/pipeline/assets/fonts/charter-regularitalic.woff2',
    'app/pipeline/assets/fonts/lato-bold.woff2',
    'app/pipeline/assets/fonts/lato-bolditalic.woff2',
    'app/pipeline/assets/fonts/lato-regular.woff2',
    'app/pipeline/assets/fonts/lato-regularitalic.woff2',
    'app/pipeline/assets/fonts/lato.css',
    'app/pipeline/assets/fonts/roboto-LICENSE.txt',
    'app/pipeline/assets/fonts/roboto-bold.woff2',
    'app/pipeline/assets/fonts/roboto-bolditalic.woff2',
    'app/pipeline/assets/fonts/roboto-regular.woff2',
    'app/pipeline/assets/fonts/roboto-regularitalic.woff2',
    'app/pipeline/authored_template.py',
    'app/pipeline/candidate_content.py',
    'app/pipeline/evidence.py',
    'app/pipeline/fonts.py',
    'app/pipeline/html_renderer.py',
    'app/pipeline/layout_compiler.py',
    'app/pipeline/layout_state.py',
    'app/pipeline/measurement.py',
    'app/pipeline/quality.py',
    'app/pipeline/render_plan.py',
    'app/pipeline/rendering.py',
    'app/pipeline/review.py',
    'app/pipeline/runtime.py',
    'app/pipeline/service.py',
    'app/template_analysis/commercial/models.py',
    'app/template_analysis/schemas.py',
    'app/generation/render_plan.py',
    'app/ingestion/pdf_reader.py',
)


def _experiment_source_hashes() -> dict[str, str]:
    """SHA-256 of the selected critical DIRECT runtime sources this E5 run
    executes (the entry module's direct local imports plus the shared
    gate/binding modules), recorded in run_config.json BEFORE the first live
    call. This is SELECTED CRITICAL-SOURCE DRIFT DETECTION: it detects a
    mid-run change to the registered files. It does NOT cover transitive
    dependencies and is NOT a proof of exact source identity — an exact
    binding additionally requires starting from a committed HEAD with no
    uncommitted changes to the running code."""
    return {name: _sha256_file(ROOT / name) for name in E5_SOURCE_FILES}

def _tracked_diff_sha256(root: Path, names: tuple[str, ...]) -> str | None:
    """SHA-256 of the tracked working-tree diff of the registered sources
    relative to HEAD (None when git is unavailable). Scoped to exactly the
    registered paths: changes to unrelated files (e.g. the owner's proposal
    notes) never enter the identity."""
    try:
        diff = subprocess.run(
            ["git", "diff", "HEAD", "--", *names],
            capture_output=True, text=True, check=True, cwd=root,
        ).stdout
    except Exception:
        return None
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()

def _untracked_source_hashes() -> dict[str, str]:
    """Registered sources NOT tracked at HEAD (if any): recorded by their
    SHA-256 so untracked experiment modules are still identity-bound."""
    untracked: dict[str, str] = {}
    for name in E5_SOURCE_FILES:
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", name],
                capture_output=True, text=True, cwd=ROOT,
            ).returncode == 0
        except Exception:
            tracked = False
        if not tracked:
            untracked[name] = _sha256_file(ROOT / name)
    return untracked

def _source_identity() -> dict[str, Any]:
    """The full source identity recorded in the frozen config: file hashes,
    the tracked-diff hash relative to HEAD, and hashes of any registered
    untracked sources."""
    return {
        "files": _experiment_source_hashes(),
        "tracked_diff_sha256": _tracked_diff_sha256(ROOT, E5_SOURCE_FILES),
        "untracked_files": _untracked_source_hashes(),
    }

def _source_hashes_changed(config: dict[str, Any]) -> bool:
    """True when the current source identity differs from the frozen config's
    (the run then records `source_changed` and is NOT source-identity
    stable). A config with missing or incomplete source identity is never
    trusted as stable."""
    frozen = config.get("source_hashes")
    if not isinstance(frozen, dict) or not isinstance(frozen.get("files"), dict):
        return True
    current = _source_identity()
    return any(frozen.get(key) != current[key] for key in current)

def _freeze_e5_config(
    out_dir: Path,
    *,
    target_id: str,
    target_frozen: FrozenCase,
    lanes: tuple[str, ...],
    max_repair_rounds: int,
    live: bool,
    prompts: dict[str, str],
    rubric_reference: dict[str, Any],
    candidate_sha256: str,
    shared_draft_ref: dict[str, Any],
    pricing: dict[str, Any] | None,
    presentation_label_approval_record: dict[str, Any] | None = None,
    budget: RunBudget | None = None,
    builder_reserve_requests: int = E5_BUILDER_RESERVE_REQUESTS,
) -> dict[str, Any]:
    """Frozen BEFORE the first live call (E5 work order): shared target/
    candidate/evidence/model/prompts/budgets plus per-lane budgets. Model
    NAMES only — never keys, URLs or tokens; rubric content never recorded.
    ``presentation_label_approval_record`` freezes the validated label-approval
    identity (provided/validated/target/catalog sha256/approved ids); an
    invalid approval aborts BEFORE this freeze, so a recorded record is one
    the shell actually applied. An omitted record (default) freezes the same
    shape with ``provided=False`` — no approval is never ambiguity."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
    except Exception:
        commit = "unknown"
    config = {
        "run_id": out_dir.name,
        "frozen_before_first_live_call": True,
        "target_id": target_id,
        "target_sha256": target_frozen.target_sha256,
        "adobe_json_sha256": target_frozen.adobe_json_sha256,
        "candidate_input": {
            "role": "explicit reviewed candidate render context",
            "sha256": candidate_sha256,
            "builder_boundary": "candidate facts are never editable by any agent",
        },
        "shared_structure_draft": shared_draft_ref,
        "presentation_label_approval": presentation_label_approval_record or {
            "provided": False,
            "validated": False,
            "target_sha256": None,
            "catalog_sha256": None,
            "approved_label_ids": [],
        },
        "model_configuration": _model_identity(),
        "sampling": {"temperature": E4_TEMPERATURE, "thinking": "disabled", "max_output_tokens": 16384},
        "budgets": {
            "global": budget.to_json() if budget is not None else None,
            "per_lane": {
                "max_model_requests": min(E5_LANE_MAX_MODEL_REQUESTS, budget.max_model_requests // len(lanes)) if budget else E5_LANE_MAX_MODEL_REQUESTS,
                "max_tool_calls": min(E5_LANE_MAX_TOOL_CALLS, budget.max_tool_calls // len(lanes)) if budget else E5_LANE_MAX_TOOL_CALLS,
                "max_request_bytes": budget.max_request_bytes if budget else None,
                "max_repair_rounds": max_repair_rounds,
                "builder_reserve_model_requests": builder_reserve_requests,
                "per_agent_run_max_requests": {
                    "visual_reviewer": E5_REVIEWER_MAX_REQUESTS,
                    "builder": E5_BUILDER_MAX_REQUESTS,
                    "attribution_investigator": E5_ATTRIBUTION_MAX_REQUESTS,
                },
            },
            "lanes": list(lanes),
        },
        "prompts": {
            "sha256": {
                name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for name, text in prompts.items()
            },
            "note": "full frozen prompt texts persisted in prompts.json; agent-visible only",
        },
        "permitted_tools": [
            "inspect_page_overview", "inspect_page_region", "inspect_adobe_json",
            "measure_local_pdf", "audit_coverage", "measure_render_words",
            "compare_pdf_geometry", "render_and_checkpoint",
        ],
        "gate_policy": {
            "mode": "exploratory_wide_entry",
            "blocking": {
                lane: list(_e5_blocking_gates(lane)) for lane in lanes
            },
            "note": (
                "all other checks are recorded as warnings and do not suppress "
                "a safety-valid PDF from owner review"
            ),
        },
        "lane_a_gates": [
            "deterministic_render", "no_target_candidate_facts",
            "section_order_matches_state", "no_blank_page",
            "candidate_content_accounting", "content_shapes_match_evidence",
            "content_gate",
        ],
        "lane_b_gates": [
            "no_target_candidate_facts", "candidate_content_accounting",
        ],
        "lane_b_diagnostics": [
            "pdf_presence", "no_blank_page",
        ],
        "evaluation_rubric_reference": {**rubric_reference, "not_given_to_agents": True},
        "starting_commit": commit,
        "source_hashes": {
            **_source_identity(),
            "basis": "selected_critical_source_drift_detection",
            "recorded_before_first_live_call": True,
            "note": (
                "SHA-256 + tracked-diff hash of SELECTED critical direct "
                "runtime sources (drift detection only; NOT an exact source "
                "identity — transitive dependencies are not covered, and an "
                "exact binding additionally requires a committed HEAD with "
                "no uncommitted changes to the running code); a mid-run "
                "change marks the run `source_changed` and NOT "
                "source_identity_stable"
            ),
        },
        "live": live,
        "provider_pricing": pricing or {"status": "unavailable", "estimate": None},
    }
    (out_dir / "run_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "prompts.json").write_text(
        json.dumps(prompts, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return config

def run_e5(
    target_pdf: Path,
    out_dir: Path | None = None,
    *,
    candidate: CandidateDocument,
    adobe_json: Path,
    normalized_evidence: Path,
    structure_draft: Path,
    probe_candidates: dict[str, CandidateDocument] | None = None,
    live: bool = False,
    lanes: tuple[str, ...] = ("a",),
    max_repair_rounds: int = E5_MAX_REPAIR_ROUNDS,
    budget: RunBudget | None = None,
    pricing: dict[str, Any] | None = None,
    presentation_label_approval: PresentationLabelApproval | None = None,
    builder_reserve_requests: int = E5_BUILDER_RESERVE_REQUESTS,
    layout_spec: LayoutTemplateSpec | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    """Run the selected Lane A mainline on explicit inputs by default.

    Lane B authored-template and paired comparison execution remain explicit
    choices; selecting A is not product output acceptance or API cutover.

    A safety-valid PDF exits ``ready_for_owner_review`` even when exploratory
    quality checks report warnings; ``budget_exhausted`` means no safety-valid
    output was reached before a configured ceiling. Neither means owner
    acceptance. Operational aborts describe the failed operation only.
    Content-shape probes always run
    against the SELECTED representation (the best-valid render's proposal/
    template, or diagnostic probes on the latest attempt when no best-valid
    render exists)."""
    if builder_reserve_requests < E5_BUILDER_MAX_REQUESTS:
        raise ValueError(
            f"builder reserve must cover one full Builder turn ({E5_BUILDER_MAX_REQUESTS} requests)"
        )
    from app.pipeline.rendering import build_format_summary

    from app.pipeline.render_plan import compile_render_plan
    from app.pipeline.html_renderer import RENDERER_VERSION, render_html, render_layout_template
    from app.pipeline import quality as c2r
    from app.pipeline.layout_state import state_bytes
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence
    from app.pipeline.authored_template import (
        authored_network_disabled_environment,
        authored_pdf_presence_gate,
        authored_privacy_gate,
        build_authored_document,
        fill_authored_template,
        presentation_label_markers,
        validate_authored_template,
    )

    if not lanes or set(lanes) - {"a", "b"} or len(lanes) != len(set(lanes)):
        raise ValueError("lanes must contain a, b, or both exactly once")
    if layout_spec is not None and (lanes != ("a",) or (not live and max_repair_rounds != 0)):
        raise ValueError("LayoutTemplateSpec requires Lane A; offline previews require zero repair rounds")
    if "b" in lanes and any(leaf.kind == "additional_entry" for leaf in candidate.leaves):
        raise ValueError("structured additional sections require Lane A only")
    if any(not re.fullmatch(r"[a-z0-9_]+", name) for name in (probe_candidates or {})):
        raise ValueError("probe names must contain lowercase letters, digits, or underscores")
    started = time.time()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    target_pdf = target_pdf.resolve()
    if not target_pdf.exists():
        raise RuntimeError(f"target PDF not found: {target_pdf}")
    target_sha256 = _sha256_file(target_pdf)
    template_state = None
    template_render = None
    template_label_catalog = None
    template_approved_labels = None
    if layout_spec is not None:
        import pdfplumber
        layout_spec = LayoutTemplateSpec.model_validate(layout_spec.model_dump())
        with pdfplumber.open(target_pdf) as document:
            if any(abs(page.width - layout_spec.page.width_pt) > 0.1 or abs(page.height - layout_spec.page.height_pt) > 0.1 for page in document.pages):
                raise ValueError("LayoutTemplateSpec page dimensions do not match the target PDF")
            template_render = render_layout_template(layout_spec, candidate, target_sha256=target_sha256, page_count=len(document.pages))
        template_state = template_render[0]
        template_label_catalog = [
            PresentationLabel(label_id=f"label.template.{node.node_id}", text=node.label,
                              kind="section_heading", evidence_ids=node.evidence_ids)
            for node in template_state.nodes if node.kind == "heading"
        ]
        template_approved_labels = (
            _apply_presentation_label_approval(template_label_catalog, presentation_label_approval, target_sha256=target_sha256)
            if presentation_label_approval is not None else _heuristic_presentation_labels(template_label_catalog)
        )
        if {label.label_id for label in template_label_catalog} != {label.label_id for label in template_approved_labels}:
            raise ValueError("unrecognized product-template presentation labels require explicit identity-bound label approval")
    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("e_pipeline_e5_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)
    budget = budget or RunBudget(
        max_model_requests=E5_LANE_MAX_MODEL_REQUESTS * len(lanes),
        max_tool_calls=E5_LANE_MAX_TOOL_CALLS * len(lanes),
    )

    def abort(operation: str, error: Exception) -> tuple[Path, str, dict[str, Any]]:
        record = E5LoopRecord(target_id="unknown", target_sha256="0" * 64)
        record.summary["terminal_state"] = "operational_abort"
        record.summary["abort"] = {"operation": operation, "error": str(error)}
        (out_dir / "e5_state.json").write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out_dir, "operational_abort", record.model_dump(mode="json")

    # -- 1. FREEZE shared inputs ------------------------------------------
    raw_path = Path(adobe_json).resolve()
    normalized_path = Path(normalized_evidence).resolve()
    draft_source = Path(structure_draft).resolve()
    for input_path in (raw_path, normalized_path, draft_source):
        if not input_path.is_file():
            return abort("pipeline_input", FileNotFoundError(str(input_path)))
    try:
        candidate = CandidateDocument.model_validate(candidate.model_dump(mode="python"))
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Adobe evidence must be a JSON object")
        normalized = NormalizedLayoutEvidence.model_validate_json(normalized_path.read_text())
        draft = TargetStructureDraft.model_validate_json(draft_source.read_text())
    except (OSError, ValueError) as error:
        return abort("pipeline_input", error)
    (out_dir / "adobe_raw.json").write_text(
        json.dumps(
            redact_signed_urls(raw),
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    assert_no_signed_strings((out_dir / "adobe_raw.json").read_text(encoding="utf-8"))
    shutil.copy2(normalized_path, out_dir / "enriched_evidence.json")
    target_frozen = freeze_cases(target_pdf, raw_path, role="frozen_blind", case_id=target_pdf.stem)
    (out_dir / "frozen_case.json").write_text(target_frozen.model_dump_json(indent=2), encoding="utf-8")
    target_id = f"target-{target_pdf.stem}-v1"
    rubric_reference = {"status": "not_configured"}

    # -- 2. Shared compile basis + shared evidence-linked StructureDraft ----
    pod = DualSourcePod(
        target_pdf, out_dir / "adobe_raw.json", out_dir,
        render_pdf=None, normalized=normalized,
    )
    try:
        summary = build_format_summary(normalized, raw, target_pdf)
        if template_state is None:
            state, derived = compile_two_column_state(target_pdf, summary, evidence=normalized)
        else:
            state, derived = template_state, {}
    except Exception as error:
        return abort("compile_two_column_state", error)
    # ONE shell-owned presentation-label catalog for BOTH lanes (owner decision
    # 2026-09-21): built from the same measured sidebar-label evidence the two
    # lanes already share. Lane A renders those labels from the render plan,
    # Lane B can only reference `{{label:<label_id>}}`; both privacy gates and
    # the owner listing reference THIS catalog, never a lane-local set.
    try:
        label_catalog = _presentation_label_catalog(derived) if template_label_catalog is None else template_label_catalog
    except Exception as error:
        return abort("presentation_label_catalog", error)
    catalog_sha256 = presentation_label_catalog_sha256(label_catalog)
    # Owner direction 2026-10-22: recognize conventional headings without
    # per-label human approval; legacy explicit input still validates identity.
    try:
        approved_label_catalog = template_approved_labels if template_approved_labels is not None else (
            _apply_presentation_label_approval(
                label_catalog, presentation_label_approval, target_sha256=target_sha256,
            ) if presentation_label_approval is not None
            else _heuristic_presentation_labels(label_catalog)
        )
    except Exception as error:
        return abort("presentation_label_approval", error)
    approval_provided = presentation_label_approval is not None
    recognition_method = "explicit_owner_input" if approval_provided else (
        "measured_exact_alias/2" if layout_spec is None else "layout_template_exact_alias/1"
    )
    # Only shell-recognized entries are renderable and privacy-excluded. The
    # proposed remainder stays visible in the owner listing but can never be
    # rendered: its ids are never issued to the template boundary, and its
    # text is never excluded from the privacy gate (so rendering it fails
    # closed as a leak instead of being silently tolerated).
    approved_label_texts = {label.text for label in approved_label_catalog}
    (out_dir / "c2_layout_state.json").write_bytes(state_bytes(state))
    body_scaffold, bullet_tiers = None, {}
    if layout_spec is None:
        _headings_scaffold, body_scaffold = compile_two_column_state_for_scaffold(target_pdf, summary)
        bullet_tiers = _two_column_bullet_tiers(_pdf_lines_and_marks(target_pdf)[0], summary)

    # Structure is an explicit caller input; no previous run is consulted.
    (out_dir / "structure_draft.json").write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    shared_draft_ref = {
        "source_file": draft_source.name,
        "sha256": _sha256_file(draft_source),
        "normalized_evidence_sha256": _sha256_file(normalized_path),
        "claims": len(draft.structure),
        "shared_by_lanes": True,
    }
    if layout_spec is not None:
        from app.generation.render_plan import layout_spec_sha256

        (out_dir / "layout_template_spec.json").write_text(layout_spec.model_dump_json(indent=2), encoding="utf-8")
        shared_draft_ref["layout_template"] = {
            "sha256": layout_spec_sha256(layout_spec), "template_version": layout_spec.template_version,
            "schema_version": layout_spec.schema_version, "compiler": RENDERER_VERSION,
            "template_approved": False, "api_cutover": False,
        }
    page_height = _page_pt_size(target_pdf, 1)[1]
    region_spans = _region_spans_from_draft(draft, page_height)

    def _known_evidence_ids() -> set[str]:
        """The evidence ids the shell ACTUALLY issued (pod access records +
        the shared draft's cited refs). Authored-template evidence_refs must
        be members of this set — string shape alone is never enough."""
        ids: set[str] = set()
        for record in getattr(pod, "records", []) or []:
            record_id = (
                record.get("evidence_id")
                if isinstance(record, dict)
                else getattr(record, "evidence_id", None)
            )
            if record_id:
                ids.add(record_id)
        for claim in draft.structure:
            for ref in claim.evidence:
                ids.add(ref.evidence_id)
        return ids

    def _e5_image_refs(
        paths: list[Any], role: str, *, first_page: int = 1
    ) -> tuple[dict[str, Any], ...]:
        """Image audit references in the EXACT user-content order: file
        path relative to the run dir, sha256, page number, target/render
        role — NEVER image bytes."""
        refs: list[dict[str, Any]] = []
        for index, path in enumerate(paths, first_page):
            path = Path(path)
            refs.append(
                {
                    "path": (
                        path.relative_to(out_dir).as_posix()
                        if path.is_relative_to(out_dir)
                        else path.name
                    ),
                    "page": index,
                    "role": role,
                    "sha256": _sha256_file(path),
                }
            )
        return tuple(refs)

    # Generic header-overflow disposition (E3 shell transition, reused for
    # the main candidate AND every content-shape probe): the compiled
    # two-column state hosts the contact rows inside section content.
    if layout_spec is None:
        candidate = _apply_header_overflow_disposition(candidate, state)
    environment = pinned_export_environment({})
    candidate_sha256 = hashlib.sha256(
        json.dumps(candidate.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    candidate_review_scope = _candidate_review_scope(candidate, label_catalog)
    candidate_name_text = next(
        (
            leaf.text for leaf in candidate.leaves
            if leaf.kind == "header_field" and leaf.slot == "name" and leaf.text
        ),
        None,
    )

    config = _freeze_e5_config(
        out_dir,
        target_id=target_id,
        target_frozen=target_frozen,
        lanes=lanes,
        max_repair_rounds=max_repair_rounds,
        builder_reserve_requests=builder_reserve_requests,
        live=live,
        prompts={
            "visual_reviewer": E5_REVIEWER_INSTRUCTIONS,
            "lane_a_builder": E5_LANE_A_BUILDER_INSTRUCTIONS,
            "lane_b_builder": E5_LANE_B_BUILDER_INSTRUCTIONS,
            "attribution_investigator": E5_ATTRIBUTION_INSTRUCTIONS,
        },
        rubric_reference=rubric_reference,
        candidate_sha256=candidate_sha256,
        shared_draft_ref=shared_draft_ref,
        pricing=pricing,
        budget=budget,
        # Frozen BEFORE the first live call: the validated approval identity
        # the lanes will run under (an invalid approval aborts before this
        # freeze, so `validated` mirrors `provided` here).
        presentation_label_approval_record={
            "recognition_method": recognition_method,
            "provided": approval_provided,
            "validated": approval_provided,
            "target_sha256": target_sha256,
            "catalog_sha256": catalog_sha256,
            "approved_label_ids": [
                label.label_id for label in approved_label_catalog
            ],
        },
    )

    store = EvidenceStore(out_dir)
    store.register_version("structure_draft_v1", out_dir / "structure_draft.json", "shared evidence-linked draft")
    page_size = _page_pt_size(target_pdf, 1)
    shared_target_images = _overview_pngs(target_pdf, out_dir, "builder_target_shared")
    body_color_context = _target_body_text_color_context(target_pdf)
    initial_builder_evidence = _e5_builder_evidence_package(
        draft=draft,
        state=state,
        derived=derived,
        page_size=page_size,
        target_images=shared_target_images,
        presentation_labels=approved_label_catalog,
        candidate_review_scope=candidate_review_scope,
        candidate=candidate,
        initial_compact=True,
        candidate_compatible_labels=_candidate_compatible_labels(
            label_catalog, candidate_review_scope
        ),
        body_color_context=body_color_context,
    )
    header_rule_context = _header_vertical_rule_target_context(target_pdf)
    if header_rule_context is not None:
        initial_builder_evidence["header_vertical_rule_target_context"] = (
            header_rule_context
        )
    initial_builder_evidence_path = out_dir / "builder_evidence_initial.json"
    initial_builder_evidence_path.write_text(
        json.dumps(initial_builder_evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    store.register_version(
        "builder_evidence_initial_v1",
        initial_builder_evidence_path,
        "frozen common Builder evidence package",
    )

    # Scripted-reviewer rehearsal data for THIS target (same runtime rehearsal
    # pattern as E3/E4, keyed by target id; Phase-0-style intent-only request).
    mapped = [
        node for node in state.nodes
        if node.kind == "section" and node.binding
        and node.binding.mapping_action == "map"
        and "work_experience" in node.binding.sources
    ]
    if not mapped and layout_spec is None:
        return abort("scripted_reviewer_region", RuntimeError("compiled state has no mapped work_experience section"))
    if layout_spec is None:
        entry_region = mapped[0].node_id
        SCRIPTED_REGION_BY_TARGET[target_id] = entry_region
        SCRIPTED_OBSERVATION_BY_TARGET[target_id] = (
            "The vertical distance from the first dated entry head to the second "
            "dated entry head appears larger than in the target."
        )
        SCRIPTED_REQUEST_BY_TARGET[target_id] = MeasurementRequest(
            request_id="measure-pending",
            metric="role_gap",
            page=1,
            region_id=entry_region,
            intent="vertical gap between the first two dated entry heads inside this region",
        )

    lane_records: dict[str, E5LaneRecord] = {}

    def _run_lane(lane: str) -> E5LaneRecord:
        def _e5_audit(
            *,
            agent: str,
            phase: str,
            round_no: int | None,
            render_version: str | None,
            finding_ids: tuple[str, ...] = (),
            measurement_ids: tuple[str, ...] = (),
            instructions: str,
            image_refs: tuple[dict[str, Any], ...] = (),
        ) -> E5AgentAuditSpec:
            return E5AgentAuditSpec(
                agent=agent,
                lane=lane,
                phase=phase,
                round_no=round_no,
                target_version=target_id,
                render_version=render_version,
                finding_ids=finding_ids,
                measurement_ids=measurement_ids,
                model=None,
                instructions=instructions,
                image_refs=image_refs,
            )

        lane_dir = out_dir / f"lane_{lane}"
        lane_dir.mkdir(exist_ok=True)
        # The per-lane budget lives INSIDE the run budget (recorded in the
        # frozen config; a smaller caller-provided budget is respected so a
        # constrained run still exits budget_exhausted with resumable state).
        lane_budget = RunBudget(
            max_model_requests=min(E5_LANE_MAX_MODEL_REQUESTS, budget.max_model_requests // len(lanes)),
            max_tool_calls=min(E5_LANE_MAX_TOOL_CALLS, budget.max_tool_calls // len(lanes)),
            max_request_bytes=budget.max_request_bytes,
            parent=budget,
        )
        trace = RunTrace(lane_dir)
        trace.add(agent="shell", phase="e0", action="lane_started", output={"lane": lane, "live": live})
        versions: list[RenderVersion] = []
        # Explicit ACTIVE version (E5 rollback fix): the lane always reads,
        # reviews and measures the active version — never `versions[-1]`.
        # A rejected candidate render stays in `versions` as immutable
        # history but never becomes active; only a shell promotion does.
        active_index = 0
        pdf_by_version: dict[str, Path] = {}
        # version_id -> the EXACT representation (proposal/template) that
        # produced it; probes must use the selected one, never the default
        # state or a scripted fixture.
        representation_by_version: dict[str, Any] = {}
        # Version-bound mutable render state (rollback correctness): the
        # RenderPlan (Lane A) and the delivery gates belong to ONE render
        # version each. Every reader (reviewer, measurement binding,
        # builder) resolves them through the ACTIVE version id — a rejected
        # candidate never overwrites the active state.
        plan_by_version: dict[str, Any] = {}
        gates_by_version: dict[str, dict[str, Any]] = {}
        # version_id -> the RICH gate details (missing-leaf sets, per-gate
        # rows) — the repair-input derivation reuses THIS evidence verbatim;
        # `gates_by_version` holds only the boolean decisions.
        gate_details_by_version: dict[str, dict[str, Any]] = {}
        # Per-version answer to "did the privacy gate exclude exactly the
        # shared shell-owned label catalog?" — derived, never lane-specific.
        label_symmetry_by_version: dict[str, bool] = {}
        # version_id -> the shell's deterministic candidate-binding lineage
        # audit for that render (the ONLY evidence that may confirm a
        # `candidate_binding` attribution, and one input to quarantine
        # eligibility). Built once per render from that version's own leaf
        # ledger, never from the target document.
        binding_lineage_by_version: dict[str, CandidateBindingLineage] = {}
        # Presentation labels THIS lane actually referenced (Lane A: rendered
        # plan labels; Lane B: template label markers).
        referenced_labels: set[str] = set()
        # The region of the last PROMOTED repair (the only state that scopes
        # the next review round; rejected attempts never do).
        last_promoted_region: str | None = None
        last_promoted_page: int | None = None
        # QUARANTINED REPAIR BASE (owner work order 2026-09-22): the history
        # index of the last repairable-but-not-passing candidate. It is NOT a
        # second state machine — it reuses the existing version map, builder-
        # candidate audit, gate records, action fingerprints and rollback: it is
        # only "which representation the next Builder edits", while
        # `active_index` stays the reviewer/measurement/promotion/owner-package
        # scope. Cleared on promotion.
        quarantine_index: int | None = None
        findings: list[DefectFinding] = []
        measurement_results: list[MeasurementResult] = []
        attributions: list[AttributionRecord] = []
        repair_attempts: list[dict[str, Any]] = []
        builder_candidates: list[dict[str, Any]] = []
        attempted_strategies: list[str] = []
        fingerprints: list[str] = []
        resolved_measurements: dict[str, tuple[MeasurementRequest, MeasurementResult]] = {}
        rolled_back_versions: set[str] = set()
        pending_layout_trials: set[str] = set()
        ledger = DefectLedger()
        pages_reviewed: set[int] = set()
        reviewed_render_fingerprints: set[str] = set()
        counter = {"finding": 0, "request": 0}
        lane_state: dict[str, Any] = {"proposal": None, "template": None}

        def record_builder_candidate(
            candidate_output: Any,
            *,
            stage: str,
            input_render_version: str | None,
            validation: dict[str, Any],
            outcome: str,
            reason: str,
            attribution: AttributionRecord | None = None,
            action_fingerprint: str | None = None,
        ) -> int:
            attempt = len(builder_candidates) + 1
            record = _e5_builder_candidate_record(
                lane=lane,
                attempt=attempt,
                stage=stage,
                input_render_version=input_render_version,
                candidate_output=candidate_output,
                validation=validation,
                outcome=outcome,
                reason=reason,
                attribution=attribution,
                action_fingerprint=action_fingerprint,
            )
            builder_candidates.append(record)
            (lane_dir / record["artifact"]).write_text(
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return attempt - 1

        def update_builder_candidate(index: int, **updates: Any) -> None:
            builder_candidates[index].update(updates)
            (lane_dir / builder_candidates[index]["artifact"]).write_text(
                json.dumps(builder_candidates[index], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        def escalate(strategy: str) -> None:
            attempted_strategies.append(strategy)
            trace.add(agent="shell", phase="loop", action="strategy_escalation", note=strategy)

        # Loop stop state (terminal semantics fix, 2026-09-23): the lane's
        # terminal state must say WHICH ceiling was hit, or that nothing was
        # left to do — never a single catch-all. Declared before
        # `_lane_terminal` so the pre-loop failure paths can read them too.
        stop_state: str | None = None
        stop_reason = ""

        def _lane_terminal(
            lane_id: str,
            forced: str | None = None,
            reason: str = "",
        ) -> E5LaneRecord:
            # The ledger is the ONLY open/closed source: a deduplicated
            # re-observation of an already-repaired/resolved defect must not
            # resurrect it as open under a new finding id.
            open_ids = _open_ledger_finding_ids(list(ledger.entries.values()))
            # Best-valid selection FIRST: the content-shape probes must run
            # against the EXACT SELECTED representation (the best-valid
            # render's proposal/template). With no best-valid render, the
            # latest attempt gets DIAGNOSTIC probes only — never promotion
            # evidence. A QUARANTINED repair base is excluded exactly like a
            # rolled-back attempt: it was never promoted and may never be
            # selected as a render, a best, or a probe basis. Unmeasured
            # layout trials likewise require human comparison before selection.
            excluded_versions = rolled_back_versions | pending_layout_trials
            if quarantine_index is not None:
                excluded_versions.add(versions[quarantine_index].version_id)
            # Best-valid selection FIRST: the content-shape probes must run
            # against the EXACT SELECTED representation (the best-valid
            # render's proposal/template). With no best-valid render, the
            # latest attempt gets DIAGNOSTIC probes only — never promotion
            # evidence.
            best_version_id = _best_valid_version_id(versions, excluded=excluded_versions)
            defect_version_id = _best_defect_level_version_id(
                versions, excluded=excluded_versions
            )
            active_version_id = versions[active_index].version_id if versions else None
            best_version = next((v for v in versions if v.version_id == best_version_id), None)
            diagnostic_probes = best_version_id is None
            # Probes run on the lane's ACTIVE state when no best-valid (or
            # defect-level) render exists — a rejected candidate never
            # becomes the probe basis.
            selected_id = best_version_id or defect_version_id or (
                versions[active_index].version_id if versions else None
            )
            selected_representation = representation_by_version.get(selected_id)
            probes_ok = bool(probe_candidates) and bool(versions) and selected_id is not None
            probe_report: dict[str, Any] = {
                "_selected_version": selected_id,
                "_probe_mode": ("not_requested" if not probe_candidates else
                                "diagnostic" if diagnostic_probes else "promotion_evidence"),
            }
            for profile, fixture in (probe_candidates or {}).items():
                probe_candidate = _probe_candidate_with_text(fixture)
                if lane == "a":
                    probe_candidate = _sectioned_probe_candidate(probe_candidate)
                entry: dict[str, Any] = {
                    "passed": False,
                    "diagnostic": diagnostic_probes,
                    "representation": (
                        selected_representation.proposal_id
                        if lane == "a" and selected_representation is not None
                        else (
                            selected_representation.template_id
                            if selected_representation is not None
                            else ("initial_compiled_plan" if lane == "a" else None)
                        )
                    ),
                }
                probe_report[profile] = entry
                try:
                    index = next(
                        (i + 1 for i, v in enumerate(versions) if v.version_id == selected_id), 0
                    )
                    probe_html_path = lane_dir / f"probe_{profile}.html"
                    probe_pdf_path = lane_dir / f"probe_{profile}.pdf"
                    probe_pdf_second = lane_dir / f"probe_{profile}_second.pdf"
                    if lane == "a":
                        # Real chain with the SELECTED proposal: candidate
                        # binding (incl. the shared header-overflow shell
                        # transition) -> compile -> HTML -> Chrome PDF -> gates.
                        probe_plan_candidate = _apply_header_overflow_disposition(
                            probe_candidate, state
                        )
                        if layout_spec is not None:
                            _, plan, _ = render_layout_template(layout_spec, probe_plan_candidate,
                                target_sha256=target_sha256, page_count=state.page.page_count)
                            plan = _apply_lane_a_structure(plan, selected_representation)
                            html = render_html(state, plan)
                        else:
                            plan = _apply_lane_a_structure(
                                compile_render_plan(state, probe_plan_candidate), selected_representation
                            )
                            if plan.status == "failed":
                                raise RuntimeError(f"probe plan failed: {plan.failures[:3]}")
                            html = render_html(state, plan)
                        probe_html_path.write_text(html, encoding="utf-8")
                        _export_pinned_html_to_pdf(probe_html_path, probe_pdf_path, environment)
                        _export_pinned_html_to_pdf(probe_html_path, probe_pdf_second, environment)
                        pages = _render_pages(probe_pdf_path, lane_dir, f"probe_{profile}")
                        second_pages = _render_pages(probe_pdf_second, lane_dir, f"probe_{profile}_second")
                        content = c2r.content_gate(plan, html, probe_pdf_path)
                        gates = {
                            "content_gate": content["passed"],
                            "candidate_content_accounting": c2r.candidate_accounting_gate(
                                plan, content
                            )["passed"],
                            # The same COMMON privacy gate with the same
                            # shell-recognized label set as the main renders.
                            "privacy_gate": authored_privacy_gate(
                                html, probe_pdf_path, target_pdf,
                                labels=approved_label_texts, candidate=probe_candidate,
                            )["passed"],
                            "no_blank_page": c2r.blank_page_gate(probe_pdf_path)["passed"],
                            "deterministic_render": (
                                all(
                                    c2r._sha256(left) == c2r._sha256(right)
                                    for left, right in zip(pages, second_pages)
                                )
                                and c2r._line_stability(probe_pdf_path, probe_pdf_second)["passed"]
                            ),
                        }
                    else:
                        if selected_representation is None:
                            raise RuntimeError("no selected authored template to probe")
                        # Real chain with the SELECTED template: validation ->
                        # fill -> document -> network-disabled Chrome -> gates.
                        validate_authored_template(
                            selected_representation,
                            target_pdf=target_pdf,
                            render_candidate=probe_candidate,
                            known_evidence_ids=_known_evidence_ids(),
                            labels=approved_label_catalog,
                        )
                        fill = fill_authored_template(
                            selected_representation, probe_candidate,
                            labels=approved_label_catalog,
                        )
                        doc = build_authored_document(selected_representation, fill, page_size)
                        probe_html_path.write_text(doc, encoding="utf-8")
                        net_env = authored_network_disabled_environment()
                        _export_pinned_html_to_pdf(probe_html_path, probe_pdf_path, net_env)
                        pages = _render_pages(probe_pdf_path, lane_dir, f"probe_{profile}")
                        gates = {
                            "candidate_content_accounting": not fill.missing_leaves,
                            "pdf_presence": authored_pdf_presence_gate(
                                fill, probe_candidate, probe_pdf_path
                            )["passed"],
                            "privacy_gate": authored_privacy_gate(
                                doc, probe_pdf_path, target_pdf,
                                labels=approved_label_texts, candidate=probe_candidate,
                            )["passed"],
                            "no_blank_page": c2r.blank_page_gate(probe_pdf_path)["passed"],
                        }
                    entry["gates"] = gates
                    entry["pages"] = len(pages)
                    entry["html"] = probe_html_path.name
                    entry["pdf"] = probe_pdf_path.name
                    entry["passed"] = all(gates.values())
                except Exception as error:  # a probe failure is recorded, never a crash
                    entry["error"] = str(error)[:200]
                probes_ok = probes_ok and bool(entry.get("passed"))
            (lane_dir / "content_shape_probes.json").write_text(
                json.dumps(probe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            # E5 asks which representation is worth pursuing.  Once a
            # safety-valid PDF exists it belongs in owner review; open visual
            # findings, failed probes, and incomplete agent review stay visible
            # as observations instead of suppressing the output.
            terminal = (
                "ready_for_owner_review"
                if best_version_id and best_version is not None
                and best_version.hard_gates_passed
                else "budget_exhausted"
            )
            terminal_reason = ""
            if terminal != "ready_for_owner_review":
                # The loop records WHICH ceiling or stall ended the lane; the
                # for-loop completing with no recorded stop means the repair
                # round budget was reached.
                terminal = stop_state or terminal
                terminal_reason = stop_reason or "repair_round_budget"
            if forced is not None:
                terminal = forced
                terminal_reason = reason
            record = E5LaneRecord(
                lane=lane_id,
                representation=(
                    "provider-neutral structured layout + fixed renderer (Lane A)"
                    if lane_id == "a"
                    else "constrained authored HTML/CSS template + typed slots (Lane B)"
                ),
                render_versions=versions,
                best_render_version=best_version_id,
                best_defect_level_version=defect_version_id,
                active_render_version=active_version_id,
                quarantined_repair_base=(
                    versions[quarantine_index].version_id
                    if quarantine_index is not None
                    else None
                ),
                findings=findings,
                measurement_results=measurement_results,
                attributions=attributions,
                repair_attempts=repair_attempts,
                builder_candidates=builder_candidates,
                attempted_strategies=attempted_strategies,
                action_fingerprints=fingerprints,
                ledger=list(ledger.entries.values()),
                open_findings=open_ids,
                content_shape_probes_passed=probes_ok,
                pages_reviewed=sorted(pages_reviewed),
                terminal_state=terminal,
                terminal_reason=terminal_reason,
                budget_state=lane_budget.to_json(),
                summary={
                    "total_findings": len(findings),
                    "best_render_version": best_version_id,
                    "best_defect_level_version": defect_version_id,
                    "active_render_version": active_version_id,
                    "quarantined_repair_base": (
                        versions[quarantine_index].version_id
                        if quarantine_index is not None
                        else None
                    ),
                    "probe_mode": probe_report.get("_probe_mode"),
                    "pending_layout_trials": sorted(pending_layout_trials),
                    "promoted_versions": len([v for v in versions if v.promoted]),
                    "confirmed_measurements": len([r for r in measurement_results if r.status == "confirmed"]),
                    "unbound_measurements": len([r for r in measurement_results if r.status == "evidence_missing"]),
                    "builder_calls": lane_budget.calls_by_agent.get(f"lane_{lane_id}_builder", 0),
                    "reviewer_calls": lane_budget.calls_by_agent.get("visual_reviewer", 0),
                    "reviewed_pdf_sha256": sorted(reviewed_render_fingerprints),
                    "attribution_calls": lane_budget.calls_by_agent.get("attribution_investigator", 0),
                    "methodology_classification": _e5_gate_classification(
                        lane_id,
                        gates_by_version.get(active_version_id, {}),
                        privacy_labels_symmetric=label_symmetry_by_version.get(
                            active_version_id or "", True
                        ),
                        gate_details=gate_details_by_version.get(active_version_id or "", {}),
                    ),
                    "diagnostic_warnings": _e5_hard_gate_record(
                        active_version_id or "none",
                        gates_by_version.get(active_version_id, {}),
                        gate_details_by_version.get(active_version_id or "", {}),
                        blocking_gates=_e5_blocking_gates(lane_id),
                    )["diagnostic_warnings"],
                    "presentation_labels_referenced": sorted(referenced_labels),
                    "elapsed_seconds": None,
                    "live": live,
                },
            )
            (lane_dir / f"e5_lane_{lane_id}_state.json").write_text(
                json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (lane_dir / "REPORT.md").write_text(
                _write_e5_lane_report_md(lane_id, record), encoding="utf-8"
            )
            checkpoint("terminal")
            trace.save()
            return record

        # --- render plumbing ---------------------------------------------
        if lane == "a":
            def render_version(
                note: str, proposal: LaneAStructureProposal | None = None
            ) -> tuple[RenderVersion, Path, dict[str, Any]]:
                lane_budget.spend_tool("render_and_checkpoint")
                if template_render is not None:
                    _, plan, html = template_render
                    if proposal is not None:
                        plan = _apply_lane_a_structure(plan, proposal)
                        html = render_html(state, plan)
                else:
                    plan = _apply_lane_a_structure(compile_render_plan(state, candidate), proposal)
                    if plan.status == "failed":
                        raise RuntimeError(f"refusing to render a failed plan: {plan.failures[:3]}")
                    html = render_html(state, plan)
                index = len(versions) + 1
                html_path = lane_dir / f"render_{index}.html"
                html_path.write_text(html, encoding="utf-8")
                pdf_path = lane_dir / f"render_{index}.pdf"
                _export_pinned_html_to_pdf(html_path, pdf_path, environment)
                second = lane_dir / f"render_{index}_second.pdf"
                _export_pinned_html_to_pdf(html_path, second, environment)
                pages = _render_pages(pdf_path, lane_dir, f"render_{index}")
                second_pages = _render_pages(second, lane_dir, f"render_{index}_second")
                stability = c2r._line_stability(pdf_path, second)
                content = c2r.content_gate(plan, html, pdf_path)
                # DIAGNOSTIC ONLY (see gate_details): the renderer gate keeps
                # excluding plan-derived labels; the privacy DECISION is the
                # common gate below.
                privacy = c2r.privacy_gate(plan, target_pdf, html, pdf_path)
                # The ONE common privacy gate: identical function and identical
                # shell-recognized label set for BOTH lanes, so the two lanes'
                # privacy decisions are directly comparable.
                common_privacy = authored_privacy_gate(
                    html, pdf_path, target_pdf, labels=approved_label_texts, candidate=candidate
                )
                structure_gate = c2r.structure_gate(plan, state, html, pdf_path)
                blank = c2r.blank_page_gate(pdf_path)
                accounting = c2r.candidate_accounting_gate(plan, content)
                shape = c2r.content_shape_verification(
                    state, plan, body_scaffold, bullet_tiers, html, summary, pdf_path
                )
                if layout_spec is not None:
                    shape["basis"] = "layout_template_spec; consumption checks only, not visual parity"
                gates = {
                    "deterministic_render": all(
                        c2r._sha256(left) == c2r._sha256(right)
                        for left, right in zip(pages, second_pages)
                    ) and stability["passed"],
                    # The COMMON privacy decision: the same gate function and
                    # the same shell-recognized label set Lane B uses.
                    "no_target_candidate_facts": common_privacy["passed"],
                    "section_order_matches_state": structure_gate["section_order_matches_state"],
                    "no_blank_page": blank["passed"],
                    "candidate_content_accounting": accounting["passed"],
                    "content_shapes_match_evidence": shape["passed"],
                    "content_gate": content["passed"],
                }
                label_semantics = _e5_label_semantics(
                    common_privacy, approved_label_catalog
                )
                label_symmetry_by_version[f"render-{target_pdf.stem}-laneA-v{index}"] = bool(
                    label_semantics["symmetric"]
                )
                referenced_labels.update(
                    label.label_id
                    for label in label_catalog
                    if c2r._norm(label.text)
                    in {
                        c2r._norm(section.label)
                        for section in [*plan.sections, *plan.appended_sections]
                    }
                )
                gate_details = {
                    "deterministic_render": {
                        "passed": gates["deterministic_render"],
                        "first_page_hashes": [c2r._sha256(page) for page in pages],
                        "second_page_hashes": [c2r._sha256(page) for page in second_pages],
                        "line_stability": stability,
                    },
                    "no_target_candidate_facts": {
                        **common_privacy,
                        "label_semantics": label_semantics,
                        # DIAGNOSTIC ONLY: the renderer gate keeps excluding
                        # plan-derived labels; it is no longer the privacy
                        # decision and never used to claim symmetry.
                        "plan_derived_privacy_gate": privacy,
                    },
                    "section_order_matches_state": structure_gate,
                    "no_blank_page": blank,
                    "candidate_content_accounting": accounting,
                    "content_shapes_match_evidence": shape,
                    "content_gate": content,
                }
                version = RenderVersion(
                    version_id=f"render-{target_pdf.stem}-laneA-v{index}",
                    html_sha256=_sha256_file(html_path),
                    pdf_sha256=_sha256_file(pdf_path),
                    page_count=len(pages),
                    hard_gates_passed=_e5_blocking_gates_passed("a", gates),
                    note=note,
                )
                versions.append(version)
                pdf_by_version[version.version_id] = pdf_path
                representation_by_version[version.version_id] = proposal
                plan_by_version[version.version_id] = plan
                gates_by_version[version.version_id] = gates
                gate_details_by_version[version.version_id] = gate_details
                binding_lineage_by_version[version.version_id] = _e5_candidate_binding_lineage(
                    candidate,
                    version.version_id,
                    content.get("leaf_records") or {},
                    representation_ceiling_gates=[
                        name for name in E5_REPRESENTATION_CEILING_GATES
                        if gates.get(name) is False
                    ],
                )
                store.register_version(version.version_id, pdf_path, note)
                (lane_dir / f"hard_gates_{version.version_id}.json").write_text(
                    json.dumps(
                        _e5_hard_gate_record(
                            version.version_id,
                            gates,
                            gate_details,
                            blocking_gates=_e5_blocking_gates("a"),
                        ),
                        indent=2,
                        sort_keys=True,
                    ) + "\n",
                    encoding="utf-8",
                )
                trace.add(agent="shell", phase="render", action="render_version", output=version.model_dump(mode="json"), note=note)
                return version, pdf_path, gates
        else:
            def render_version(
                note: str, template: AuthoredTemplateCandidate | None = None
            ) -> tuple[RenderVersion, Path, dict[str, Any]]:
                lane_budget.spend_tool("render_and_checkpoint")
                template = template or lane_state["template"]
                validate_authored_template(
                    template,
                    target_pdf=target_pdf,
                    render_candidate=candidate,
                    known_evidence_ids=_known_evidence_ids(),
                    labels=approved_label_catalog,
                )
                fill = fill_authored_template(
                    template, candidate, labels=approved_label_catalog
                )
                index = len(versions) + 1
                doc = build_authored_document(template, fill, page_size)
                html_path = lane_dir / f"render_{index}.html"
                html_path.write_text(doc, encoding="utf-8")
                pdf_path = lane_dir / f"render_{index}.pdf"
                net_env = authored_network_disabled_environment()
                _export_pinned_html_to_pdf(html_path, pdf_path, net_env)
                pages = _render_pages(pdf_path, lane_dir, f"render_{index}")
                presence = authored_pdf_presence_gate(fill, candidate, pdf_path)
                # The SAME common gate Lane A runs: identical gate function and
                # identical shell-recognized label set, so both lanes' privacy
                # decisions are directly comparable.
                common_privacy = authored_privacy_gate(
                    doc, pdf_path, target_pdf, labels=approved_label_texts, candidate=candidate
                )
                label_semantics = _e5_label_semantics(
                    common_privacy, approved_label_catalog
                )
                referenced_labels.update(presentation_label_markers(template.html))
                # DIAGNOSTIC ONLY: the frozen-target copy gate under the same
                # approved label set (not the privacy decision).
                privacy_baseline_gate = authored_privacy_gate(
                    doc, pdf_path, target_pdf, labels=approved_label_texts
                )
                blank = c2r.blank_page_gate(pdf_path)
                gates = {
                    "no_target_candidate_facts": common_privacy["passed"],
                    "candidate_content_accounting": not fill.missing_leaves,
                }
                gate_details = {
                    "no_target_candidate_facts": {
                        **common_privacy,
                        "label_semantics": label_semantics,
                        "target_privacy_diagnostic": privacy_baseline_gate,
                    },
                    "no_blank_page": blank,
                    "candidate_content_accounting": {
                        "passed": not fill.missing_leaves,
                        "missing_leaves": fill.missing_leaves,
                        "leaf_counts": fill.leaf_counts,
                    },
                    "diagnostics": {
                        "pdf_presence": presence,
                        "no_blank_page": blank,
                    },
                }
                version = RenderVersion(
                    version_id=f"render-{target_pdf.stem}-laneB-v{index}",
                    html_sha256=_sha256_file(html_path),
                    pdf_sha256=_sha256_file(pdf_path),
                    page_count=len(pages),
                    hard_gates_passed=_e5_blocking_gates_passed("b", gates),
                    note=note,
                )
                versions.append(version)
                pdf_by_version[version.version_id] = pdf_path
                representation_by_version[version.version_id] = template
                gates_by_version[version.version_id] = gates
                gate_details_by_version[version.version_id] = gate_details
                binding_lineage_by_version[version.version_id] = _e5_candidate_binding_lineage(
                    candidate,
                    version.version_id,
                    {leaf_id: {} for leaf_id in fill.leaf_counts},
                )
                label_symmetry_by_version[version.version_id] = bool(
                    label_semantics["symmetric"]
                )
                store.register_version(version.version_id, pdf_path, note)
                (lane_dir / f"hard_gates_{version.version_id}.json").write_text(
                    json.dumps(
                        _e5_hard_gate_record(
                            version.version_id,
                            gates,
                            gate_details,
                            blocking_gates=_e5_blocking_gates("b"),
                        ),
                        indent=2,
                        sort_keys=True,
                    ) + "\n",
                    encoding="utf-8",
                )
                trace.add(agent="shell", phase="render", action="render_version", output=version.model_dump(mode="json"), note=note)
                return version, pdf_path, gates

        def render_entry_texts(region_id: str | None, plan: Any = None) -> tuple[list[str], list[str]]:
            # The plan is always the version-bound plan of the render being
            # measured (the ACTIVE plan for a round measurement, the
            # candidate's own plan for the identical re-measurement) — never
            # a mutable lane-wide variable that a rejected render could
            # overwrite.
            if lane == "a" and plan is not None:
                plan_section = next(
                    (s for s in [*plan.sections, *plan.appended_sections] if s.node_id == _owning_section_id(state, region_id)),
                    None,
                )
                if plan_section is not None and plan_section.entries:
                    heads = [entry.title_lines[0] for entry in plan_section.entries if entry.title_lines]
                    head_ids = {line.leaf_id for line in heads}
                    other_fields = [field.text for entry in plan_section.entries for row in entry.rows or []
                                    for field in [*row.left, *row.right] if field.leaf_id not in head_ids]
                    return [line.text for line in heads], other_fields
            # generic fallback: the candidate's own entry heads (same role)
            return [
                leaf.text or "" for leaf in candidate.leaves
                if leaf.kind in {"work_entry", "education_entry"} and leaf.text
            ][:4], []

        def execute_measurement(
            finding: DefectFinding,
            pdf: Path,
            request_id: str,
            *,
            plan: Any = None,
            render_version: str = "",
        ) -> tuple[MeasurementResult, MeasurementRequest]:
            section_id = _owning_section_id(state, finding.region)
            span = region_spans.get(section_id or finding.region)
            span_tuple = (span[1], span[2]) if span and span[0] == finding.page else None
            trace.add(
                agent="measure_controller", phase="measure", action="binding",
                input={"render_version": render_version, "finding": finding.finding_id},
            )
            request = finding.requested_measurement.model_copy(update={"request_id": request_id})
            if request.metric == "unsupported":
                return MeasureController(pod, lane_budget, trace).execute(request, current_pdf=pdf), request
            dimension = finding.suspected_dimension.casefold()
            compound = bool(re.search(r"\b(and|plus)\b", dimension))
            if compound or (request.metric == "role_gap" and not any(
                word in dimension for word in ("gap", "spacing", "role")
            )):
                result = MeasurementResult(
                    request_id=request_id, status="not_measurable",
                    reason=("compound dimensions need separate measurements" if compound else
                            f"dimension {finding.suspected_dimension!r} is not supported by the role_gap metric"),
                    method="reviewer_metric_binding/1",
                )
                trace.add(agent="measure_controller", phase="measure", action="binding_unresolved",
                          output=result.model_dump(mode="json"), persist_output=True)
                return result, request
            if request.metric == "header_vertical_rule_geometry":
                finding_text = (
                    f"{finding.region} {finding.suspected_dimension} {finding.observation} "
                    f"{request.intent or ''}"
                ).casefold()
                if not (
                    finding.region.startswith("header")
                    and any(word in finding_text for word in ("header", "name"))
                    and "vertical" in finding_text
                    and any(word in finding_text for word in ("rule", "line", "bar"))
                ):
                    result = MeasurementResult(
                        request_id=request_id,
                        status="not_measurable",
                        reason="metric may bind only to an explicit header vertical-rule finding",
                        method=HEADER_VERTICAL_RULE_MEASUREMENT_VERSION,
                    )
                else:
                    lane_budget.spend_tool("measure_header_vertical_rule_geometry")
                    result = _measure_header_vertical_rule_geometry(
                        request,
                        target_pdf=target_pdf,
                        current_pdf=pdf,
                        current_name_text=candidate_name_text,
                    )
                trace.add(
                    agent="measure_controller", phase="measure", action="measurement",
                    tool="measure_header_vertical_rule_geometry",
                    output=result.model_dump(mode="json"), persist_output=True,
                )
                return result, request
            heads, other_fields = render_entry_texts(finding.region, plan)
            bound = _bind_role_gap_anchors(
                request,
                target_pdf=target_pdf,
                current_pdf=pdf,
                region_span=span_tuple,
                render_leaf_texts=heads, render_non_head_texts=other_fields,
            )
            if isinstance(bound, MeasurementResult):
                trace.add(
                    agent="measure_controller", phase="measure", action="binding_unresolved",
                    note=bound.reason, persist_output=True,
                )
                return bound, request
            # MeasureController.execute is the SINGLE tool-budget consumer for
            # a deterministic measurement (one compare_pdf_geometry per call,
            # one trace record). Callers never spend or re-trace it.
            result = MeasureController(pod, lane_budget, trace).execute(bound, current_pdf=pdf)
            return result, bound

        def attribute(
            finding: DefectFinding, result: MeasurementResult, request: MeasurementRequest
        ) -> AttributionRecord:
            attribution = _e5_default_attribution(finding, result, request)
            attributions.append(attribution)
            # Single source of defect state: the attribution (and its
            # measurement request) is written back to the owning ledger
            # entry; a no_defect measurement closes the entry as resolved.
            _record_ledger_attribution(ledger, finding, attribution, request.request_id)
            trace.add(
                agent="attribution_investigator", phase="attribute", action="attribution",
                output=attribution.model_dump(mode="json"),
            )
            return attribution

        def accepted_regions_hold(candidate_pdf: Path) -> tuple[bool, list[dict[str, Any]]]:
            """FAIL CLOSED accepted-region recheck: an accepted region is held
            ONLY when the identical re-measurement is CONFIRMED on the
            candidate PDF with all measured values present and the error did
            not grow beyond the prior error + tolerance. Any evidence_missing
            / unmeasurable / vanished anchor / missing value means the
            candidate CANNOT be verified to hold the region -> not held."""
            rechecks: list[dict[str, Any]] = []
            for finding_id, (request, prior) in resolved_measurements.items():
                finding = findings_by_id[finding_id]
                repeat, _ = execute_measurement(
                    finding.model_copy(update={"requested_measurement": request}),
                    candidate_pdf,
                    request.request_id,
                    plan=plan_by_version.get(versions[-1].version_id),
                    render_version=versions[-1].version_id,
                )
                values_present = (
                    repeat.current_value_pt is not None
                    and repeat.target_value_pt is not None
                    and prior.current_value_pt is not None
                    and prior.target_value_pt is not None
                )
                held = (
                    repeat.status == "confirmed"
                    and values_present
                    and abs(repeat.current_value_pt - repeat.target_value_pt)
                    <= abs(prior.current_value_pt - prior.target_value_pt) + E2_IMPROVEMENT_TOLERANCE_PT
                )
                reason = None
                if not held:
                    if repeat.status != "confirmed":
                        reason = f"re-measurement not confirmed on the candidate PDF (status={repeat.status})"
                    elif not values_present:
                        reason = "re-measurement left measured values missing (vanished anchor / evidence gap)"
                    else:
                        reason = "accepted region error grew beyond the prior error + tolerance"
                rechecks.append(
                    {
                        "finding": finding_id,
                        "held": held,
                        "status": repeat.status,
                        **({"reason": reason} if reason else {}),
                    }
                )
                if not held:
                    return False, rechecks
            return True, rechecks

        def checkpoint(phase: str) -> None:
            """Small atomic recovery snapshot plus machine-readable residuals."""
            current = versions[active_index].version_id if versions else None
            payload = {
                "schema_version": "e5-checkpoint/1",
                "phase": phase,
                "lane": lane,
                "active_render_version": current,
                "render_versions": [item.model_dump(mode="json") for item in versions],
                "findings": [item.model_dump(mode="json") for item in findings],
                "ledger": [item.model_dump(mode="json") for item in ledger.entries.values()],
                "builder_candidates": builder_candidates,
                "budget_state": lane_budget.to_json(),
            }
            path = lane_dir / "checkpoint.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)

            findings_by_key = {_ledger_key(item): item for item in findings}
            residuals = []
            for key, entry in ledger.entries.items():
                finding = findings_by_key.get(key)
                if finding is None:
                    continue
                repair = next(
                    (
                        item for item in reversed(builder_candidates)
                        if (item.get("trigger_attribution") or {}).get("finding_id")
                        == finding.finding_id
                    ),
                    None,
                )
                attribution = entry.attribution
                residuals.append({
                    "schema_version": "e5-residual/1",
                    "residual_type": _residual_type(finding),
                    "region_role": finding.region,
                    "status": entry.status,
                    "finding_id": finding.finding_id,
                    "render_version": finding.render_version,
                    "observation": finding.observation,
                    "repair_attempted": repair is not None,
                    "repair_outcome": repair.get("outcome") if repair else None,
                    "resolution_layer": (
                        "representation" if attribution and attribution.repair_owner == "builder"
                        else attribution.repair_owner if attribution else None
                    ),
                })
            residual_path = lane_dir / "residuals.jsonl"
            residual_tmp = residual_path.with_suffix(".tmp")
            residual_tmp.write_text(
                "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in residuals),
                encoding="utf-8",
            )
            residual_tmp.replace(residual_path)

        # --- 3. Initial Builder output (representation under test) ---------
        initial_note = f"lane {lane} initial render ({'authored template' if lane == 'b' else 'structured layout'})"
        candidate_audit_index: int | None = None
        try:
            if lane == "a":
                proposal = None
                if live:
                    proposal = _live_lane_a_builder(
                        lane_budget,
                        trace,
                        payload=initial_builder_evidence,
                        images=shared_target_images,
                        proposal_id="lane-a-r1",
                        audit=_e5_audit(
                            agent="lane_a_builder",
                            phase="initial",
                            round_no=0,
                            render_version=None,
                            instructions=E5_LANE_A_BUILDER_INSTRUCTIONS,
                            image_refs=_e5_image_refs(shared_target_images, "target"),
                        ),
                    )
                    validation_error = _validate_lane_a_proposal(proposal, state)
                    candidate_audit_index = record_builder_candidate(
                        proposal,
                        stage="initial",
                        input_render_version=None,
                        validation={"passed": validation_error is None, "error": validation_error},
                        outcome="validator_rejected" if validation_error else "validated",
                        reason=validation_error or "typed proposal accepted by the shell validator",
                    )
                    if validation_error:
                        raise ValueError(validation_error)
                lane_state["proposal"] = proposal
                lane_state["active_proposal"] = proposal
                version, pdf, gates = render_version(initial_note, proposal)
                if candidate_audit_index is not None:
                    update_builder_candidate(
                        candidate_audit_index,
                        candidate_render_version=version.version_id,
                        outcome="active_unpromoted",
                        reason="initial validated candidate rendered and became the active version",
                    )
            else:
                template = None
                if live:
                    template = _live_lane_b_builder(
                        lane_budget,
                        trace,
                        payload=initial_builder_evidence,
                        images=shared_target_images,
                        template_id="lane-b-r1",
                        audit=_e5_audit(
                            agent="lane_b_builder",
                            phase="initial",
                            round_no=0,
                            render_version=None,
                            instructions=E5_LANE_B_BUILDER_INSTRUCTIONS,
                            image_refs=_e5_image_refs(shared_target_images, "target"),
                        ),
                    )
                else:
                    from app.pipeline.authored_template import SCRIPTED_AUTHORED_TEMPLATE

                    template = SCRIPTED_AUTHORED_TEMPLATE
                try:
                    validation = validate_authored_template(
                        template,
                        target_pdf=target_pdf,
                        render_candidate=candidate,
                        known_evidence_ids=_known_evidence_ids(),
                        labels=approved_label_catalog,
                    )
                    validation_error = None
                except ValueError as error:
                    validation = {"passed": False, "error": str(error)}
                    validation_error = str(error)
                candidate_audit_index = record_builder_candidate(
                    template,
                    stage="initial",
                    input_render_version=None,
                    validation=validation,
                    outcome="validator_rejected" if validation_error else "validated",
                    reason=validation_error or "typed template accepted by the shell validator",
                )
                if validation_error:
                    raise ValueError(validation_error)
                lane_state["template"] = template
                lane_state["active_template"] = template
                version, pdf, gates = render_version(initial_note, template)
                update_builder_candidate(
                    candidate_audit_index,
                    candidate_render_version=version.version_id,
                    outcome="active_unpromoted",
                    reason="initial validated candidate rendered and became the active version",
                )
        except CheckpointBudgetExceeded:
            escalate("builder_initial_budget_exhausted")
            return _lane_terminal(lane, "budget_exhausted", reason="builder_initial_budget")
        except Exception as error:
            import traceback as _tb
            detail = _tb.format_exc()[-600:]
            if candidate_audit_index is not None and builder_candidates[candidate_audit_index]["outcome"] != "validator_rejected":
                update_builder_candidate(
                    candidate_audit_index,
                    outcome="render_failed",
                    reason=str(error)[:300],
                )
            lane_state["last_rejection"] = f"initial render failed: {str(error)[:200]}"
            trace.add(agent="shell", phase="builder", action="initial_failed", note=str(error)[:200], output={"traceback": detail}, persist_output=True)
            escalate(f"lane_builder_initial_failed:{type(error).__name__}")
            return _lane_terminal(lane, "budget_exhausted", reason="builder_initial_failed")
        checkpoint("initial_render")
        if layout_spec is not None and not live:
            # Offline previews execute the supplied template without a model loop.
            return _lane_terminal(lane)

        layout_trial_regions = {
            node.node_id for node in state.nodes
            if live and lane == "a" and _owning_section_id(state, node.node_id) is not None
        }
        # --- 4. See -> Measure -> Attribute -> Repair -> Re-render loop -----
        # Scheduling state (repair-starvation fix, 2026-09-21 owner work order):
        # a render fingerprint is fully reviewed AT MOST ONCE; each round's
        # attribution backlog is bounded; the Builder is called as soon as a
        # confirmed, attributed, builder-owned defect bound to the ACTIVE
        # version exists — it never waits for the whole open-findings ledger.
        # Review scheduling state: the fingerprint is the ACTIVE render's
        # FINAL-PDF sha256 (owner correction 2026-09-22). version_id ->
        # pdf hash is also recorded: two different version ids with the SAME
        # hash do not re-review, and the SAME version_id can never map to a
        # different PDF (conflict fails closed).
        version_pdf_hashes: dict[str, str] = {}
        gate_repair_versions_seen: set[str] = set()
        findings_by_id: dict[str, DefectFinding] = {}
        # finding_id -> (finding, result, bound) for every measurement taken on
        # any version; the repair scan filters by the ACTIVE version.
        measured_by_finding: dict[str, tuple[DefectFinding, MeasurementResult, MeasurementRequest]] = {}
        for round_no in range(1, max_repair_rounds + 2):
            try:
                if lane_budget.remaining_model_requests() < 1:
                    trace.add(agent="shell", phase="loop", action="budget_exhausted", note="before review")
                    stop_state, stop_reason = "budget_exhausted", "model_request_budget"
                    break
                # The round reviews/measures the ACTIVE version (the last
                # PROMOTED render, or the initial render before any
                # promotion) — never the newest render in history.
                current_version = versions[active_index]
                current_pdf = pdf_by_version[current_version.version_id]
                current_plan = plan_by_version.get(current_version.version_id)
                current_gates = gates_by_version.get(current_version.version_id, {})
                current_gate_details = gate_details_by_version.get(current_version.version_id, {})
                # The repair base is the quarantined candidate when one exists
                # (its own representation/version/gates/missing leaves), and
                # the ACTIVE version otherwise. Reviewer, measurement binding,
                # promotion and the owner package always resolve against the
                # ACTIVE version; the only thing that may look at the quarantine
                # is the hard-gate repair input and the Builder payload that
                # continues fixing it.
                repair_base_version = (
                    versions[quarantine_index] if quarantine_index is not None else current_version
                )
                repair_base_gates = gates_by_version.get(repair_base_version.version_id, {})
                repair_base_details = gate_details_by_version.get(repair_base_version.version_id, {})
                repair_scope_versions = {
                    current_version.version_id, repair_base_version.version_id,
                }
                # The next round's changed-region scope comes ONLY from the
                # last PROMOTED repair: a rejected/rolled-back attempt is
                # immutable history (diagnostic), never review scope.
                changed_regions: list[str] = (
                    [last_promoted_region] if last_promoted_region else []
                )
                findings_new: list[DefectFinding] = []
                # SCHEDULING GATE: the SAME render fingerprint gets at most ONE
                # full Reviewer pass. With no new render there is NO review
                # this round: the round works on the bounded deferred backlog
                # or calls the Builder for an already-attributed repair
                # candidate — it never re-reviews an unchanged render.
                # Review gate: fingerprint = final-PDF sha256. A conflicting
                # re-hash for a known version_id fails closed.
                render_fingerprint = current_version.pdf_sha256
                known_pdf_hash = version_pdf_hashes.get(current_version.version_id)
                if known_pdf_hash is not None and known_pdf_hash != render_fingerprint:
                    raise RuntimeError(
                        f"render identity conflict: {current_version.version_id} "
                        f"previously hashed {known_pdf_hash}, now {render_fingerprint}"
                    )
                version_pdf_hashes[current_version.version_id] = render_fingerprint
                run_review = _e5_review_this_round(reviewed_render_fingerprints, render_fingerprint)
                trace.add(
                    agent="shell", phase="loop", action="review_gate",
                    output={
                        "render_version": current_version.version_id,
                        "pdf_sha256": render_fingerprint,
                        "review_executed": run_review,
                        "skip_reason": (
                            None if run_review
                            else "same render pdf_sha256 already fully reviewed"
                        ),
                    },
                )
                reviewer_failed = False
                try:
                    if live and run_review:
                        target_overviews = _overview_pngs(target_pdf, out_dir, f"review_target_l{lane}_{round_no}")
                        render_overviews = _overview_pngs(current_pdf, out_dir, f"review_render_l{lane}_{round_no}")
                        scope = _review_scope_payload(
                            round_no,
                            changed_regions,
                            findings,
                            ledger_statuses={
                                entry.finding_id: entry.status
                                for entry in ledger.entries.values()
                            },
                            changed_pages=[last_promoted_page] if last_promoted_page else [],
                        )
                        region_catalog = _e5_region_catalog(state, derived)
                        presentation_facts = _reviewer_presentation_facts(
                            target_pdf, current_pdf, derived
                        )
                        counter["finding"] += 1
                        node_inventory = json.dumps(
                            {
                                "region_catalog": region_catalog,
                                "review_scope": scope,
                                "candidate_review_scope": candidate_review_scope,
                            },
                            ensure_ascii=False,
                        )
                        # Bounded images per call: one PAGE per reviewer call
                        # (target overview + render overview), merged across pages.
                        review_page_count = (
                            len(render_overviews)
                            if layout_spec is not None else len(target_overviews)
                        )
                        for page_index in range(1, review_page_count + 1):
                            target_page_number = min(page_index, len(target_overviews))
                            target_page = prepare_review_image(target_overviews[target_page_number - 1])
                            lane_images = [target_page]
                            if page_index <= len(render_overviews):
                                lane_images.append(prepare_review_image(render_overviews[page_index - 1]))
                            counter["finding"] += 1
                            page_findings = _with_connection_retry(
                                lambda lane_images=lane_images, page_index=page_index, target_page_number=target_page_number: _live_reviewer_findings(
                                    lane_images,
                                    finding_id=f"finding-l{lane}-r{round_no}-p{page_index}",
                                    target_version=target_id,
                                    render_version=current_version.version_id,
                                    page=page_index,
                                    node_inventory=node_inventory,
                                    budget=lane_budget,
                                    trace=trace,
                                    id_prefix=f"finding-l{lane}-r{round_no}-p{page_index}",
                                    instructions=E5_REVIEWER_INSTRUCTIONS,
                                    visual_model=True,
                                    audit=_e5_audit(
                                        agent="visual_reviewer",
                                        phase="review",
                                        round_no=round_no,
                                        render_version=current_version.version_id,
                                        finding_ids=(f"finding-l{lane}-r{round_no}-p{page_index}",),
                                        instructions=E5_REVIEWER_INSTRUCTIONS,
                                        image_refs=(
                                            _e5_image_refs([target_page], "target", first_page=target_page_number)
                                            + _e5_image_refs(
                                                [lane_images[1]], "render", first_page=page_index
                                            )
                                            if len(lane_images) > 1
                                            else _e5_image_refs([target_page], "target", first_page=target_page_number)
                                        ),
                                    ),
                                    message=(
                                        _e5_reviewer_message(
                                            target_version=target_id,
                                            render_version=current_version.version_id,
                                            page=page_index,
                                            region_catalog=region_catalog,
                                            review_scope=scope,
                                            candidate_review_scope=candidate_review_scope,
                                            lane=lane,
                                            deterministic_presentation_facts=presentation_facts,
                                            image_pages=(
                                                {"target": target_page_number,
                                                 "render": page_index if page_index <= len(render_overviews) else None}
                                                if layout_spec is not None else None
                                            ),
                                        )
                                    ),
                                ),
                                trace=trace, what=f"visual reviewer page {page_index}",
                            )
                            findings_new.extend(_validate_reviewer_regions(
                                page_findings,
                                {item["region_id"] for item in region_catalog},
                                page_index,
                            ))
                            pages_reviewed.add(page_index)
                    elif not live:
                        counter["finding"] += 1
                        prompt = f"TARGET {target_frozen.case_id} RENDER {current_version.version_id} page 1."
                        findings_new = ScriptedReviewer().run(
                            prompt,
                            finding_id=f"finding-l{lane}-r{round_no}",
                            target_version=target_id,
                            render_version=current_version.version_id,
                            page=1,
                            budget=lane_budget,
                            trace=trace,
                            target_id=target_id,
                        )
                        pages_reviewed.add(1)
                except CheckpointBudgetExceeded:
                    escalate("reviewer_budget_exhausted")
                    stop_state, stop_reason = "budget_exhausted", "reviewer_budget"
                    break
                except Exception as error:
                    import traceback as _tb2
                    trace.add(
                        agent="shell", phase="loop", action="reviewer_failed",
                        output={"traceback": _tb2.format_exc()[-4000:]}, persist_output=True,
                    )
                    escalate(f"reviewer_live_call_failed:{type(error).__name__}: {str(error)[:200]}")
                    reviewer_failed = True
                if run_review and not reviewer_failed:
                    reviewed_render_fingerprints.add(render_fingerprint)
                findings_new = _validate_finding_versions(findings_new, target_id, current_version.version_id)
                filtered = [
                    {
                        "finding": finding.model_dump(mode="json"),
                        "reason": reason,
                    }
                    for finding in findings_new
                    if (reason := _candidate_boundary_filter_reason(
                        finding, candidate_review_scope
                    )) is not None
                ]
                findings_new = _candidate_aware_findings(findings_new, candidate_review_scope)
                if filtered:
                    trace.add(
                        agent="shell",
                        phase="review",
                        action="candidate_boundary_filtered",
                        output={"removed": len(filtered), "findings": filtered},
                        persist_output=True,
                    )
                findings.extend(findings_new)
                for finding in findings_new:
                    findings_by_id[finding.finding_id] = finding

                # Hard-gate-derived builder-owned repair inputs: recorded ONCE
                # per render version, from the shell's OWN gate evidence, and
                # pre-attributed (no live attribution needed). A
                # candidate-accounting/content failure enters repair BEFORE
                # any visual attribution backlog (owner work order §四).
                gate_items: list[
                    tuple[DefectFinding, MeasurementResult, MeasurementRequest, AttributionRecord]
                ] = []
                if repair_base_version.version_id not in gate_repair_versions_seen:
                    gate_repair_versions_seen.add(repair_base_version.version_id)
                    gate_items = _e5_gate_repair_items(
                        lane, target_id, repair_base_version.version_id, repair_base_details,
                        candidate=candidate,
                        current_pdf=pdf_by_version[repair_base_version.version_id],
                    )
                for gate_finding, gate_result, gate_request, gate_attribution in gate_items:
                    findings_by_id[gate_finding.finding_id] = gate_finding
                    findings.append(gate_finding)
                    measured_by_finding[gate_finding.finding_id] = (
                        gate_finding, gate_result, gate_request,
                    )
                    entry, action = ledger.observe(gate_finding)
                    trace.add(
                        agent="shell", phase="ledger", action=action,
                        output={"key": entry.ledger_key, "finding": gate_finding.finding_id},
                    )
                    attributions.append(gate_attribution)
                    _record_ledger_attribution(
                        ledger, gate_finding, gate_attribution, gate_request.request_id,
                    )

                # Ledger dedup BEFORE attribution (Phase 0).
                actionable: list[DefectFinding] = []
                carried_open_used = False
                for finding in findings_new:
                    resolved = finding.finding_id in resolved_measurements
                    entry, action = ledger.observe(finding, resolved=resolved)
                    trace.add(
                        agent="shell", phase="ledger", action=action,
                        output={"key": entry.ledger_key, "finding": finding.finding_id},
                    )
                    if action == "dedup" and entry.status in {"resolved", "repaired", "attributed"}:
                        continue  # unchanged finding with unchanged evidence: no re-attribution
                    if action == "dedup":
                        if entry.last_measured_version == current_version.version_id:
                            continue  # already measured on THIS render version
                        if finding.region not in changed_regions or carried_open_used:
                            continue  # unchanged region: the stored measurement stays the record
                        carried_open_used = True  # one bounded carried-open re-measure
                    actionable.append(finding)
                if run_review and not reviewer_failed:
                    checkpoint("review_complete")

                # Bounded per-round scheduling: this round's new findings
                # first, then previously deferred open findings; at most
                # E5_MAX_ROUND_ATTRIBUTION_FINDINGS enter this round's
                # attribution backlog. The rest stay in the ledger as
                # `deferred` — open, never lost, re-eligible in later rounds
                # under the SAME rule (both lanes run this identical logic).
                # Builder FIRST (scheduling guarantee, owner work order §三.6):
                # if a confirmed, attributed, builder-owned defect bound to the
                # ACTIVE version already exists (e.g. attributed in an earlier
                # round, or a hard-gate repair input), the Builder is called
                # THIS ROUND — before any full review and before the
                # attribution backlog expands by even one finding.
                pre_scan_candidates = [
                    finding
                    for finding, _result, _bound in measured_by_finding.values()
                    if finding.render_version in repair_scope_versions
                ]
                pre_scan_measured = {
                    finding.finding_id: (result, bound)
                    for finding, result, bound in measured_by_finding.values()
                    if finding.render_version in repair_scope_versions
                }
                pre_repair, _pre_repeated = _next_e5_repair_finding(
                    pre_scan_candidates, pre_scan_measured, attributions, fingerprints,
                    layout_trial_regions=layout_trial_regions,
                )
                skip_attribution = pre_repair is not None

                deferred_candidates: list[DefectFinding] = [
                    findings_by_id[entry.finding_id]
                    for entry in ledger.entries.values()
                    if entry.status == "deferred"
                    and entry.finding_id in findings_by_id
                    and findings_by_id[entry.finding_id].render_version
                    == current_version.version_id
                ]
                if skip_attribution:
                    selected, deferred_now = [], []
                else:
                    # Gate repair inputs do NOT enter the round backlog: they
                    # are pre-attributed and reached the pre-repair scan above
                    # (the Builder-first guarantee); the bounded cap is for
                    # reviewer findings.
                    selected, deferred_now = _e5_select_round_work(
                        actionable, [], deferred_candidates
                    )
                for deferred_finding in deferred_now:
                    entry = ledger.entries.get(_ledger_key(deferred_finding))
                    if entry is None or entry.status != "open":
                        continue
                    entry.status = "deferred"
                    trace.add(
                        agent="shell", phase="ledger", action="deferred",
                        output={"finding": deferred_finding.finding_id},
                        note=(
                            f"not selected this round (cap "
                            f"{E5_MAX_ROUND_ATTRIBUTION_FINDINGS}); stays open and "
                            "re-eligible"
                        ),
                    )

                # Bind -> measure -> attribute for the SELECTED findings only.
                measured: dict[str, tuple[MeasurementResult, MeasurementRequest]] = {}
                for finding in selected:
                    counter["request"] += 1
                    request_id = f"measure-l{lane}-{counter['request']:03d}"
                    result, bound = execute_measurement(
                        finding, current_pdf, request_id,
                        plan=current_plan, render_version=current_version.version_id,
                    )
                    measurement_results.append(result)
                    measured[finding.finding_id] = (result, bound)
                    measured_by_finding[finding.finding_id] = (finding, result, bound)
                    ledger.entries[_ledger_key(finding)].last_measured_version = current_version.version_id
                batches = _e5_attribution_batches(selected, measured)
                if live and lane != "b" and batches and _builder_reserve_intact(lane_budget, builder_reserve_requests):
                    pod.render_pdf = current_pdf
                    for group in batches:
                        try:
                            hypotheses = _live_attribution_batch(
                                pod, lane_budget, trace,
                                findings=group,
                                results=[measured[f.finding_id][0] for f in group],
                                lane=lane,
                                current_representation=representation_by_version.get(current_version.version_id),
                                compiled_layout=_lane_a_attribution_layout(current_plan, state, group),
                                audit=_e5_audit(
                                    agent="attribution_investigator",
                                    phase="attribute",
                                    round_no=round_no,
                                    render_version=current_version.version_id,
                                    finding_ids=tuple(item.finding_id for item in group),
                                    measurement_ids=tuple(
                                        measured[item.finding_id][1].request_id for item in group
                                    ),
                                    instructions=E5_ATTRIBUTION_INSTRUCTIONS,
                                ),
                            )
                            # Identity binding, never list position: a swapped,
                            # missing, duplicated, or foreign finding_id rejects
                            # the WHOLE batch (it contributes nothing).
                            bound_hypotheses = _bind_live_attribution_batch(group, hypotheses)
                            for finding, hypothesis in bound_hypotheses:
                                request_id = measured[finding.finding_id][1].request_id
                                attribution = _e5_live_attribution_record(
                                    finding,
                                    hypothesis,
                                    request_id,
                                    lineage=binding_lineage_by_version.get(
                                        finding.render_version
                                    ),
                                )
                                attributions.append(attribution)
                                _record_ledger_attribution(
                                    ledger, finding, attribution, request_id,
                                )
                                trace.add(
                                    agent="attribution_investigator", phase="attribute",
                                    action="live_attribution", output=attribution.model_dump(mode="json"),
                                )
                        except CheckpointBudgetExceeded:
                            escalate("attribution_budget_exhausted")
                            stop_state, stop_reason = "budget_exhausted", "attribution_budget"
                            break
                        except LiveAttributionBindingError as error:
                            # Fail closed WITH an auditable reason: the batch's
                            # findings fall through to the deterministic
                            # attribution instead of a positional guess.
                            escalate(f"attribution_batch_binding_rejected:{error}")
                            trace.add(
                                agent="shell", phase="attribute",
                                action="attribution_batch_binding_rejected",
                                output={
                                    "batch_findings": [item.finding_id for item in group],
                                    "reason": str(error),
                                },
                            )
                        except Exception as error:
                            escalate(f"attribution_live_call_failed:{type(error).__name__}: {str(error)[:150]}")
                if lane == "b":
                    for finding in _e5_findings_needing_fallback_attribution(
                        selected, measured, attributions
                    ):
                        result, bound = measured[finding.finding_id]
                        attribution = _e5_lane_b_attribution(finding, result, bound)
                        attributions.append(attribution)
                        _record_ledger_attribution(
                            ledger, finding, attribution, bound.request_id,
                        )
                        trace.add(
                            agent="shell",
                            phase="attribute",
                            action="lane_b_direct_attribution",
                            output=attribution.model_dump(mode="json"),
                        )
                for finding in _e5_findings_needing_fallback_attribution(
                    selected, measured, attributions
                ):
                    result, bound = measured[finding.finding_id]
                    attribute(finding, result, bound)

                # `max_repair_rounds=0` is review-only. The old `+ 2` loop
                # envelope accidentally allowed one repair even with a zero
                # repair budget.
                if round_no > max_repair_rounds:
                    stop_state, stop_reason = "budget_exhausted", "repair_round_budget"
                    break

                # Builder opportunity (SCHEDULING GUARANTEE): the scan covers
                # EVERY defect bound to the ACTIVE version — this round's and
                # previously attributed ones — so the Builder is called as
                # soon as ONE confirmed, attributed, builder-owned defect
                # exists. Live A may also try a bounded layout hypothesis;
                # improvement is established only after rendering and rechecks.
                scan_candidates = [
                    finding
                    for finding, _result, _bound in measured_by_finding.values()
                    if finding.render_version in repair_scope_versions
                ]
                scan_measured = {
                    finding.finding_id: (result, bound)
                    for finding, result, bound in measured_by_finding.values()
                    if finding.render_version in repair_scope_versions
                }
                repair_finding, all_repairable_repeated = _next_e5_repair_finding(
                    scan_candidates, scan_measured, attributions, fingerprints,
                    layout_trial_regions=layout_trial_regions,
                )
                if repair_finding is None and all_repairable_repeated:
                    escalate("stalled_no_new_action")
                    # Nothing is left to DO (every repairable action
                    # fingerprint was already executed) — that is a distinct
                    # state from a real budget ceiling.
                    stop_state, stop_reason = "stalled_no_new_action", "stalled_no_new_action"
                    break
                if repair_finding is None:
                    actionable_keys = {
                        (finding.finding_id, finding.render_version) for finding in scan_candidates
                    }
                    awaiting = any(
                        (item.finding_id, item.render_version) in actionable_keys
                        and item.attribution != "no_defect"
                        for item in attributions
                    )
                    # Termination (no infinite no-op rounds): with no new
                    # render, no review, and no pending backlog work, another
                    # round would deterministically repeat this state.
                    work_this_round = bool(findings_new) or bool(selected) or bool(gate_items)
                    if not work_this_round:
                        escalate(f"round{round_no}:no_new_render_no_pending_work")
                        stop_state, stop_reason = "stalled_no_new_action", "no_new_render_no_pending_work"
                        break
                    escalate(
                        "awaiting_attribution_or_other_owner"
                        if awaiting
                        else f"round{round_no}:no_repairable_bound_finding_continue_review"
                    )
                    continue
                finding, result, bound, attribution, fingerprint = repair_finding
                # The Builder repairs the REPAIR BASE: with a quarantine that is
                # the quarantined candidate's own render images, and its own
                # representation/render version/gates are bound in the payload
                # below. Without a quarantine the base IS the active version, so
                # this is the pre-existing behavior unchanged.
                current_render_images = _overview_pngs(
                    pdf_by_version[repair_base_version.version_id],
                    lane_dir,
                    f"builder_render_r{round_no}",
                )
                repair_target_images = shared_target_images
                repair_render_images = current_render_images
                if (
                    lane == "b"
                    and finding.requested_measurement.metric
                    == "header_vertical_rule_geometry"
                ):
                    repair_target_images = [
                        _header_crop_from_overview_source(
                            shared_target_images[0],
                            lane_dir / f"builder_target_header_r{round_no}.png",
                        )
                    ]
                    repair_render_images = [
                        _header_crop_from_overview_source(
                            current_render_images[0],
                            lane_dir / f"builder_render_header_r{round_no}.png",
                        )
                    ]
                repair_builder_evidence = _e5_builder_evidence_package(
                    draft=draft,
                    state=state,
                    derived=derived,
                    page_size=page_size,
                    current_render_version=repair_base_version.version_id,
                    findings=[finding] if lane == "b" else scan_candidates,
                    measurements=[result] if lane == "b" else [
                        scan_measured[item.finding_id][0] for item in scan_candidates
                    ],
                    current_gates=repair_base_gates,
                    last_rejection=lane_state.get("last_rejection"),
                    target_images=repair_target_images,
                    current_render_images=repair_render_images,
                    current_representation=representation_by_version[
                        repair_base_version.version_id
                    ],
                    selected_attribution=attribution,
                    action_fingerprint=fingerprint,
                    presentation_labels=approved_label_catalog,
                    candidate_review_scope=candidate_review_scope,
                    candidate=candidate,
                    body_color_context=body_color_context,
                    missing_leaf_ids=list(
                        repair_base_details.get("candidate_content_accounting", {}).get(
                            "missing_leaves"
                        )
                        or []
                    ),
                    compact=lane == "b",
                )
                if lane == "a":
                    repair_builder_evidence["repair_policy"] = {
                        "mode": "bounded_layout_trial",
                        "instruction": "Attribution is a hypothesis, not permission. Try only existing typed layout primitives; preserve candidate facts. Unmeasured changes require human comparison and cannot be automatically selected.",
                        "compiled_layout": _lane_a_attribution_layout(current_plan, state, [finding]),
                    }
                (lane_dir / f"builder_evidence_round_{round_no:02d}.json").write_text(
                    json.dumps(
                        repair_builder_evidence,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ) + "\n",
                    encoding="utf-8",
                )
                candidate_audit_index = None
                try:
                    if lane == "a":
                        if live:
                            proposal = _live_lane_a_builder(
                                lane_budget,
                                trace,
                                payload=repair_builder_evidence,
                                images=[*shared_target_images, *current_render_images],
                                proposal_id=f"lane-a-r{round_no + 1}",
                                audit=_e5_audit(
                                    agent="lane_a_builder",
                                    phase="repair",
                                    round_no=round_no,
                                    render_version=repair_base_version.version_id,
                                    finding_ids=(finding.finding_id,),
                                    measurement_ids=(bound.request_id,),
                                    instructions=E5_LANE_A_BUILDER_INSTRUCTIONS,
                                    image_refs=(
                                        _e5_image_refs(shared_target_images, "target")
                                        + _e5_image_refs(current_render_images, "render")
                                    ),
                                ),
                            )
                        else:
                            proposal = _scripted_lane_a_proposal(state, derived)
                        validation_error = _validate_lane_a_proposal(proposal, state)
                        candidate_audit_index = record_builder_candidate(
                            proposal,
                            stage="repair",
                            input_render_version=repair_base_version.version_id,
                            validation={"passed": validation_error is None, "error": validation_error},
                            outcome="validator_rejected" if validation_error else "validated",
                            reason=validation_error or "typed proposal accepted by the shell validator",
                            attribution=attribution,
                            action_fingerprint=fingerprint,
                        )
                        if validation_error:
                            repair_attempts.append({"finding": finding.finding_id, "region": finding.region, "rejected": validation_error, "round": round_no})
                            trace.add(agent="shell", phase="repair", action="rejected", note=validation_error)
                            continue
                        repair_attempts.append(
                            {"finding": finding.finding_id, "region": finding.region, "round": round_no,
                             "agent": proposal.agent, "sections": len(proposal.sections)}
                        )
                        candidate_version, candidate_pdf, candidate_gates = render_version(
                            f"lane A repair round {round_no} for {finding.finding_id}", proposal
                        )
                    else:
                        if live:
                            template = _live_lane_b_builder(
                                lane_budget, trace, payload=repair_builder_evidence,
                                images=[*repair_target_images, *repair_render_images],
                                template_id=f"lane-b-r{round_no + 1}",
                                audit=_e5_audit(
                                    agent="lane_b_builder",
                                    phase="repair",
                                    round_no=round_no,
                                    render_version=repair_base_version.version_id,
                                    finding_ids=(finding.finding_id,),
                                    measurement_ids=(bound.request_id,),
                                    instructions=E5_LANE_B_BUILDER_INSTRUCTIONS,
                                    image_refs=(
                                        _e5_image_refs(repair_target_images, "target")
                                        + _e5_image_refs(repair_render_images, "render")
                                    ),
                                ),
                            )
                        else:
                            from app.pipeline.authored_template import SCRIPTED_AUTHORED_TEMPLATE as _base_template

                            # The offline Builder continues from the REPAIR BASE's
                            # own representation (the quarantined candidate when
                            # one exists), never from a fixed fixture.
                            template = _scripted_lane_b_template(
                                representation_by_version.get(
                                    repair_base_version.version_id
                                )
                                or _base_template,
                                finding,
                                result,
                            )
                        try:
                            validation = validate_authored_template(
                                template,
                                target_pdf=target_pdf,
                                render_candidate=candidate,
                                known_evidence_ids=_known_evidence_ids(),
                                labels=approved_label_catalog,
                            )
                        except ValueError as error:
                            validation = {"passed": False, "error": str(error)}
                            candidate_audit_index = record_builder_candidate(
                                template,
                                stage="repair",
                                input_render_version=repair_base_version.version_id,
                                validation=validation,
                                outcome="validator_rejected",
                                reason=str(error),
                                attribution=attribution,
                                action_fingerprint=fingerprint,
                            )
                            lane_state["last_rejection"] = str(error)[:280]
                            repair_attempts.append({"finding": finding.finding_id, "region": finding.region, "rejected": str(error)[:300], "round": round_no})
                            trace.add(agent="shell", phase="repair", action="rejected", note=str(error)[:300])
                            continue
                        candidate_audit_index = record_builder_candidate(
                            template,
                            stage="repair",
                            input_render_version=repair_base_version.version_id,
                            validation=validation,
                            outcome="validated",
                            reason="typed template accepted by the shell validator",
                            attribution=attribution,
                            action_fingerprint=fingerprint,
                        )
                        repair_attempts.append(
                            {"finding": finding.finding_id, "region": finding.region,
                             "round": round_no, "template": template.template_id}
                        )
                        candidate_version, candidate_pdf, candidate_gates = render_version(
                            f"lane B repair round {round_no} for {finding.finding_id}", template
                        )
                    assert candidate_audit_index is not None
                    update_builder_candidate(
                        candidate_audit_index,
                        candidate_render_version=candidate_version.version_id,
                        outcome="rendered_pending_decision",
                        reason="validated candidate rendered; awaiting shell promotion or rollback",
                    )
                    checkpoint("repair_rendered")
                except CheckpointBudgetExceeded:
                    escalate("builder_budget_exhausted")
                    stop_state, stop_reason = "budget_exhausted", "builder_budget"
                    break
                except Exception as error:
                    if candidate_audit_index is not None:
                        update_builder_candidate(
                            candidate_audit_index,
                            outcome="render_failed",
                            reason=str(error)[:300],
                        )
                    escalate(f"repair_render_failed:{type(error).__name__}: {str(error)[:150]}")
                    continue
                lane_budget.repair_attempt_count += 1
                fingerprints.append(fingerprint)  # records the EXECUTED action
                candidate_index = len(versions) - 1  # the candidate's history index
                trace.add(
                    agent="builder", phase="repair", action="proposal",
                    note=f"round {round_no} on repair base {repair_base_version.version_id}",
                    persist_output=True,
                    output={
                        "current_version": repair_base_version.version_id,
                        "active_version": current_version.version_id,
                        "quarantined": quarantine_index is not None,
                        "current_gates": repair_base_gates,
                    },
                )
                # Re-measure FIRST: the IDENTICAL request, changing only the version.
                # A hard-gate repair input re-measures the SAME shell gate on
                # the candidate (the gate already re-ran during the candidate
                # render); no reviewer measurement channel is invented.
                counter["request"] += 1
                repeat_id = bound.request_id
                if finding.suspected_dimension == "hard_gate_content":
                    candidate_gate_details = (
                        gate_details_by_version.get(candidate_version.version_id, {}).get(
                            "candidate_content_accounting"
                        )
                        or {}
                    )
                    candidate_missing = len(
                        candidate_gate_details.get("missing_leaves")
                        or []
                    )
                    repeat_result = MeasurementResult(
                        request_id=repeat_id,
                        status="confirmed",
                        target_value_pt=0.0,
                        current_value_pt=float(candidate_missing),
                        delta_pt=float(candidate_missing),
                        method="candidate_content_accounting_missing/1",
                        warnings=[
                            "the identical hard-gate record re-measured on the "
                            "candidate render (target baseline 0)"
                        ],
                    )
                else:
                    repeat_result, _ = execute_measurement(
                        finding, candidate_pdf, repeat_id,
                        plan=plan_by_version.get(candidate_version.version_id),
                        render_version=candidate_version.version_id,
                    )
                measurement_results.append(repeat_result)
                improved, improvement_reason = _measurement_improvement(result, repeat_result)
                if not improved:
                    unmeasured_trial = (
                        lane == "a" and result.status in {"ambiguous", "evidence_missing", "not_measurable"}
                        and all(candidate_gates.get(name) is True for name in LANE_A_OUTPUT_GATES)
                        and not binding_lineage_by_version[candidate_version.version_id].proofs
                        and candidate_version.html_sha256 != repair_base_version.html_sha256
                    )
                    if lane == "a" and result.status != "confirmed":
                        failed = [name for name in LANE_A_OUTPUT_GATES if candidate_gates.get(name) is not True]
                        if failed:
                            improvement_reason = f"unmeasured trial failed output gate(s): {failed}"
                    if unmeasured_trial:
                        holds, rechecks = accepted_regions_hold(candidate_pdf)
                        if holds:
                            pending_layout_trials.add(candidate_version.version_id)
                            update_builder_candidate(candidate_audit_index, outcome="awaiting_owner_review",
                                reason="Unmeasured layout trial passed output gates and accepted-region checks; not automatically selected or claimed improved")
                            trace.add(agent="shell", phase="repair", action="awaiting_owner_review",
                                output={"version": candidate_version.version_id, "rechecks": rechecks}, persist_output=True)
                            continue
                        improvement_reason = "unmeasured trial failed accepted-region recheck"
                    rolled_back_versions.add(candidate_version.version_id)
                    update_builder_candidate(
                        candidate_audit_index,
                        outcome="rolled_back",
                        reason=improvement_reason,
                    )
                    attempted_strategies.append(f"round{round_no}:{finding.finding_id}:non_improving_rolled_back")
                    trace.add(
                        agent="shell", phase="repair", action="rolled_back",
                        note=f"non-improving repair: {improvement_reason}",
                    )
                    continue
                gate_keys = _e5_blocking_gates(lane)
                failing_gates = [
                    gate for gate in gate_keys if candidate_gates.get(gate) is not True
                ]
                # A red candidate is no longer thrown away wholesale: the shell
                # decides between an unsafe REJECTION and a QUARANTINED repair
                # base. The decision is deterministic and uses existing evidence
                # only (gate records, builder-candidate audit, the per-version
                # candidate-binding lineage audit).
                quarantinable, quarantine_reason = (
                    _e5_quarantine_eligibility(
                        candidate_version,
                        candidate_gates,
                        representation=representation_by_version.get(
                            candidate_version.version_id
                        ),
                        binding_lineage=binding_lineage_by_version.get(
                            candidate_version.version_id
                        ),
                        candidate_audit=(
                            builder_candidates[candidate_audit_index]
                            if candidate_audit_index is not None
                            else None
                        ),
                    )
                    if failing_gates
                    else (False, "candidate passed every hard gate")
                )
                if failing_gates and not quarantinable:
                    rolled_back_versions.add(candidate_version.version_id)
                    update_builder_candidate(
                        candidate_audit_index,
                        outcome="rolled_back",
                        reason=f"not quarantinable: {quarantine_reason}",
                    )
                    attempted_strategies.append(
                        f"round{round_no}:{finding.finding_id}:rejected_not_quarantinable_rolled_back"
                    )
                    # The rollback note names the ACTUAL failing gates (the old
                    # blanket "candidate-safety gate failed" mislabeled a pure
                    # content-gate failure as a safety failure).
                    trace.add(
                        agent="shell", phase="repair", action="rolled_back",
                        output={
                            "candidate_version": candidate_version.version_id,
                            "failing_gates": failing_gates,
                            "reason": quarantine_reason,
                        },
                        note=f"not quarantinable: failing gates {failing_gates}; {quarantine_reason}",
                    )
                    continue
                holds, rechecks = accepted_regions_hold(candidate_pdf)
                if not holds:
                    rolled_back_versions.add(candidate_version.version_id)
                    update_builder_candidate(
                        candidate_audit_index,
                        outcome="rolled_back",
                        reason="accepted-region recheck failed closed",
                    )
                    attempted_strategies.append(f"round{round_no}:{finding.finding_id}:accepted_region_regressed_rolled_back")
                    trace.add(agent="shell", phase="repair", action="rolled_back", note="accepted region regressed")
                    continue
                if failing_gates:
                    # QUARANTINE (owner work order 2026-09-22): the candidate is
                    # repairable but not passing. It becomes the NEXT repair
                    # base ONLY — never promoted, never best, never the lane's
                    # owner-facing output; `active_index` still points at the
                    # accepted active version.
                    previous_base = (
                        versions[quarantine_index].version_id
                        if quarantine_index is not None
                        else current_version.version_id
                    )
                    quarantine_index = candidate_index
                    update_builder_candidate(
                        candidate_audit_index,
                        outcome="quarantined",
                        reason=f"repairable-but-not-passing: {quarantine_reason}",
                    )
                    attempted_strategies.append(
                        f"round{round_no}:{finding.finding_id}:quarantined_repair_base"
                    )
                    trace.add(
                        agent="shell", phase="repair", action="quarantined",
                        output={
                            "candidate_version": candidate_version.version_id,
                            "previous_repair_base": previous_base,
                            "active_version": current_version.version_id,
                            "failing_gates": failing_gates,
                            "reason": quarantine_reason,
                        },
                        note=(
                            f"quarantined repair base {candidate_version.version_id} "
                            f"(failing gates {failing_gates}); active version "
                            f"{current_version.version_id} unchanged"
                        ),
                        persist_output=True,
                    )
                    continue
                # PROMOTION (shell-only): the candidate becomes the new ACTIVE
                # version; the rejected candidates before it stay in the
                # immutable history and are never implicitly selected again. A
                # promoted version supersedes any quarantined repair base.
                quarantine_index = None
                versions[candidate_index] = candidate_version.model_copy(update={"promoted": True})
                update_builder_candidate(
                    candidate_audit_index,
                    outcome="promoted",
                    reason="shell verified improvement, safety gates, and accepted-region hold",
                )
                active_index = candidate_index
                last_promoted_region = finding.region
                last_promoted_page = finding.page
                if lane == "a":
                    lane_state["active_proposal"] = proposal
                else:
                    lane_state["active_template"] = template
                resolved_measurements[finding.finding_id] = (bound, repeat_result)
                repaired_entry = ledger.entries[_ledger_key(finding)]
                repaired_entry.status = "repaired"
                repaired_entry.measurement_request_id = repeat_result.request_id
                trace.add(
                    agent="shell", phase="repair", action="promoted",
                    output={"version": candidate_version.version_id, "rechecks": rechecks},
                    persist_output=True,
                )

            except (CheckpointBudgetExceeded, BudgetExhausted) as budget_error:
                escalate("loop_tool_budget_exhausted")
                trace.add(agent="shell", phase="loop", action="budget_exhausted", note=str(budget_error)[:200])
                stop_state, stop_reason = "budget_exhausted", "tool_call_budget"
                break
        return _lane_terminal(lane)

    # -- 5. Run the lanes (fresh contexts; lane B never sees lane A output) --
    for lane in lanes:
        started_lane = time.time()
        record = _run_lane(lane)
        record.summary["elapsed_seconds"] = round(time.time() - started_lane, 1)
        record.summary["estimated_provider_cost"] = _estimate_lane_cost(record, pricing)
        lane_records[lane] = record

    # -- 6. Terminal state + comparison + owner package ----------------------
    # The run-level state follows the LANES: owner-ready only when every lane
    # is; otherwise it says whether the run stopped because a ceiling was hit
    # or because no lane had an action left (the 2026-09-23 mislabel: a stalled
    # Lane B with 11 of 400 requests spent reported as `budget_exhausted`).
    blocked_lanes = [
        record for record in lane_records.values()
        if record.terminal_state != "ready_for_owner_review"
    ]
    if lane_records and not blocked_lanes:
        overall_terminal = "ready_for_owner_review"
    elif blocked_lanes and all(
        record.terminal_state == "stalled_no_new_action" for record in blocked_lanes
    ):
        overall_terminal = "stalled_no_new_action"
    else:
        overall_terminal = "budget_exhausted"
    # Source identity check: if any registered direct runtime source changed
    # mid-run, the run records `source_changed` and is NOT source-identity
    # stable. `source_identity_stable` says ONLY that the source did not
    # drift — it never promotes an offline rehearsal to a canonical run.
    source_changed = _source_hashes_changed(config)
    loop_record = E5LoopRecord(
        target_id=target_id,
        target_sha256=target_frozen.target_sha256,
        lanes={lane: rec for lane, rec in lane_records.items()},
        budget_state=budget.to_json(),
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
        summary={
            "terminal_state": overall_terminal,
            "live": live,
            "elapsed_seconds": round(time.time() - started, 1),
            "lanes": {lane: r.terminal_state for lane, r in lane_records.items()},
            "lane_terminal_reasons": {
                lane: r.terminal_reason for lane, r in lane_records.items()
            },
            "source_changed": source_changed,
            "source_identity_stable": not source_changed,
            "presentation_label_approval": {
                "recognition_method": recognition_method,
                "target_sha256": target_sha256,
                "catalog_sha256": catalog_sha256,
                "approval_provided": approval_provided,
                "approval_validated": approval_provided,
                "approved_label_ids": [
                    label.label_id for label in approved_label_catalog
                ],
                "proposed_label_ids": [
                    label.label_id for label in label_catalog
                    if label.label_id not in {a.label_id for a in approved_label_catalog}
                ],
            },
        },
    )
    (out_dir / "e5_state.json").write_text(
        json.dumps(loop_record.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    store.record_state(overall_terminal, f"lanes={ {k: v.terminal_state for k, v in lane_records.items()} }")
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_e5_comparison(out_dir, loop_record, config)
    try:
        _write_e5_owner_package(
            out_dir,
            target_pdf,
            loop_record,
            label_catalog,
            approved_label_ids=[label.label_id for label in approved_label_catalog],
            target_sha256=target_sha256,
            catalog_sha256=catalog_sha256,
            # an invalid approval aborts before the package exists, so any
            # package that reached this point validated a provided approval
            approval_provided=approval_provided,
            approval_validated=approval_provided,
        )
    except Exception as error:
        (out_dir / "owner_package_failed.txt").write_text(str(error), encoding="utf-8")
    return out_dir, overall_terminal, loop_record.model_dump(mode="json")

def _write_e5_lane_report_md(lane_id: str, record: E5LaneRecord) -> str:
    versions_table = "\n".join(
        f"| `{v.version_id}` | {v.hard_gates_passed} | {v.promoted} | {v.note} |"
        for v in record.render_versions
    ) or "| - | - | - | - |"
    findings_table = "\n".join(
        f"| `{f.finding_id}` | {f.region} | {f.suspected_dimension} | {f.observation[:70]} |"
        for f in record.findings
    ) or "| - | - | - | - |"
    ledger_table = "\n".join(
        f"| `{e.finding_id}` | {e.status} | {e.first_seen_version} -> {e.last_seen_version} |"
        for e in record.ledger
    ) or "| - | - | - |"
    probes_note = (
        "DIAGNOSTIC probes on the selected non-best attempt (no best-valid "
        "render; NOT promotion evidence)" if record.summary.get("probe_mode") == "diagnostic"
        else "promotion-evidence probes against the selected best-valid representation"
    )
    stop_reason = (
        f" (stop reason: {record.terminal_reason})" if record.terminal_reason else ""
    )
    return f"""# Pipeline E5 Lane {lane_id.upper()} — {record.representation}

- Terminal state: **{record.terminal_state}**{stop_reason} (never owner acceptance)
- Best render: `{record.best_render_version or 'NONE — no best-valid render exists'}`
- Active render: `{record.active_render_version}`
- Defect-level promoted (never BEST): `{record.best_defect_level_version or 'none'}`
- Quarantined repair base (the next Builder's input; never promoted, never
  BEST, never the lane's output): `{record.quarantined_repair_base or 'none'}`
- Findings: {record.summary.get('total_findings')}; confirmed measurements:
  {record.summary.get('confirmed_measurements')}; unbound:
  {record.summary.get('unbound_measurements')}
- Builder calls: {record.summary.get('builder_calls')}; reviewer:
  {record.summary.get('reviewer_calls')}; attribution:
  {record.summary.get('attribution_calls')}
- Tokens (live): {record.budget_state.get('usage', {}).get('input_tokens', 0)}/
  {record.budget_state.get('usage', {}).get('output_tokens', 0)}
- Diagnostic warnings: {', '.join(record.summary.get('diagnostic_warnings') or []) or 'none'}
- Probes: {record.content_shape_probes_passed} — {probes_note}

## Render versions

| version | gates | promoted | note |
| --- | --- | --- | --- |
{versions_table}

## Findings

| finding | region | dimension | observation |
| --- | --- | --- | --- |
{findings_table}

## Defect ledger (Phase 0)

| finding | status | seen |
| --- | --- | --- |
{ledger_table}

This is an experiment record; automated metrics declare no winner and no
convergence. The owner reviews the actual files and decides.
"""

def summarize_e5_state(e5_state_path: Path) -> dict[str, Any]:
    """Deterministic summary of ONE E5 run from its `e5_state.json` (the
    ledger/report data source): every reported number is read from that
    single run record, so reports can never splice findings, measurements,
    calls, probes or costs from different runs. Companion helper for
    `E_PIPELINE_PREP.md` and run reports."""
    state = json.loads(Path(e5_state_path).read_text(encoding="utf-8"))
    lanes: dict[str, Any] = {}
    for lane_id, lane in state.get("lanes", {}).items():
        summary = lane.get("summary", {})
        budget_state = lane.get("budget_state", {})
        lanes[lane_id] = {
            "terminal_state": lane.get("terminal_state"),
            "terminal_reason": lane.get("terminal_reason"),
            "best_render_version": lane.get("best_render_version"),
            "active_render_version": lane.get("active_render_version"),
            "render_versions": len(lane.get("render_versions", [])),
            "promoted_versions": summary.get("promoted_versions"),
            "findings": summary.get("total_findings"),
            "confirmed_measurements": summary.get("confirmed_measurements"),
            "unbound_measurements": summary.get("unbound_measurements"),
            "builder_calls": summary.get("builder_calls"),
            "reviewer_calls": summary.get("reviewer_calls"),
            "attribution_calls": summary.get("attribution_calls"),
            "model_calls": budget_state.get("model_request_count"),
            "model_calls_by_mode": budget_state.get("calls_by_mode"),
            "tool_calls": budget_state.get("tool_call_count"),
            "tool_calls_by_tool": budget_state.get("calls_by_tool"),
            "input_tokens": budget_state.get("usage", {}).get("input_tokens", 0),
            "output_tokens": budget_state.get("usage", {}).get("output_tokens", 0),
            "probes_passed": lane.get("content_shape_probes_passed"),
            "probe_mode": summary.get("probe_mode"),
        }
    return {
        "run_id": Path(e5_state_path).parent.name,
        "source": str(e5_state_path),
        "terminal_state": state.get("summary", {}).get("terminal_state"),
        "source_changed": state.get("summary", {}).get("source_changed"),
        "source_identity_stable": state.get("summary", {}).get("source_identity_stable"),
        "lanes": lanes,
    }

def _selected_lane_artifact(
    lane: "E5LaneRecord",
) -> tuple[RenderVersion | None, str, str]:
    """Resolve the owner-facing lane artifact through the explicit version
    state (never `len(render_versions)` / `versions[-1]` silently). Returns
    (version, file stem, label):

    - ``BEST`` — only a real hard-gate-valid best render;
    - ``ACTIVE DEFECT-LEVEL VERSION`` — the defect-level promoted active
      render (local improvement state, explicitly NOT best);
    - ``ACTIVE UNPROMOTED VERSION`` — the lane's current ACTIVE render when
      it never earned promotion (never BEST, never a rejected attempt);
    - ``LATEST ATTEMPT`` — only when NO active version exists at all.

    A rolled-back candidate is never selected: it is immutable history, not
    the lane's current output.
    """
    def _stem(version_id: str) -> tuple[RenderVersion | None, str]:
        for index, version in enumerate(lane.render_versions):
            if version.version_id == version_id:
                return version, f"render_{index + 1}"
        return None, ""

    if lane.best_render_version:
        version, stem = _stem(lane.best_render_version)
        if version is not None:
            return version, stem, "BEST"
    if lane.best_defect_level_version:
        version, stem = _stem(lane.best_defect_level_version)
        if version is not None:
            return version, stem, "ACTIVE DEFECT-LEVEL VERSION"
    if lane.active_render_version:
        version, stem = _stem(lane.active_render_version)
        if version is not None:
            return version, stem, "ACTIVE UNPROMOTED VERSION"
    if lane.render_versions:
        return lane.render_versions[-1], f"render_{len(lane.render_versions)}", "LATEST ATTEMPT"
    return None, "", "LATEST ATTEMPT"

def _write_e5_comparison(
    out_dir: Path, record: E5LoopRecord, config: dict[str, Any]
) -> None:
    """The lane comparison report: NO aggregate similarity percentage, NO
    automated winner declaration — comparable counts + recorded costs only."""
    rows = []
    for lane_id, lane in record.lanes.items():
        summary = lane.summary
        # Page count comes from the SELECTED artifact (safety-valid render, or
        # the clearly-labeled latest attempt when none exists) — never from
        # `render_versions[-1]` silently.
        selected, _stem, label = _selected_lane_artifact(lane)
        rows.append(
            {
                "lane": lane_id,
                "representation": lane.representation,
                "terminal_state": lane.terminal_state,
                "terminal_reason": lane.terminal_reason,
                "best_render": lane.best_render_version,
                "best_defect_level_version": lane.best_defect_level_version,
                "active_render": lane.active_render_version,
                "quarantined_repair_base": lane.quarantined_repair_base,
                "render_label": label,
                "selected_version": selected.version_id if selected else None,
                "selected_pages": selected.page_count if selected else None,
                "findings": summary.get("total_findings"),
                "confirmed_measurements": summary.get("confirmed_measurements"),
                "measurement_binding_failures": summary.get("unbound_measurements"),
                "attribution_calls": summary.get("attribution_calls"),
                "builder_calls": summary.get("builder_calls"),
                "improving_repairs": summary.get("promoted_versions"),
                "rollbacks": len([s for s in lane.attempted_strategies if "rolled_back" in s]),
                "probes_passed": lane.content_shape_probes_passed,
                "diagnostic_warnings": summary.get("diagnostic_warnings", []),
                "probe_mode": summary.get("probe_mode"),
                "input_tokens": lane.budget_state.get("usage", {}).get("input_tokens", 0),
                "output_tokens": lane.budget_state.get("usage", {}).get("output_tokens", 0),
                "tool_calls": lane.budget_state.get("tool_call_count"),
                "elapsed_seconds": summary.get("elapsed_seconds"),
                "estimated_provider_cost": summary.get("estimated_provider_cost"),
                # Not measured in this run: recorded as not_evaluated, never
                # as a zero (a zero would claim a measurement that never ran).
                "target_specific_code": None,
                "target_specific_code_status": "not_evaluated",
                "shared_backend_code": {
                    "lane_a": "c2_plan.SectionPlan rail fields + c2_html rail branch",
                    "lane_b": "app/pipeline/authored_template.py",
                }.get("lane_a" if lane_id == "a" else "lane_b"),
            }
        )
    comparison = {
        "run_id": record.summary.get("run_id", out_dir.name),
        "frozen_config": config.get("run_id"),
        "source_changed": record.summary.get("source_changed"),
        "lanes": rows,
        "decision": (
            "Automated metrics declare NO winner (plan §13). The owner compares "
            "the owner_review/ artifacts visually and decides."
        ),
    }
    (out_dir / "comparison_report.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    table = "\n".join(
        f"| {row['lane']} | {row['terminal_state']}"
        f"{(' (' + row['terminal_reason'] + ')') if row.get('terminal_reason') else ''} "
        f"| {row['best_render'] or 'none'} "
        f"({row['render_label']}) | {row['findings']} | {row['confirmed_measurements']} | "
        f"{row['builder_calls']} | {row['improving_repairs']} | "
        f"{', '.join(row['diagnostic_warnings']) or 'none'} | "
        f"{row['input_tokens']}/{row['output_tokens']} | {row['elapsed_seconds']} |"
        for row in rows
    )
    (out_dir / "COMPARISON.md").write_text(
        f"""# Pipeline E5 Builder-representation comparison — {out_dir.name}

- Terminal state: **{record.summary.get('terminal_state')}**
- Per-lane stop reason: {", ".join(
  f"{lane}={state} ({record.summary.get('lane_terminal_reasons', {}).get(lane) or 'not recorded'})"
  for lane, state in (record.summary.get('lanes') or {}).items()
) or 'not recorded'} (`budget_exhausted` means a configured ceiling WAS hit;
  `stalled_no_new_action` means the loop had no action left)
- Question: which Builder representation gives the Agent a practical path
  toward convergence WITHOUT target-specific backend code?
- This report declares NO winner; the owner decides from `owner_review/`.
- BEST labels mark a safety-valid render; non-blocking checks remain visible
  as diagnostics. ACTIVE DEFECT-LEVEL VERSION marks a promoted active
  render when no safety-valid best
  exists (a local improvement, never BEST); ACTIVE UNPROMOTED VERSION marks
  the lane's current active render that never earned promotion; a
  rolled-back attempt is never shown as the lane's output.

| lane | terminal | best render | findings | confirmed | builder calls | improving repairs | diagnostics | tokens in/out | elapsed s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
{table}

## Non-claims

- No Pipeline E convergence claim; no Resume I acceptance; NO T-v1 creation.
- No aggregate similarity percentage; metrics declare nothing (plan §13).
- Owner visual preference is an owner-only input; it is NOT inferred here.
""",
        encoding="utf-8",
    )

def _write_e5_owner_package(
    out_dir: Path,
    target_pdf: Path,
    record: E5LoopRecord,
    presentation_labels: list[PresentationLabel] | None = None,
    *,
    approved_label_ids: list[str] | None = None,
    target_sha256: str | None = None,
    catalog_sha256: str | None = None,
    approval_provided: bool | None = None,
    approval_validated: bool | None = None,
) -> Path:
    """The safe owner-review package: target and each lane's selected
    render side by side per page,
    plus probe outputs, costs, the shell-issued presentation-label table,
    remaining differences, and the non-claims."""
    from app.ingestion.pdf_reader import read_pdf_text
    from app.pipeline import quality as c2r
    from app.template_analysis.visual_comparator import compare_pdf_layouts

    package = out_dir / "owner_review"
    package.mkdir(exist_ok=True)
    target_pages = _render_pages(target_pdf, out_dir, "owner_target")
    page_count = len(target_pages)
    # Resolve each lane's artifact through the explicit version state; the
    # label distinguishes a real BEST render from a defect-level active
    # version and from a mere latest attempt.
    lane_selected: dict[str, tuple[RenderVersion | None, Path, str]] = {}
    for lane_id, lane in record.lanes.items():
        version, stem, label = _selected_lane_artifact(lane)
        stem_path = (out_dir / f"lane_{lane_id}" / f"{stem}.pdf") if stem else None
        lane_selected[lane_id] = (version, stem_path, label) if stem_path else (None, None, "LATEST ATTEMPT")
    lane_pdfs: dict[str, Path] = {
        lane_id: paths[1] for lane_id, paths in lane_selected.items() if paths[1]
    }
    lane_pages: dict[str, list[Path]] = {}
    for lane_id, lane_pdf in lane_pdfs.items():
        visual_dir = package / f"lane_{lane_id}_visual"
        visual = compare_pdf_layouts(target_pdf, lane_pdf, visual_dir)
        lane_pages[lane_id] = [visual_dir / name for name in visual.generated_page_filenames]
        page_count = max(page_count, len(lane_pages[lane_id]))
    # Keep missing pages visible; never hide a continuation beyond the target.
    from PIL import Image

    missing_page = package / "missing_page.png"
    with Image.open(target_pages[0]) as first_page:
        Image.new("RGB", first_page.size, "white").save(missing_page)
    for page in range(1, page_count + 1):
        target_page = target_pages[page - 1] if page <= len(target_pages) else None
        columns: list[tuple[str, Path | None]] = [
            ("TARGET" if target_page else "TARGET (NO PAGE)", target_page or missing_page)
        ]
        for lane_id, images in lane_pages.items():
            lane_page = images[page - 1] if page <= len(images) else None
            label = f"LANE {lane_id.upper()} {lane_selected[lane_id][2]}"
            columns.append((label if lane_page else f"{label} (NO PAGE)", lane_page or missing_page))
        if len(columns) >= 2:
            _side_by_side(columns, package / f"comparison_page_{page}.png")
    shutil.copy2(target_pdf, package / "target.pdf")
    # Selected lane artifacts (copies; originals immutable). The filename and
    # every label distinguish a real BEST render from a defect-level active
    # version and from a latest attempt.
    _LABEL_KIND = {
        "BEST": "best",
        "ACTIVE DEFECT-LEVEL VERSION": "active_defect_level",
        "ACTIVE UNPROMOTED VERSION": "active_unpromoted",
        "LATEST ATTEMPT": "latest_attempt",
    }
    for lane_id, (version, lane_pdf, label) in lane_selected.items():
        if lane_pdf is None:
            continue
        kind = _LABEL_KIND[label]
        lane_html = out_dir / f"lane_{lane_id}" / f"{lane_pdf.stem}.html"
        shutil.copy2(lane_pdf, package / f"lane_{lane_id}_{kind}.pdf")
        if lane_html.exists():
            shutil.copy2(lane_html, package / f"lane_{lane_id}_{kind}.html")
    # Keep the bounded repair attempt visible even when the shell correctly
    # rolls it back: owner review needs BEFORE and AFTER, not only BEST.
    repair_artifacts: list[str] = []
    for lane_id, lane in record.lanes.items():
        if len(lane.render_versions) < 2:
            continue
        lane_dir = out_dir / f"lane_{lane_id}"
        initial_pdf = lane_dir / "render_1.pdf"
        repaired_pdf = lane_dir / f"render_{len(lane.render_versions)}.pdf"
        if not initial_pdf.exists() or not repaired_pdf.exists():
            continue
        shutil.copy2(initial_pdf, package / f"lane_{lane_id}_initial.pdf")
        shutil.copy2(repaired_pdf, package / f"lane_{lane_id}_repaired.pdf")
        repair_artifacts.extend((f"lane_{lane_id}_initial.pdf", f"lane_{lane_id}_repaired.pdf"))
        for page in range(1, max(v.page_count for v in lane.render_versions) + 1):
            before = lane_dir / f"render_1_page_{page}.png"
            after = lane_dir / f"render_{len(lane.render_versions)}_page_{page}.png"
            if before.exists() or after.exists():
                name = f"lane_{lane_id}_before_after_page_{page}.png"
                _side_by_side([("BEFORE", before), ("REPAIRED", after)], package / name)
                repair_artifacts.append(name)
    # Probe outputs.
    for lane_id in record.lanes:
        probes = out_dir / f"lane_{lane_id}" / "content_shape_probes.json"
        if probes.exists():
            shutil.copy2(probes, package / f"lane_{lane_id}_content_shape_probes.json")
    cost = {
        lane_id: {
            "tokens_in": lane.budget_state.get("usage", {}).get("input_tokens", 0),
            "tokens_out": lane.budget_state.get("usage", {}).get("output_tokens", 0),
            "live_calls": lane.budget_state.get("calls_by_mode", {}).get("live", 0),
            "tool_calls": lane.budget_state.get("tool_call_count"),
            "estimated_cost": lane.summary.get("estimated_provider_cost"),
        }
        for lane_id, lane in record.lanes.items()
    }
    (package / "cost_summary.json").write_text(
        json.dumps(
            {
                "pricing_source": (out_dir / "run_config.json").exists()
                and json.loads((out_dir / "run_config.json").read_text(encoding="utf-8")).get("provider_pricing"),
                "per_lane": cost,
                "note": "raw usage preserved; no price invented when billing is not verifiable",
            },
            ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    remaining = "\n".join(
        f"- lane {lane_id} `{fid}`: {next((f.observation for f in lane.findings if f.finding_id == fid), fid)}"
        for lane_id, lane in record.lanes.items()
        for fid in (lane.open_findings or [])
    ) or "- none recorded"
    def _best_line(lane_id: str, lane: E5LaneRecord) -> str:
        if lane.best_render_version:
            return f"- Lane {lane_id.upper()} best render: `{lane.best_render_version}`"
        if lane.best_defect_level_version:
            return (
                f"- Lane {lane_id.upper()}: NO hard-gate-valid best render exists; the "
                f"active defect-level version `{lane.best_defect_level_version}` is shown "
                "as ACTIVE DEFECT-LEVEL VERSION (a local improvement, never BEST)"
            )
        if lane.active_render_version:
            line = (
                f"- Lane {lane_id.upper()}: no best-valid render exists; the active "
                f"version `{lane.active_render_version}` is shown as ACTIVE "
                "UNPROMOTED VERSION — a rolled-back attempt is never shown as the "
                "lane's current output, never labeled BEST"
            )
            if lane.quarantined_repair_base:
                line += (
                    f". A quarantined repairable-but-not-passing candidate "
                    f"(`{lane.quarantined_repair_base}`) exists as the next "
                    "Builder's repair base only: it is never promoted, never "
                    "labeled BEST, and is NOT shown as the lane's current output"
                )
            return line
        return (
            f"- Lane {lane_id.upper()} best render: NONE — no best-valid render "
            "exists; the package shows the latest attempt (LATEST ATTEMPT), "
            "never labeled BEST"
        )

    best_lines = "\n".join(
        _best_line(lane_id, lane) for lane_id, lane in record.lanes.items()
    )
    # Presentation-label catalog: written into the run dir AND the package,
    # with the label table in the report. Audit material only — it is NOT an
    # acceptance of T-v1 and declares no winner.
    label_listing = _presentation_label_listing(
        presentation_labels or [],
        {
            lane_id: list((lane.summary or {}).get("presentation_labels_referenced") or [])
            for lane_id, lane in record.lanes.items()
        },
        approved_label_ids,
        target_sha256=target_sha256,
        catalog_sha256=catalog_sha256,
        approval_provided=approval_provided,
        approval_validated=approval_validated,
        recognition_method=(record.summary.get("presentation_label_approval") or {}).get(
            "recognition_method", "explicit_owner_input"
        ),
    )
    (out_dir / "presentation_labels.json").write_text(
        json.dumps(label_listing, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (package / "presentation_labels.json").write_text(
        json.dumps(label_listing, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    approved_count = len(label_listing["approved_label_ids"])
    proposed_count = len(label_listing["proposed_label_ids"])
    label_rows = "\n".join(
        "| `{}` | {} | {} | {} | {} | {} |".format(
            entry["label_id"],
            entry["text"],
            entry["kind"],
            entry["status"],
            ", ".join(entry["evidence_ids"]),
            ", ".join(entry["referenced_by_lanes"]) or "(none)",
        )
        for entry in label_listing["labels"]
    ) or "| (no measured presentation labels) | | | | | |"
    (package / "REPORT.md").write_text(
        f"""# Pipeline E5 owner-review package — {out_dir.name}

- Terminal state: **{record.summary.get('terminal_state')}** (NEVER owner acceptance)
{best_lines}

## Contents

- `comparison_page_N.png` — TARGET | Lane A | Lane B, per page;
  a column labeled LANE X LATEST ATTEMPT is NOT a best-valid render;
- `lane_*_visual/` — selected-output previews, difference images, and the
  existing visual-comparison report. Extra pages are previewed and shown in
  the comparison strips; differences cover matching page positions only.
  Text differs by design, so image scores cannot decide fidelity or content;
- `lane_*_best.html/.pdf` — each lane's best-valid render (only present when
  a best-valid render exists);
- `lane_*_active_defect_level.html/.pdf` — a lane's defect-level promoted
  active version when NO hard-gate-valid best exists (a local improvement,
  explicitly NOT BEST);
- `lane_*_active_unpromoted.html/.pdf` — a lane's current ACTIVE render that
  never earned promotion (no rollback attempt is ever shown as the lane's
  current output);
- `lane_*_latest_attempt.html/.pdf` — latest attempt for a lane with NO
  active version at all (explicitly not BEST);
- `lane_*_initial.pdf`, `lane_*_repaired.pdf`, and
  `lane_*_before_after_page_N.png` — the bounded repair evidence, retained
  even when the repaired candidate was rolled back ({', '.join(repair_artifacts) or 'no repair attempted'});
- `lane_*_content_shape_probes.json` — short/medium/long probe outcomes;
  probes marked diagnostic ran on a latest attempt and are NOT promotion
  evidence;
- `cost_summary.json` — usage + verified pricing record (no invented price);
- `presentation_labels.json` — the complete shell-issued presentation-label
  catalog (label_id, text, kind, target evidence id, which lanes referenced
  it); the same table is below; it is audit material, NOT an acceptance of
  T-v1;
- remaining material differences below; iteration histories in lane dirs.

## Presentation labels (recognized vs proposed; audit material)

Fixed visible text enters only through a shell-recognized label id. The shell
owns the escaped text. Owner direction 2026-10-22 permits automatic recognition
of measured headings matching the existing exact product aliases; unknown
labels remain proposed, unrenderable, and outside privacy exclusions. Legacy
explicit approval is still bound to target and catalog checksums. The retained
`approved` status means renderable, not human acceptance of the output.

- recognition method: {label_listing['recognition_method']}

- target_sha256: `{target_sha256}`
- catalog_sha256 (proposed-catalog identity): `{catalog_sha256}`
- owner approval provided: {approval_provided} · validated: {approval_validated}

Recognized: {approved_count} · proposed (unrecognized): {proposed_count}

| label_id | text | kind | status | evidence | referenced by lanes |
| --- | --- | --- | --- | --- | --- |
{label_rows}

## Remaining material differences (open findings)

{remaining}

## Non-claims

- This package is NOT a delivery and creates NO T-v1 record.
- No convergence claim and no representation winner is declared.
- No signed provider URLs, credentials, environment data, or rubric answers
  entered this package.
""",
        encoding="utf-8",
    )
    review_files = sorted(
        path for path in package.rglob("*")
        if path.is_file() and path.name != "missing_page.png"
    )
    pending_trials = [
        f"- Lane {lane_id.upper()} `{version}`: [trial PDF](../lane_{lane_id}/render_{index}.pdf), "
        f"[trial HTML](../lane_{lane_id}/render_{index}.html) — unmeasured, not BEST; human comparison required."
        for lane_id, lane in record.lanes.items()
        for index, render in enumerate(lane.render_versions, 1)
        if (version := render.version_id) in lane.summary.get("pending_layout_trials", [])
    ]
    (package / "REVIEW_INDEX.md").write_text(
        "# Owner review\n\n"
        "Inspect the selected PDFs, every comparison page, and independent gate reports. "
        "BEST and ready_for_owner_review are internal exploratory states, not product acceptance. "
        "No winner is declared. Red in difference images is target-only; blue is output-only. "
        "Candidate text differs by design.\n\n"
        + "\n".join(
            f"- [{path.relative_to(package).as_posix()}]({path.relative_to(package).as_posix()})"
            for path in review_files
        ) + ("\n\n## Layout trials awaiting human comparison\n\n" + "\n".join(pending_trials) if pending_trials else "") + "\n",
        encoding="utf-8",
    )
    for path in sorted(package.rglob("*")):
        if path.is_file():
            try:
                payload = path.read_text(encoding="utf-8")
            except Exception:
                continue
            assert_no_signed_strings(payload)
    return package
