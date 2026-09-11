"""State, routing, and bounded mutations for A-pipeline refinement."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from lxml import etree, html as lxml_html
from pydantic import BaseModel, ConfigDict, Field


class GateFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    details: Any = None


class HardGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    failures: list[GateFailure] = Field(default_factory=list)
    l1: dict[str, Any] = Field(default_factory=dict)
    independent: dict[str, Any] = Field(default_factory=dict)


class VisualIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region: str
    category: Literal["content_integrity", "semantic_placement", "structure", "presentation"] = "presentation"
    dimension: str
    description: str
    evidence: str
    suggested_action: Literal[
        "reassign_source", "regroup_record", "select_existing_variant",
        "set_style_token", "set_rule_geometry", "set_section_spacing", "unknown",
    ] = "unknown"
    measurement_refs: list[str] = Field(default_factory=list)


class RubricResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rubric_id: str
    status: Literal["pass", "fail", "unknown"]
    evidence: str


class BuilderOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["set_style_token", "set_rule_geometry", "set_section_spacing"]
    selector: str
    property: str
    measurement_ref: str


class PlacementOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["reassign_source", "regroup_record", "select_existing_variant"]
    source_line_id: str = Field(pattern=r"^L\d{4}$")
    destination_selector: str


class VisualRegression(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region: str
    dimension: str


class VisualComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preference: Literal["challenger_better", "champion_better", "tie", "not_applicable"]
    confidence: Literal["high", "medium", "low"]
    retained_champion: Literal["challenger", "champion"]
    retained_champion_acceptable: bool
    retained_champion_issues: list[VisualIssue] = Field(default_factory=list)
    regressions: list[VisualRegression] = Field(default_factory=list)
    rubric_results: list[RubricResult]


@dataclass
class CandidateState:
    template_html: str
    filled_html: str
    template_sha256: str
    builder_changes: list[dict[str, Any]]


@dataclass
class RefinementResult:
    champion_round: int | None
    champion_html: str | None
    stop_reason: str
    rounds: list[dict[str, Any]]
    filler_usage: dict[str, int]
    visual_usage: dict[str, int]
    filler_attempts: int
    reviewer_checks: int
    champion_state: CandidateState | None = None
    builder_usage: dict[str, int] | None = None
    builder_changes: dict[str, Any] | None = None


FILLER_ACTIONS = {"reassign_source", "regroup_record", "select_existing_variant"}
BUILDER_ACTIONS = {"set_style_token", "set_rule_geometry", "set_section_spacing"}
BUILDER_PROPERTIES = {
    "set_style_token": {
        "font-size": (".font_size_pt",),
        "line-height": (".line_height_pt",),
        "letter-spacing": (".char_spacing_pt",),
    },
    "set_rule_geometry": {
        "border-top-width": (".stroke_width_pt",),
        "margin-top": (".gap_above_pt",),
        "margin-bottom": (".gap_below_pt",),
    },
    "set_section_spacing": {
        "margin-top": (".spacing_before_pt",),
        "margin-bottom": (".spacing_before_pt",),
        "padding-top": (".spacing_before_pt",),
        "padding-bottom": (".spacing_before_pt",),
    },
}


def build_measurement_catalog(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}

    def add(reference: str, value: Any, provenance: Any) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            catalog[reference] = {"value": value, "unit": "pt", "provenance": provenance}

    for style_id, style in summary.get("style_groups", {}).items():
        for field in ("font_size_pt", "line_height_pt", "char_spacing_pt"):
            add(f"{style_id}.{field}", style.get(field), {
                "summary_path": f"style_groups.{style_id}.{field}",
                "source": style.get("provenance", summary.get("measurement_policy")),
            })
    for group in summary.get("visual_groups", []):
        add(f"{group['id']}.spacing_before_pt", group.get("spacing_before_pt"), {
            "summary_path": f"visual_groups.{group['id']}.spacing_before_pt",
            "source": summary.get("measurement_policy"),
        })
    for index, rule in enumerate(summary.get("rules", []), 1):
        for field in ("stroke_width_pt", "gap_above_pt", "gap_below_pt"):
            add(f"rule_{index}.{field}", rule.get(field), {
                "summary_path": f"rules.{index - 1}.{field}",
                "source": rule.get("provenance", summary.get("measurement_policy")),
            })
    return catalog


def resolve_measurements(issue: VisualIssue, catalog: dict[str, dict[str, Any]], *, required: bool = False) -> list[dict[str, Any]]:
    unknown = [reference for reference in issue.measurement_refs if reference not in catalog]
    if unknown:
        raise ValueError("Unknown measurement references: " + ", ".join(unknown))
    resolved = [{"measurement_ref": reference, **catalog[reference]} for reference in issue.measurement_refs]
    if required and not resolved:
        raise ValueError(f"{issue.suggested_action} requires a measured reference")
    return resolved


def issue_fingerprint(issue: VisualIssue) -> str:
    normalize = lambda value: re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    try:
        evidence = json.loads(issue.evidence)
    except (json.JSONDecodeError, TypeError):
        evidence = {}
    if issue.dimension in _GROUPED_GATE_CODES and isinstance(evidence, list):
        line_ids = sorted(row.get("source_line_id") for row in evidence if isinstance(row, dict) and row.get("source_line_id"))
        if line_ids:
            return f"{issue.dimension}|{','.join(line_ids)}"
    if not isinstance(evidence, dict):
        evidence = {}
    code = issue.dimension
    line_id = evidence.get("source_line_id")
    if code in {"missing_source_content", "duplicated_source_content", "source_reading_order"} and line_id:
        return f"{code}|{line_id}"
    if code == "source_block_assignment" and line_id:
        return f"{code}|{line_id}|{evidence.get('expected_block')}"
    if code == "source_record_assignment" and line_id:
        return f"{code}|{line_id}|{evidence.get('expected_record')}"
    if code == "invented_content":
        if evidence.get("placeholder_derived"):
            return "invented_content|template_placeholder"
        token = next(iter(evidence.get("invented_tokens", [])), "")
        return f"{code}|{normalize(token)}"
    return "|".join((issue.category, normalize(issue.region), normalize(issue.dimension)))


_GROUPED_GATE_CODES = {
    "missing_source_content", "duplicated_source_content",
    "invalid_source_block_identity", "duplicate_source_block",
}


def _consolidate_gate_failures(failures: list[GateFailure]) -> list[GateFailure]:
    """Owner rule 2026-09-08: one scratch repair must be able to fix a whole
    class of content findings (e.g. every duplicated line) in a single call,
    so per-line findings of the same code merge into one grouped failure."""
    consolidated: list[GateFailure] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for failure in failures:
        if failure.code in _GROUPED_GATE_CODES and isinstance(failure.details, dict):
            if failure.code in {"missing_source_content", "duplicated_source_content"}:
                row = {
                    key: failure.details.get(key)
                    for key in ("source_line_id", "source_text", "missing_tokens", "excess_tokens")
                    if failure.details.get(key) is not None
                }
                nodes = failure.details.get("offending_nodes") or failure.details.get("relevant_fragments") or []
                if nodes:
                    row["offending_nodes"] = nodes[:3]
                destinations = failure.details.get("allowed_destination_nodes") or []
                if destinations:
                    row["allowed_destination_nodes"] = destinations[:1]
            else:
                row = dict(failure.details)
            grouped.setdefault(failure.code, []).append(row)
            continue
        consolidated.append(failure)
    for code, rows in grouped.items():
        consolidated.insert(0, GateFailure(code=code, details=rows))
    return consolidated


def _compact_gate_details(details: Any) -> str:
    if isinstance(details, list) and details and isinstance(details[0], dict):
        # Grouped rows: keep every row and its mutation roots intact (the roots
        # are the repair contract); consolidation already capped node counts.
        details = [dict(row) for row in details]
    elif isinstance(details, dict):
        placeholder = details.get("placeholder_derived") is True
        details = {
            key: value if placeholder and key in {"invented_tokens", "offending_nodes"}
            else value[:8] if isinstance(value, list) else value
            for key, value in details.items()
            if value not in (None, [], {}) and (value is not True or key == "placeholder_derived")
        }
    elif isinstance(details, list):
        details = details[:8]
    return json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)


def gate_failure_issue(failure: GateFailure) -> VisualIssue:
    content_codes = {
        "missing_source_content", "duplicated_source_content", "invented_content",
        "source_block_assignment", "source_record_assignment", "source_reading_order",
        "invalid_candidate_html", "missing_html_content", "missing_pdf_content",
        "nested_source_annotations", "duplicate_source_block", "document_has_no_section_heading",
        "invalid_source_block_identity", "missing_source_heading", "unknown_source_line",
        "section_semantic_mismatch", "slot_semantic_mismatch",
        "source_block_split", "header_semantic_mismatch",
        "layout_render_1", "layout_render_2",
    }
    category = "content_integrity" if failure.code in content_codes else "structure"
    return VisualIssue(
        region="deterministic_gate", category=category, dimension=failure.code,
        description=(
            f"Repair EVERY listed occurrence of {failure.code} in one pass; the row list is authoritative"
            if isinstance(failure.details, list) and failure.details and isinstance(failure.details[0], dict)
            else f"Repair the exact failed invariant: {failure.code}"
        ),
        evidence=_compact_gate_details(failure.details),
        suggested_action="reassign_source" if category == "content_integrity" else "select_existing_variant",
    )


def select_issue(issues: list[VisualIssue]) -> VisualIssue | None:
    priority = {"content_integrity": 0, "semantic_placement": 1, "structure": 2, "presentation": 3}
    unique = {issue_fingerprint(issue): issue for issue in reversed(issues)}
    return min(unique.values(), key=lambda issue: (priority[issue.category], issue_fingerprint(issue)), default=None)


def route_issue(issue: VisualIssue, ledger: dict[str, dict[str, Any]]) -> tuple[str, str] | None:
    row = ledger.get(issue_fingerprint(issue), {})
    if issue.suggested_action in FILLER_ACTIONS:
        actor, action = "Filler", issue.suggested_action
    elif issue.suggested_action in BUILDER_ACTIONS:
        actor, action = "Builder", issue.suggested_action
    else:
        # Owner decision 2026-09-08: the Filler edits full candidate HTML with
        # global content scope, so an unclassifiable action goes to the Filler
        # (a "Builder + unknown" pair would demand measurements it cannot have).
        actor, action = "Filler", "reassign_source"
    failures = sum(
        item.get("actor") == actor and item.get("action") == action and not item.get("promoted", False)
        for item in row.get("outcomes", [])
    )
    return None if failures >= 4 else (actor, action)


def update_issue_ledger(ledger: dict[str, dict[str, Any]], issue: VisualIssue, round_number: int, *, actor: str | None = None, action: str | None = None, resolved_measurements: list[dict[str, Any]] | None = None, gate_result: bool | None = None, promoted: bool | None = None, outcome: str | None = None) -> dict[str, Any]:
    fingerprint = issue_fingerprint(issue)
    row = ledger.setdefault(fingerprint, {
        "issue_fingerprint": fingerprint, "first_seen_round": round_number,
        "last_seen_round": round_number - 1, "current_streak": 0,
        "actors_attempted": [], "actions_attempted": [], "resolved_measurements": [],
        "gate_result": None, "promotion_result": None, "last_outcome": None, "outcomes": [],
    })
    row["current_streak"] = row["current_streak"] + 1 if row["last_seen_round"] == round_number - 1 else 1
    row["last_seen_round"] = round_number
    if actor:
        row["actors_attempted"].append(actor)
    if action:
        row["actions_attempted"].append(action)
    if resolved_measurements is not None:
        row["resolved_measurements"] = resolved_measurements
    if gate_result is not None:
        row["gate_result"] = gate_result
    if promoted is not None:
        row["promotion_result"] = promoted
    if outcome is not None:
        row["last_outcome"] = outcome
    if actor and action and outcome is not None:
        row["outcomes"].append({"round": round_number, "actor": actor, "action": action, "gate_passed": gate_result, "promoted": promoted, "outcome": outcome})
    return row


def _style_blocks(document: str) -> list[str]:
    return re.findall(r"<style\b[^>]*>(.*?)</style>", document, re.I | re.S)


def _class_names(document: str) -> set[str]:
    return {name for value in re.findall(r"\bclass=[\"']([^\"']*)", document, re.I) for name in value.split()}


def _declared_class_names(document: str) -> set[str]:
    return {name for stylesheet in _style_blocks(document) for name in re.findall(r"(?<![\w-])\.([A-Za-z_][\w-]*)", stylesheet)}


def validate_filler_conformance(template_html: str, candidate_html: str) -> list[str]:
    errors: list[str] = []
    if _style_blocks(candidate_html) != _style_blocks(template_html):
        errors.append("Filler modified or added <style> content")
    if re.findall(r"<link\b[^>]*rel=[\"']?stylesheet[^>]*>", candidate_html, re.I) != re.findall(r"<link\b[^>]*rel=[\"']?stylesheet[^>]*>", template_html, re.I):
        errors.append("Filler modified or added a stylesheet")
    # Only a NEW or modified style string is a modification. A lost template
    # inline style is a visual defect the Reviewer sees; the template's style
    # blocks themselves remain frozen and are restored by code.
    if {value.strip() for value in re.findall(r"\sstyle=[\"'][^\"']*[\"']", candidate_html, re.I)} - {
        value.strip() for value in re.findall(r"\sstyle=[\"'][^\"']*[\"']", template_html, re.I)
    }:
        errors.append("Filler modified or added inline styles")
    # Owner rule 2026-09-08: Font Awesome utility classes (fa-*) are icon
    # semantics following the template's own convention, not CSS hooks — an
    # unmatched one renders nothing. They cannot introduce styling.
    new_classes = sorted(
        name for name in _class_names(candidate_html) - (_class_names(template_html) | _declared_class_names(template_html))
        if not name.startswith("fa-")
    )
    if new_classes:
        errors.append("Filler introduced CSS classes: " + ", ".join(new_classes))
    return errors


def validate_scratch_repair(scratch_draft_html: str, repaired_html: str, issue: VisualIssue) -> list[str]:
    """Compare parsed trees after masking only the issue's allowed mutable roots."""
    if issue.category == "content_integrity":
        # Content repairs may need to regroup the whole candidate DOM. CSS and
        # assets remain frozen, and the complete content/layout gates rerun.
        return []
    details = json.loads(issue.evidence)
    grouped_rows = details if isinstance(details, list) and details and isinstance(details[0], dict) else None
    evidence_rows = grouped_rows or [details]

    def _nodes(key: str) -> list[dict[str, Any]]:
        return [row for evidence_row in evidence_rows for row in (evidence_row.get(key, []) or []) if isinstance(row, dict)]

    mutable_paths = {
        row["node"] for row in _nodes("offending_nodes") + _nodes("relevant_fragments") + _nodes("allowed_destination_nodes")
        if row.get("node")
    }
    source_line_ids = {
        row.get("source_line_id") for row in evidence_rows
        if isinstance(row, dict) and row.get("source_line_id")
    }
    if not mutable_paths and not source_line_ids:
        return ["scratch repair has no allowed mutable roots"]
    destination_blocks = {
        row.get("ancestry", {}).get("source_block")
        for row in _nodes("allowed_destination_nodes")
        if row.get("ancestry", {}).get("source_block")
    }
    destination_records = {
        row.get("ancestry", {}).get("source_record")
        for row in _nodes("allowed_destination_nodes")
        if row.get("ancestry", {}).get("source_record")
    }

    def canonical(document: str) -> bytes:
        root = lxml_html.document_fromstring(document)
        tree = root.getroottree()
        selected = []
        for path in mutable_paths:
            selected.extend(tree.xpath(path))
        for owner in destination_blocks:
            selected.extend(root.xpath(f"//*[@data-source-block='{owner}']"))
        for owner in destination_records:
            selected.extend(root.xpath(f"//*[@data-source-record='{owner}']"))
        for line_id in source_line_ids:
            selected.extend(root.xpath(f"//*[@data-source-line='{line_id}']"))
        selected = list({id(node): node for node in selected}.values())
        selected = [node for node in selected if not any(parent in selected for parent in node.iterancestors())]
        for node in selected:
            parent = node.getparent()
            if parent is None:
                continue
            if node.tail:
                previous = node.getprevious()
                if previous is not None:
                    previous.tail = (previous.tail or "") + node.tail
                else:
                    parent.text = (parent.text or "") + node.tail
            parent.remove(node)
        return etree.tostring(root, method="c14n", with_comments=False)

    try:
        return [] if canonical(scratch_draft_html) == canonical(repaired_html) else ["scratch repair changed DOM outside allowed mutable roots"]
    except (etree.ParserError, ValueError, TypeError) as error:
        return [f"scratch repair HTML comparison failed: {error}"]


