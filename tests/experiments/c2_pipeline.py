"""C2-0a: validate a provider-neutral JSON layout state (Pipeline C2, step 0a).

Owner direction 2026-09-15 (PIPELINE_EVOLUTION_PROPOSAL §16): C2-0a compiles the
provider-neutral target evidence DIRECTLY into a versioned layout state and
validates that it can bind real structured candidate content to the target
layout. No A/C1 seed HTML is an input, intermediate, or output; HTML becomes a
compiled RenderPlan product only in C2-0b.

    Target document
    -> provider-neutral TargetLayoutEvidence (cached Adobe + local supplements)
    -> deterministic C2 compiler (reuses C1's measured scaffold derivations)
    -> validated C2LayoutState JSON (layout-state/1, experimental)
    -> section content shapes + section-owned structure
    -> probes driven by INDEPENDENT candidate fixtures (never derived from
       the state) with a leaf-ownership ledger

This is an EXPERIMENTAL contract. The product schema remains
``app.template_analysis.schemas.LayoutTemplateSpec`` (``2.0``); the
compatibility/migration assessment lives in ``C2_0A_REPORT.md`` §11.

Content/presentation separation: the state stores layout structure, measured
presentation, and semantic slot kinds only. Candidate facts come from
CandidateProfile at fill time (C2-0b+); target sample facts never enter the
state; target labels are presentation and never a content source.

Run one real target:

    .venv/bin/python -m tests.experiments.c2_pipeline \
        --target tests/local_datasets/resume_matrix/resume_E.pdf

Artifacts land under ``tests/experiments/runs/c2_0a_<stem>_<ts>/`` (ignored);
deterministic bytes for the same target.
"""

from __future__ import annotations

from app.pipeline.layout_compiler import (
    RULE_TOP_MATCH_TOLERANCE_PT,
    _below_heading_rule,
    _body_style_token,
    _content_to_heading_gap,
    _elements_in_top_range,
    _entry_child_node,
    _entry_tier_values,
    _header_style_token,
    _heading_style_token,
    _label_case,
    _list_child,
    _measured_color_for_evidence,
    _mode_style_group,
    _resolve_above_heading_rule,
    _rule_evidence,
    _section_content_style,
    _token_for_style_group,
    own_leaf,
    state_from_scaffolds,
)


import argparse
import hashlib
import json
import re
from collections import Counter, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.template_analysis.commercial.bridge import _dominant_body
from app.template_analysis.commercial.models import NormalizedLayoutEvidence
from tests.experiments.a_pipeline import _analyze_target, build_format_summary
from tests.experiments.c_pipeline import (
    BodyScaffold,
    derive_body_scaffold,
    derive_body_tier_targets,
    derive_header_scaffold,
)

RUNS = Path(__file__).with_name("runs")
DEFAULT_TARGET = (
    Path(__file__).resolve().parents[2] / "tests/local_datasets/resume_matrix/resume_E.pdf"
)

# Renderer-instruction ban (work order): the provider-neutral state must not
# carry HTML/CSS/OOXML instructions. extra="forbid" rejects such fields; this
from tests.experiments.c2_state import (  # phase-1 split re-export
    _RENDERER_SYNTAX,
    SourceRole,
    ALL_SOURCES,
    _DEFAULT_CONTENT_KINDS,
    _SOURCE_KEYWORDS,
    bind_source,
    _COMPOSITE_SPLIT,
    bind_composite,
    StateModel,
    EvidenceProvenance,
    PageState,
    StyleToken,
    RuleDecoration,
    BadgeDecoration,
    Column,
    HeaderField,
    NodeSpacing,
    FlowConstraint,
    SectionBinding,
    SectionContent,
    CategoryGridColumn,
    CategoryGrid,
    LayoutNode,
    CapabilityGap,
    _ALLOWED_PARENT_KINDS,
    C2LayoutState,
    validate_layout_state,
    state_bytes,
)
from tests.experiments.c2_candidates import (  # phase-1 split re-export
    CandidateLeafKind,
    CandidateLeaf,
    UnroutableContent,
    CandidateSection,
    CandidateDocument,
    _profile_leaves,
    independent_candidate_fixtures,
    FROZEN_C1_RUNS,
    C2_0B_PAIRS,
    _leaf,
    _unroutable,
    candidate_resume_D,
    candidate_resume_E,
    candidate_resume_F,
    candidate_document_for_pair,
)




































