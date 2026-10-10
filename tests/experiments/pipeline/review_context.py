"""Reviewer scope, region catalog, and content-free prompt context."""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
from tests.experiments.pipeline.shared import DefectFinding
from tests.experiments.pipeline.measurement import _header_vertical_rule_target_context, _target_body_text_color_context

E5_LANE_REPAIR_SURFACE: dict[str, str] = {
    "b": (
        "Lane B — constrained authored HTML/CSS template with typed candidate\n"
        "slots. The Builder owns the whole template, so which candidate leaves\n"
        "render (and how) is template_compilation owned by this Builder;\n"
        "no_target_candidate_facts failures are never builder-ownable. Template\n"
        "safety is enforced before rendering by the authored-template validator."
    ),
}


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


def _e5_region_catalog(state: Any, derived: dict[str, Any]) -> list[dict[str, Any]]:
    label_pages = {
        str(item.get("text") or "").casefold(): int(item["page"])
        for item in derived.get("sidebar_labels") or [] if item.get("page")
    }
    nodes = {node.node_id: node for node in state.nodes}

    def section_for(node: Any) -> Any | None:
        current = node
        while current is not None and current.kind != "section":
            current = nodes.get(current.parent_id)
        return current

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
        section = section_for(node)
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
    extra_render_page: bool = False,
) -> str:
    context = {
        "target_version": target_version,
        "render_version": render_version,
        "page": page,
        "extra_render_page": extra_render_page,
        "region_catalog": region_catalog,
        "review_scope": review_scope,
        "candidate_review_scope": candidate_review_scope,
        "representation_capabilities": E5_LANE_REPAIR_SURFACE[lane],
        "deterministic_presentation_facts": deterministic_presentation_facts,
    }
    return (
        ("Inspect this RENDER-only extra page for spillover or invalid continuation. "
         if extra_render_page else "Compare the TARGET and RENDER images for exactly this page. ")
        + "Use one "
        "region_id from region_catalog per finding and exactly one suspected "
        "dimension. Carry the exact target_version, render_version, and page "
        "below. Do not combine ABOUT ME with contact, target-only content with "
        "candidate presentation, or multiple regions/dimensions. role_gap is "
        "only for entry/job/role spacing; header_vertical_rule_geometry is only "
        "for the header/name vertical rule. Typed split-name slots are available "
        "only from verified CandidateProfile parts; flat "
        "candidate:contact cannot create a label/value table. Candidate "
        "facts are absent by design.\n"
        + json.dumps(context, ensure_ascii=False, sort_keys=True)
    )


def _validate_reviewer_regions(
    findings: list[DefectFinding], region_ids: set[str], page: int | set[int]
) -> list[DefectFinding]:
    pages = page if isinstance(page, set) else {page}
    valid = []
    for finding in findings:
        dimension = finding.suspected_dimension.casefold()
        if finding.page not in pages or finding.region not in region_ids:
            continue
        if re.search(r"\b(and|plus)\b|[,/]", dimension):
            continue
        metric = finding.requested_measurement.metric
        if metric in {"page_overflow", "pdf_font_size", "pdf_font_role", "heading_continuation"}:
            continue  # shell-owned structural gate, not a reviewer measurement
        if metric == "role_gap" and not any(word in dimension for word in ("gap", "spacing", "role")):
            continue
        if metric == "header_vertical_rule_geometry" and not finding.region.startswith("header"):
            continue
        valid.append(finding)
    return valid