def apply_placement_operation(champion: CandidateState, payload: Any, expected_action: str) -> CandidateState:
    operation = PlacementOperation.model_validate(payload)
    if operation.action != expected_action:
        raise ValueError("Placement operation action does not match the routed action")
    match = re.fullmatch(r"\[data-(section|slot|repeatable|variant|source-block|source-record)=['\"]([^'\"]+)['\"]\]", operation.destination_selector)
    if not match:
        raise ValueError("Placement destination must be one controlled data-* selector")
    tree = lxml_html.document_fromstring(champion.filled_html)
    sources = tree.xpath(f"//*[@data-source-line='{operation.source_line_id}']")
    destinations = tree.xpath(f"//*[@data-{match.group(1)}='{match.group(2)}']")
    if not sources or len(destinations) != 1:
        raise ValueError("Placement requires an existing source line and exactly one destination")
    destination = destinations[0]
    if any(destination is source or destination in source.iterdescendants() for source in sources):
        raise ValueError("Placement destination cannot be inside the moved source fragment")
    for source in sources:
        destination.append(source)
    return CandidateState(champion.template_html, etree.tostring(tree, encoding="unicode", method="html"), champion.template_sha256, list(champion.builder_changes))


def validate_builder_operation(payload: Any, template_html: str, resolved_measurements: list[dict[str, Any]], expected_action: str | None = None) -> BuilderOperation:
    if not isinstance(payload, dict):
        raise ValueError("Builder must return exactly one operation object")
    operation = BuilderOperation.model_validate(payload)
    if expected_action is not None and operation.action != expected_action:
        raise ValueError("Builder operation action does not match the routed action")
    allowed = BUILDER_PROPERTIES[operation.action]
    if operation.property not in allowed:
        raise ValueError(f"Property {operation.property!r} is not allowed for {operation.action}")
    if not operation.measurement_ref.endswith(allowed[operation.property]):
        raise ValueError("Measurement reference does not match the selected property")
    measurement = {row["measurement_ref"]: row for row in resolved_measurements}.get(operation.measurement_ref)
    if not measurement or not isinstance(measurement.get("value"), (int, float)) or isinstance(measurement.get("value"), bool):
        raise ValueError("Builder measurement reference is unresolved or non-numeric")
    if measurement.get("unit") != "pt":
        raise ValueError("Builder measurement must use pt")
    class_match = re.fullmatch(r"\.([A-Za-z_][\w-]*)", operation.selector)
    data_match = re.fullmatch(r"\[data-([\w-]+)=(?:'([^']+)'|\"([^\"]+)\")\]", operation.selector)
    exists = bool(class_match and class_match.group(1) in (_class_names(template_html) | _declared_class_names(template_html)))
    if data_match:
        attribute, value = data_match.group(1), data_match.group(2) or data_match.group(3)
        exists = attribute in {"section", "slot", "repeatable", "variant"} and bool(re.search(rf"\bdata-{re.escape(attribute)}=[\"']{re.escape(value)}[\"']", template_html, re.I))
    if not exists:
        raise ValueError("Builder selector does not exist in the current template")
    return operation


def apply_builder_operation(state: CandidateState, operation: BuilderOperation, resolved_measurements: list[dict[str, Any]]) -> CandidateState:
    measurement = next(row for row in resolved_measurements if row["measurement_ref"] == operation.measurement_ref)
    override = f"\n{operation.selector} {{ {operation.property}: {measurement['value']}pt; }}\n"

    def apply(document: str) -> str:
        if not re.search(r"</style>", document, re.I):
            raise ValueError("Builder cannot apply CSS: template has no style block")
        return re.sub(r"</style>", lambda match: override + match.group(0), document, count=1, flags=re.I)

    change = {**operation.model_dump(mode="json"), "resolved_value": measurement["value"], "unit": "pt", "provenance": measurement["provenance"]}
    template_html = apply(state.template_html)
    digest = hashlib.sha256(template_html.encode()).hexdigest()
    return CandidateState(template_html, apply(state.filled_html), digest, [*state.builder_changes, change])