def compile_layout_state(
    target_pdf: Path,
    summary: dict[str, Any],
    *,
    evidence: NormalizedLayoutEvidence | None = None,
    provider_name: str = "adobe",
    template_version: str = "c2-0a-3",
) -> C2LayoutState:
    """Compile the provider-neutral state directly from measured evidence."""
    margins = summary.get("margins_pt", {}).get("default", {})
    missing = [key for key in ("left", "right", "top") if margins.get(key) is None]
    if missing:
        raise ValueError(f"target evidence lacks measured margins: {missing}")

    header_scaffold = derive_header_scaffold(target_pdf, summary)
    body_scaffold = derive_body_scaffold(target_pdf, summary, header_scaffold=header_scaffold)
    bullet_tiers = (
        derive_body_tier_targets(target_pdf, float(body_scaffold.entry.left_x0_pt))
        if body_scaffold.entry is not None
        else {}
    )
    return state_from_scaffolds(
        hashlib.sha256(target_pdf.read_bytes()).hexdigest(),
        header_scaffold,
        body_scaffold,
        bullet_tiers,
        summary,
        provider_name=provider_name,
        evidence=evidence,
        template_version=template_version,
    )


def render_context_coverage(candidate: CandidateDocument, source_text: str) -> dict[str, object]:
    """Total ordered coverage of the candidate render context against the
    frozen C1 source inventory.

    Every non-empty source line carrying an alphanumeric character (the same
    inventory rule as C1's verbatim-coverage gates; pure-glyph artifact lines
    carry no content) must be consumed exactly once by the authored walk:
    header leaves and anchored unroutables in document order, then each
    section's heading plus its leaves. Matching rules, in order: an authored
    text equals one source line or a space-join of consecutive lines (wrapped
    bullets); or 2-3 consecutive authored texts jointly partition one source
    line (e.g. D's table cells ``Power Business Ink – Senior Business
    Engineer`` split into company + role). Comparison ignores whitespace and
    dash characters on both sides — the same hyphen-insensitive rule as C1's
    PDF-side coverage gate (Chrome line-break merging).
    """

    def norm(value: str) -> str:
        return re.sub(r"[\s\u2013\u2014-]+", "", value)

    inventory = [
        line.strip()
        for line in source_text.splitlines()
        if line.strip() and any(character.isalnum() for character in line)
    ]
    leaf_by_id = {leaf.leaf_id: leaf for leaf in candidate.leaves}
    walk: list[tuple[str, str]] = []  # (kind, text)
    pending = deque(candidate.unroutable)
    for leaf in candidate.leaves:
        while pending and pending[0].before_leaf_id == leaf.leaf_id:
            record = pending.popleft()
            walk.append(("unroutable", record.text))
        if leaf.kind == "header_field":
            walk.append(("leaf", leaf.text or ""))
    if pending:
        raise ValueError(
            f"unroutable anchors not encountered in document order: "
            f"{[record.text for record in pending]}"
        )
    for section in candidate.sections:
        if section.heading:
            walk.append(("heading", section.heading))
        for leaf_id in section.leaf_ids:
            walk.append(("leaf", leaf_by_id[leaf_id].text or ""))

    cursor = 0
    unmatched: list[str] = []
    index = 0
    while index < len(walk):
        kind, text = walk[index]
        stripped = text.strip()
        matched = False
        for span in (1, 2, 3):
            if norm(" ".join(inventory[cursor : cursor + span])) == norm(stripped) and stripped:
                cursor += span
                matched = True
                break
        if not matched:
            # Authored partition of one source line (split cells).
            for partition in (2, 3):
                if index + partition <= len(walk) and norm(
                    " ".join(item for _, item in walk[index : index + partition])
                ) == norm(inventory[cursor]):
                    index += partition - 1
                    cursor += 1
                    matched = True
                    break
        if not matched:
            unmatched.append(f"{kind}:{stripped[:80]}")
        index += 1
    return {
        "inventory_lines": len(inventory),
        "consumed_lines": cursor,
        "unconsumed_lines": inventory[cursor:],
        "unmatched_authored_texts": unmatched,
        "total_coverage": cursor == len(inventory) and not unmatched,
    }


# ---------------------------------------------------------------------------
# Real structural probe with a leaf-ownership ledger
# ---------------------------------------------------------------------------

# Statuses: fully_materialized (every leaf owned), materialized_with_gaps
# (all routable leaves owned exactly once; remaining leaves carry explicit
# unhomed-source gaps), failed (any ownership violation).
ProbeStatus = Literal["fully_materialized", "materialized_with_gaps", "failed"]




def run_flow_probe(state: C2LayoutState, candidate: CandidateDocument) -> dict[str, Any]:
    """Instantiate INDEPENDENT candidate content against the state and validate
    leaf-by-leaf ownership: every candidate leaf maps to exactly one
    materialized destination, or an explicit unhomed-source gap is recorded.
    Structural violations (duplicate consumption, incompatible section,
    unresolved-binding placement, missing content structure) FAIL the probe.
    """
    failures: list[str] = []
    unhomed: list[dict[str, str]] = []
    notes: list[str] = []
    ledger: dict[str, str] = {}  # leaf_id -> destination node id

    by_id = {node.node_id: node for node in state.nodes}
    header_rows = [node for node in state.nodes if node.kind == "header_row"]
    sections = [node for node in state.nodes if node.kind == "section"]
    instances: list[dict[str, Any]] = []
    skill_group_counter = 0
    next_order = max(node.reading_order for node in state.nodes) + 1

    def own(leaf_id: str, destination: str) -> bool:
        return own_leaf(ledger, failures, leaf_id, destination)

    def add_instance(node_id: str, parent_id: str | None, kind: str) -> None:
        nonlocal next_order
        instances.append(
            {"node_id": node_id, "parent_id": parent_id, "kind": kind, "reading_order": next_order}
        )
        next_order += 1

    def mapped_sections(source: SourceRole) -> list[LayoutNode]:
        return [
            section
            for section in sections
            if section.binding is not None
            and section.binding.mapping_action == "map"
            and source in (section.binding.sources or [])
        ]

    def content_destination(section: LayoutNode, source: SourceRole | None = None) -> str | None:
        content = section.content
        if content is None:
            return None
        if content.content_kind == "composite":
            # C2-0cM: a composite section consumes a source through the
            # matching ordered sub-content's own shape.
            if source is None:
                return None
            sub = next(
                (s for s in content.sub_contents if source in s.sources), None
            )
            if sub is None or sub.content_kind == "unsupported":
                return None
            if sub.content_kind == "entries":
                return section.entry_ref
            if sub.content_kind in {"item_list", "inline_items", "badge_items"}:
                return section.list_ref or section.node_id
            return section.node_id
        if content.content_kind == "entries":
            return section.entry_ref
        if content.content_kind in {"item_list", "inline_items", "badge_items"}:
            return section.list_ref or section.node_id
        if content.content_kind == "paragraph":
            return section.node_id
        return None  # unsupported handled by callers

    def section_consumes(section: LayoutNode, source: SourceRole, kinds: set[str]) -> bool:
        """Whether the mapped section consumes ``source`` through a content
        shape in ``kinds`` (its own, or — composite sections — the matching
        sub-content's; C2-0cM)."""
        content = section.content
        if content is None:
            return False
        if content.content_kind == "composite":
            return any(
                source in sub.sources and sub.content_kind in kinds
                for sub in content.sub_contents
            )
        return content.content_kind in kinds

    def source_sub_unsupported(section: LayoutNode, source: SourceRole) -> bool:
        content = section.content
        if content is None:
            return False
        if content.content_kind == "unsupported":
            return True
        if content.content_kind == "composite":
            return any(
                source in sub.sources and sub.content_kind == "unsupported"
                for sub in content.sub_contents
            )
        return False

    def route_top_level(
        leaf: CandidateLeaf, source: SourceRole, expected_kinds: set[str], instance_kind: str
    ) -> bool:
        homes = [
            section for section in mapped_sections(source)
            if section_consumes(section, source, expected_kinds)
        ]
        broken = [
            section for section in mapped_sections(source)
            if section.content is None or source_sub_unsupported(section, source)
        ]
        if broken:
            failures.append(
                f"candidate leaf {leaf.leaf_id!r}: mapped section(s) "
                f"{[s.node_id for s in broken]} declare no supported content structure"
            )
            return False
        if not homes:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id,
                    "kind": leaf.kind,
                    "source": source,
                    "reason": f"no mapped {source!r} section with {'/'.join(sorted(expected_kinds))} content",
                }
            )
            return False
        destination = content_destination(homes[0], source)
        if destination is None:
            failures.append(
                f"candidate leaf {leaf.leaf_id!r}: {homes[0].node_id} has no consumable content destination"
            )
            return False
        add_instance(f"{homes[0].node_id}.content.{leaf.leaf_id}", homes[0].node_id, instance_kind)
        return own(leaf.leaf_id, destination)

    # -- header fields --------------------------------------------------------
    for leaf in (item for item in candidate.leaves if item.kind == "header_field"):
        homes = [row for row in header_rows if leaf.slot in row.slots]
        if not homes:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id,
                    "kind": leaf.kind,
                    "source": "header",
                    "reason": f"no header row carries slot {leaf.slot!r}",
                }
            )
            continue
        home = homes[0]
        add_instance(f"{home.node_id}.{leaf.slot}", home.node_id, "header_value")
        own(leaf.leaf_id, f"{home.node_id}.{leaf.slot}")

    # -- summary paragraphs ----------------------------------------------------
    for leaf in (item for item in candidate.leaves if item.kind == "summary_paragraph"):
        route_top_level(leaf, "summary", {"paragraph"}, "paragraph_instance")

    # -- skills: groups own their items ----------------------------------------
    skill_items_by_group: dict[str, list[CandidateLeaf]] = {}
    for leaf in candidate.leaves:
        if leaf.kind == "skill":
            skill_items_by_group.setdefault(leaf.parent_leaf_id or "", []).append(leaf)
    for leaf in (item for item in candidate.leaves if item.kind == "skill_group"):
        all_homes = mapped_sections("skills")
        broken = [
            section for section in all_homes
            if section.content is None or source_sub_unsupported(section, "skills")
        ]
        if broken:
            failures.append(
                f"candidate leaf {leaf.leaf_id!r}: mapped section(s) "
                f"{[s.node_id for s in broken]} declare no supported content structure"
            )
            continue
        homes = [
            section for section in all_homes
            if section_consumes(section, "skills", {"item_list", "inline_items", "badge_items"})
        ]
        if len(homes) > 1:
            unpartitioned = [
                section.node_id for section in homes
                if section.binding is None or section.binding.partition_policy == "none"
            ]
            if unpartitioned:
                failures.append(
                    "duplicate consumption without partition policy: candidate "
                    f"skills content reaches mapped sections {unpartitioned}; "
                    "one candidate skills collection is never duplicated"
                )
                continue
        if not homes:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": "skills",
                    "reason": "no mapped skills section with item content",
                }
            )
            # Cascade: the group's items share its fate, never silently.
            for item_leaf in skill_items_by_group.get(leaf.leaf_id, []):
                unhomed.append(
                    {
                        "leaf_id": item_leaf.leaf_id, "kind": item_leaf.kind,
                        "source": "skills",
                        "reason": f"parent skill group {leaf.leaf_id!r} has no home",
                    }
                )
            continue
        # Round-robin across explicitly partitioned sections.
        section = homes[skill_group_counter % len(homes)]
        skill_group_counter += 1
        destination = content_destination(section, "skills")
        list_node = by_id.get(section.list_ref) if section.list_ref else None
        add_instance(f"{section.node_id}.content.{leaf.leaf_id}", section.node_id, "item_group_instance")
        own(leaf.leaf_id, destination or section.node_id)
        for item_leaf in skill_items_by_group.get(leaf.leaf_id, []):
            if list_node is not None and list_node.list_marker == "bullet":
                add_instance(
                    f"{section.node_id}.item.{item_leaf.leaf_id}", section.node_id, "item_instance"
                )
            else:
                add_instance(
                    f"{section.node_id}.item.{item_leaf.leaf_id}", section.node_id, "inline_item_instance"
                )
            own(item_leaf.leaf_id, destination or section.node_id)

    # -- languages --------------------------------------------------------------
    for leaf in (item for item in candidate.leaves if item.kind == "language"):
        route_top_level(leaf, "languages", {"item_list", "inline_items"}, "item_instance")

    # -- work experience entries + bullets ---------------------------------------
    work_section: LayoutNode | None = next(iter(mapped_sections("work_experience")), None)
    work_usable = (
        work_section is not None
        and section_consumes(work_section, "work_experience", {"entries"})
    )
    if mapped_sections("work_experience") and not work_usable:
        failures.append(
            "mapped work_experience section declares no supported entries structure"
        )
    entry_instance_ids: dict[str, str] = {}
    subgroup_instance_ids: dict[str, str] = {}
    for leaf in (item for item in candidate.leaves if item.kind == "work_entry"):
        if not work_usable:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": "work_experience",
                    "reason": "no mapped work_experience section with entries content",
                }
            )
            continue
        instance_id = f"{work_section.node_id}.content.{leaf.leaf_id}"
        add_instance(instance_id, work_section.node_id, "entry_instance")
        own(leaf.leaf_id, work_section.entry_ref or work_section.node_id)
        entry_instance_ids[leaf.leaf_id] = instance_id
    for leaf in (item for item in candidate.leaves if item.kind == "entry_subgroup"):
        parent_instance = entry_instance_ids.get(leaf.parent_leaf_id or "")
        if parent_instance is None:
            if work_usable:
                failures.append(
                    f"candidate leaf {leaf.leaf_id!r}: parent work entry has no instance"
                )
            else:
                unhomed.append(
                    {
                        "leaf_id": leaf.leaf_id, "kind": leaf.kind,
                        "source": leaf.source,
                        "reason": "parent work entry has no home: no usable "
                        "work-experience section",
                    }
                )
            continue
        entry_node = by_id.get(work_section.entry_ref) if work_section.entry_ref else None
        if entry_node is None or entry_node.subgroup_title_style_id is None:
            failures.append(
                f"candidate leaf {leaf.leaf_id!r}: entry sub-group requires a declared "
                "measured sub-group title tier on the entry row "
                "(subgroup_title_style_id); none is declared"
            )
            continue
        instance_id = f"{parent_instance}.subgroup.{leaf.leaf_id}"
        add_instance(instance_id, parent_instance, "subgroup_instance")
        own(leaf.leaf_id, parent_instance)
        subgroup_instance_ids[leaf.leaf_id] = instance_id
    for leaf in (item for item in candidate.leaves if item.kind == "work_bullet"):
        parent_instance = entry_instance_ids.get(leaf.parent_leaf_id or "")
        if parent_instance is None:
            parent_instance = subgroup_instance_ids.get(leaf.parent_leaf_id or "")
        if parent_instance is None:
            if work_usable:
                failures.append(
                    f"candidate leaf {leaf.leaf_id!r}: parent work entry has no instance"
                )
            else:
                # C2-0a bookkeeping closure (2026-09-15): when the target has no
                # usable work-experience section, child bullets inherit the
                # parent entry's unhomed status — they are never left as
                # unexplained unconsumed leaves.
                unhomed.append(
                    {
                        "leaf_id": leaf.leaf_id, "kind": leaf.kind,
                        "source": "work_experience",
                        "reason": "parent work entry has no home: no usable "
                        "work-experience section",
                    }
                )
            continue
        list_node = by_id.get(work_section.list_ref) if work_section.list_ref else None
        if list_node is not None and list_node.list_marker == "bullet":
            add_instance(f"{parent_instance}.bullet.{leaf.leaf_id}", parent_instance, "bullet_instance")
        else:
            # Zero-bullet target ruling (§10.5): source bullet glyphs stay
            # verbatim body text inside their entry.
            add_instance(f"{parent_instance}.textline.{leaf.leaf_id}", parent_instance, "text_line_instance")
        own(leaf.leaf_id, parent_instance)

    # -- education entries ---------------------------------------------------------
    for leaf in (item for item in candidate.leaves if item.kind == "education_entry"):
        homes = [
            section for section in mapped_sections("education")
            if section_consumes(section, "education", {"entries"})
        ]
        if not homes:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": "education",
                    "reason": "no mapped education section with entries content",
                }
            )
            continue
        section = homes[0]
        instance_id = f"{section.node_id}.content.{leaf.leaf_id}"
        add_instance(instance_id, section.node_id, "entry_instance")
        own(leaf.leaf_id, section.entry_ref or section.node_id)
        entry_instance_ids[leaf.leaf_id] = instance_id

    # -- entry child lines (detail/meta; C2-0b render contexts) --------------------
    for leaf in (item for item in candidate.leaves if item.kind in {"entry_detail", "entry_meta"}):
        parent_instance = entry_instance_ids.get(leaf.parent_leaf_id or "")
        if parent_instance is None:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": leaf.source,
                    "reason": "parent entry has no instance",
                }
            )
            continue
        add_instance(f"{parent_instance}.{leaf.kind}.{leaf.leaf_id}", parent_instance, f"{leaf.kind}_instance")
        own(leaf.leaf_id, parent_instance)

    # -- certifications ----------------------------------------------------------
    for leaf in (item for item in candidate.leaves if item.kind == "certification_item"):
        route_top_level(leaf, "certifications", {"item_list", "inline_items", "badge_items"}, "item_instance")

    # -- additional sections (candidate-only unmatched sections retain headings) --
    for leaf in (item for item in candidate.leaves if item.kind == "additional_section"):
        homes = [
            section for section in mapped_sections("additional_details")
            if section_consumes(section, "additional_details", {"item_list", "inline_items", "entries"})
        ]
        if homes:
            section = homes[0]
            add_instance(f"{section.node_id}.content.{leaf.leaf_id}", section.node_id, "section_content_instance")
            own(leaf.leaf_id, content_destination(section, "additional_details") or section.node_id)
        else:
            # Owner decision: candidate-only unmatched additional sections
            # retain their own headings — materialized as candidate-owned
            # instances, recorded as explicit gaps (never dropped silently).
            add_instance(f"candidate_only.{leaf.leaf_id}", None, "candidate_only_section")
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": "additional_details",
                    "reason": "no mapped additional_details section; retained as candidate-only section with its own heading",
                }
            )
    for leaf in (item for item in candidate.leaves if item.kind == "additional_item"):
        parent_leaf = next(
            (item for item in candidate.leaves if item.leaf_id == leaf.parent_leaf_id), None
        )
        if parent_leaf is not None and parent_leaf.leaf_id in ledger:
            parent_dest = ledger[parent_leaf.leaf_id]
            add_instance(f"item.{leaf.leaf_id}", parent_dest, "item_instance")
            own(leaf.leaf_id, parent_dest)
        else:
            unhomed.append(
                {
                    "leaf_id": leaf.leaf_id, "kind": leaf.kind, "source": "additional_details",
                    "reason": "parent additional section was not consumed",
                }
            )

    # -- invariant checks (computed, never asserted True) -------------------------
    instance_ids = [instance["node_id"] for instance in instances]
    unique_ids = len(set(instance_ids)) == len(instance_ids)
    if not unique_ids:
        failures.append("generated instance node ids are not unique")
    orders = [instance["reading_order"] for instance in instances]
    reading_order_valid = orders == sorted(orders) and len(set(orders)) == len(orders)
    if not reading_order_valid:
        failures.append("instance reading order is not strictly increasing")
    known_ids = set(by_id) | set(instance_ids)
    parents_valid = all(
        instance["parent_id"] is None or instance["parent_id"] in known_ids
        for instance in instances
    )
    if not parents_valid:
        failures.append("instance parent references a nonexistent node")
    body_y_free = not any(
        node.top_pt is not None for node in state.nodes if node.kind != "header_row"
    )
    if not body_y_free:
        failures.append("body node carries absolute y geometry")
    duplicated_leaves = len(candidate.leaves) - len(ledger) - len(unhomed)
    if duplicated_leaves > 0:
        failures.append(f"{duplicated_leaves} candidate leaf(s) consumed more than once")
    for leaf in candidate.leaves:
        if leaf.leaf_id not in ledger and not any(u["leaf_id"] == leaf.leaf_id for u in unhomed):
            failures.append(f"candidate leaf {leaf.leaf_id!r} is unconsumed with no recorded gap")

    if failures:
        status: ProbeStatus = "failed"
    elif unhomed:
        status = "materialized_with_gaps"
    else:
        status = "fully_materialized"
    return {
        "status": status,
        "passed": status != "failed",
        "failures": failures,
        "unhomed": unhomed,
        "notes": notes,
        "leaf_ownership": {
            "total_leaves": len(candidate.leaves),
            "owned_leaves": len(ledger),
            "unhomed_leaves": len(unhomed),
            "ownership_exactly_one": all(
                sum(1 for key in ledger if key == leaf.leaf_id) == 1
                for leaf in candidate.leaves
                if leaf.leaf_id in ledger
            ),
        },
        "checks": {
            "instance_ids_unique": unique_ids,
            "reading_order_valid": reading_order_valid,
            "parent_references_valid": parents_valid,
            "body_nodes_carry_no_absolute_y": body_y_free,
            "no_duplicated_leaf_consumption": duplicated_leaves == 0,
            "no_unowned_leaves": all(
                leaf.leaf_id in ledger or any(u["leaf_id"] == leaf.leaf_id for u in unhomed)
                for leaf in candidate.leaves
            ),
        },
        "instantiated": {"instances": len(instances)},
        "ledger": dict(sorted(ledger.items())),
    }


# ---------------------------------------------------------------------------
# Validation and artifacts
def compile_target(target_pdf: Path, run_dir: Path) -> dict[str, Any]:
    """Full C2-0a run for one target: evidence -> state -> validation -> probes."""
    run_dir.mkdir(parents=True, exist_ok=True)
    evidence, raw = _analyze_target(target_pdf, run_dir, use_persistent_cache=True)
    summary = build_format_summary(evidence, raw, target_pdf)
    state = compile_layout_state(
        target_pdf, summary, evidence=evidence, provider_name=evidence.provider
    )

    violations = validate_layout_state(state)
    probes = {}
    for profile_name, candidate in independent_candidate_fixtures().items():
        probes[profile_name] = {
            "candidate_id": candidate.candidate_id,
            "result": run_flow_probe(state, candidate),
        }
    provenance = {
        "schema_version": state.schema_version,
        "template_version": state.template_version,
        "target": str(target_pdf),
        "target_sha256": state.provenance.target_sha256,
        "node_provenance": [
            {
                "node_id": node.node_id,
                "evidence_ids": node.evidence_ids,
                "derivation": (
                    "header_scaffold"
                    if node.kind == "header_row"
                    else "body_scaffold.heading"
                    if node.kind in {"section", "heading"}
                    else "body_scaffold.entry"
                    if node.kind == "entry_row"
                    else "local_pdf.bullet_tiers"
                ),
            }
            for node in state.nodes
        ],
        "style_provenance": [
            {"style_id": style.style_id, "evidence_ids": style.evidence_ids}
            for style in state.styles
        ],
        "decoration_provenance": [
            *[
                {"rule_id": rule.rule_id, "evidence_ids": rule.evidence_ids}
                for rule in state.rules
            ],
            *[
                {"badge_id": badge.badge_id, "evidence_ids": badge.evidence_ids}
                for badge in state.badges
            ],
        ],
    }

    (run_dir / "layout_state.json").write_bytes(state_bytes(state))
    (run_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "schema_validation.json").write_text(
        json.dumps(
            {"valid": not violations, "violations": violations}, indent=2, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "capability_gaps.json").write_text(
        json.dumps(
            {
                "gaps": [gap.model_dump() for gap in state.capability_gaps],
                "warnings": state.warnings,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "probes_summary.json").write_text(
        json.dumps(probes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "run_dir": str(run_dir),
        "node_count": len(state.nodes),
        "style_count": len(state.styles),
        "rule_count": len(state.rules),
        "badge_count": len(state.badges),
        "capability_gap_count": len(state.capability_gaps),
        "schema_valid": not violations,
        "violations": violations,
        "probes": probes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    target = (
        args.target
        if args.target.is_absolute()
        else (Path(__file__).resolve().parents[2] / args.target)
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.out or (RUNS / f"c2_0a_{target.stem}_{stamp}")
    result = compile_target(target, run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["schema_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
