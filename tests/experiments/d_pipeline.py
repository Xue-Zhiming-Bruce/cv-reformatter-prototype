"""Pipeline D0 — bounded agent-directed layout repair experiment.

Owner-authorized experiment under `tests/experiments/` (see
D_PIPELINE_PROPOSAL.md §11 "Minimal Experimental Slice" and §12 "D0: prove
agent-directed routing"). Not part of the active product architecture.

Hypothesis under test (work order, 2026-09):

    A bounded main agent can inspect versioned evidence, delegate a narrow
    diagnosis or repair task, and select a typed edit that fixes one known
    layout defect without changing candidate content or regressing
    unaffected nodes.

Initial defect: `section_heading_rule_placement_mismatch`, reproduced from
the committed C1 matrix artifacts of the E→D terminal run
(`runs/c1_matrix_ED_B_20260911T044203Z`): the target resume_D.pdf places a
horizontal rule BELOW each section heading (heading→rule ≈1.742pt,
rule→content ≈10.46pt, content-independent local gaps per evolution
proposal §10 ruling 1); the accepted render places the section rules ABOVE
the headings. The C1 machine gates passed because they only measured rules
above headings — exactly the relationship-defect class the 2026-09-10 owner
matrix final review documented (evolution proposal §8.2, "rule 位置").

Runtime shape (deterministic outer state machine, agent only at explicit
checkpoints):

    initialized -> evidence_ready -> diagnosis_ready -> patch_proposed
    -> candidate_rendered -> validated -> accepted | rejected
    | needs_human_review

Layer doctrine (D_PIPELINE_PROPOSAL.md §2): agents reason and propose;
deterministic code measures, mutates, renders and validates. The agent never
overwrites canonical HTML directly — it selects one typed EditAction
(`SetHeadingRule`); the shell schema/policy-validates it, applies it to a
candidate version, re-renders, validates, and promotes or discards.

Dependencies: PydanticAI is an experiment-only dependency
(`pydantic-ai-slim[openai]`, proposal §4.1); it is intentionally NOT added to
pyproject.toml product dependencies. Everything else reuses the existing
experiment stacks (a_pipeline Chrome export, c_pipeline measurement).

Usage:

    .venv/bin/python -m tests.experiments.d_pipeline \
        --base-run <committed C1 run dir> [--live] [--out DIR]

Without `--live` the agents run on scripted offline models (PydanticAI
FunctionModel) — a zero-API rehearsal that still performs real Chrome
renders and real measurement. `--live` uses the DeepSeek-compatible
endpoint configured via DEEPSEEK_API_KEY / OPENAI_API_KEY.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from lxml import etree, html as lxml_html
from pydantic import BaseModel, Field
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext

from tests.experiments.a_pipeline import (
    RUNS,
    _export_pinned_html_to_pdf,
    _html_text,
    _render_pages,
    _side_by_side,
)
from tests.experiments.c_pipeline import (
    _pdf_lines_and_marks,
    per_line_render_stability,
    pinned_export_environment,
)

# --- bounded experiment constants (proposal §10 control rules) --------------

DEFECT_CLASS = "section_heading_rule_placement_mismatch"
GAP_TOLERANCE_PT = 1.0  # evolution proposal §10 ruling 1: local y gaps, |Δ| ≤ 1pt
MAX_REPAIR_ATTEMPTS = 2  # ≤2 automatic repair attempts per finding
MAX_FIT_RENDERS = 4  # zero-API deterministic knob fitting budget
MAX_LINE_SHIFT_PT = 30.0  # downstream reflow must stay bounded/explainable
REFLOW_TOLERANCE_PT = 0.5
CHECKPOINT_REQUEST_LIMIT = 12  # §10 bounded budgets: one checkpoint may not loop
# DeepSeek rejects structured-output tool_choice in thinking mode; the A/B/C
# pipelines disable thinking the same way (deepseek.py extra_body).
LIVE_MODEL_SETTINGS = {"extra_body": {"thinking": {"type": "disabled"}}}
DEFAULT_BASE_RUN = RUNS / "c1_matrix_ED_B_20260911T044203Z"
ROOT = Path(__file__).resolve().parents[2]
MAIN_WORKSPACE = Path("/Users/xuezhiming/Desktop/CV converter prototype")


# --- typed contracts ---------------------------------------------------------


class HeadingRuleFact(BaseModel):
    """Measured rule relation for one rendered/target section heading."""

    node_id: str
    heading_verbatim: str
    page: int
    placement: Literal["below", "above", "none"]
    gap_heading_to_rule_pt: float | None = None  # below: rule.top - heading.bottom
    gap_rule_above_pt: float | None = None  # above: heading.top - rule.bottom
    gap_rule_to_content_pt: float | None = None
    gap_prev_content_to_heading_pt: float | None = None  # informational
    rule_x0_pt: float | None = None
    rule_x1_pt: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class TargetRuleDesign(BaseModel):
    """Content-independent local gap targets measured from the target PDF."""

    placement: Literal["below", "above", "none"]
    gap_heading_to_rule_pt: float
    gap_rule_to_content_pt: float
    rule_x0_pt: float | None = None
    rule_x1_pt: float | None = None
    per_heading: list[HeadingRuleFact] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class SetHeadingRuleChanges(BaseModel):
    placement: Literal["below", "above"]
    gap_heading_pt: float = Field(ge=0.0, le=40.0)
    gap_content_pt: float = Field(ge=0.0, le=40.0)


class SetHeadingRuleEdit(BaseModel):
    """The single supported EditAction type for D0 (proposal §11 item 5)."""

    type: Literal["SetHeadingRule"]
    target_node_id: str
    scope: Literal["node", "template_role"]
    base_layout_version_id: str
    changes: SetHeadingRuleChanges
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = ""


class PatchProposal(BaseModel):
    edit: SetHeadingRuleEdit | None = None
    capability_gap: str | None = None


class DefectDiagnosis(BaseModel):
    defect_class: Literal[
        "section_heading_rule_placement_mismatch",
        "unclassified",
    ]
    node_ids: list[str]
    claim: str
    measurable: bool
    evidence_ids: list[str] = Field(default_factory=list)


class ReviewFindings(BaseModel):
    """Visual reviewer is a defect detector, not an acceptance authority."""

    findings: list[dict[str, Any]] = Field(default_factory=list)
    overall_note: str = ""


class OrchestratorDecision(BaseModel):
    action: Literal[
        "delegate_diagnosis",
        "delegate_repair",
        "accept",
        "reject",
        "needs_human_review",
    ]
    note: str = ""


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    details: Any = None


class ValidationReport(BaseModel):
    passed: bool = True
    checks: list[ValidationCheck] = Field(default_factory=list)

    def add(self, name: str, passed: bool, details: Any = None) -> None:
        self.checks.append(ValidationCheck(name=name, passed=passed, details=details))
        if not passed:
            self.passed = False


# --- deterministic measurement -----------------------------------------------


def _heading_line(lines: list[dict[str, Any]], verbatim: str) -> dict[str, Any] | None:
    want = verbatim.strip().upper()
    return next((l for l in lines if l["text"].strip().upper() == want), None)


def _nearest_rule_below(rules: list[dict[str, Any]], line: dict[str, Any]) -> dict[str, Any] | None:
    below = [
        r for r in rules if r["page"] == line["page"] and 0 <= r["top"] - line["bottom"] < 15
    ]
    return min(below, key=lambda r: r["top"] - line["bottom"], default=None)


def _nearest_rule_above(rules: list[dict[str, Any]], line: dict[str, Any]) -> dict[str, Any] | None:
    above = [
        r for r in rules if r["page"] == line["page"] and 0 <= line["top"] - r["bottom"] < 15
    ]
    return min(above, key=lambda r: line["top"] - r["bottom"], default=None)


def _first_content_below(
    lines: list[dict[str, Any]], page: int, rule: dict[str, Any], exclude: dict[str, Any]
) -> dict[str, Any] | None:
    candidates = [
        l
        for l in lines
        if l["page"] == page
        and l["top"] > rule["bottom"]
        and 0 < l["top"] - rule["bottom"] < 25
        and l is not exclude
    ]
    return min(candidates, key=lambda l: l["top"] - rule["bottom"], default=None)


def measure_heading_rule_fact(
    pdf: Path, node_id: str, heading_verbatim: str
) -> HeadingRuleFact | None:
    """Measure the heading/rule relation for one node from a rendered PDF."""
    lines, marks = _pdf_lines_and_marks(pdf)
    rules = [m for m in marks if m.get("kind") == "rule"]
    line = _heading_line(lines, heading_verbatim)
    if line is None:
        return None
    below = _nearest_rule_below(rules, line)
    above = _nearest_rule_above(rules, line)
    evidence_ids = [f"render.line.p{line['page']}.top{line['top']:.1f}"]
    if below is not None:
        content = _first_content_below(lines, line["page"], below, line)
        return HeadingRuleFact(
            node_id=node_id,
            heading_verbatim=line["text"].strip(),
            page=line["page"],
            placement="below",
            gap_heading_to_rule_pt=round(below["top"] - line["bottom"], 3),
            gap_rule_to_content_pt=(
                round(content["top"] - below["bottom"], 3) if content else None
            ),
            rule_x0_pt=round(below["x0"], 3),
            rule_x1_pt=round(below["x1"], 3),
            evidence_ids=evidence_ids,
        )
    if above is not None:
        return HeadingRuleFact(
            node_id=node_id,
            heading_verbatim=line["text"].strip(),
            page=line["page"],
            placement="above",
            gap_rule_above_pt=round(line["top"] - above["bottom"], 3),
            rule_x0_pt=round(above["x0"], 3),
            rule_x1_pt=round(above["x1"], 3),
            evidence_ids=evidence_ids,
        )
    return HeadingRuleFact(
        node_id=node_id,
        heading_verbatim=line["text"].strip(),
        page=line["page"],
        placement="none",
        evidence_ids=evidence_ids,
    )


def _prev_content_gap(pdf: Path, heading_verbatim: str) -> float | None:
    lines, _ = _pdf_lines_and_marks(pdf)
    line = _heading_line(lines, heading_verbatim)
    if line is None:
        return None
    prev = [
        l
        for l in lines
        if l["page"] == line["page"] and l["bottom"] <= line["top"] + 0.5 and l is not line
    ]
    p = max(prev, key=lambda l: l["bottom"], default=None)
    return round(line["top"] - p["bottom"], 3) if p else None


def discover_section_nodes(candidate_html: str) -> list[dict[str, str]]:
    """Stable logical node inventory (proposal §16.5): section.<name>.heading."""
    tree = lxml_html.document_fromstring(candidate_html)
    nodes: list[dict[str, str]] = []
    for section in tree.xpath("//div[@class='section']"):
        headings = section.xpath("./h2[contains(@class,'section-heading')]")
        if not headings:
            continue
        heading = headings[0]
        text = " ".join(str(heading.text_content()).split())
        nodes.append(
            {
                "node_id": f"section.{section.get('data-section')}.heading",
                "heading_verbatim": text,
                "data_source_line": heading.get("data-source-line") or "",
            }
        )
    return nodes


def measure_target_design(target_pdf: Path, base_run_dir: Path) -> TargetRuleDesign:
    """Measure the target's content-independent heading-rule design.

    Heading verbatims come from the committed C1 body scaffold (Adobe-derived
    evidence); the local gaps come from fresh pdfplumber measurement of the
    target PDF. The inline SUMMARY lead heading (carrying a `—` lead) is
    excluded: it is not a pure heading node.
    """
    scaffold_path = base_run_dir / "body_scaffold.json"
    headings: list[dict[str, Any]] = []
    if scaffold_path.exists():
        headings = json.loads(scaffold_path.read_text(encoding="utf-8")).get("headings", [])
    verbatims = [
        h["verbatim"] for h in headings if h.get("verbatim") and "—" not in h["verbatim"]
    ]
    if not verbatims:
        raise RuntimeError("target heading evidence missing: body_scaffold.json has no headings")
    facts: list[HeadingRuleFact] = []
    for verbatim in verbatims:
        fact = measure_heading_rule_fact(target_pdf, f"target.{verbatim}", verbatim)
        if fact is not None:
            fact.gap_prev_content_to_heading_pt = _prev_content_gap(target_pdf, verbatim)
            facts.append(fact)
    below = [f for f in facts if f.placement == "below" and f.gap_heading_to_rule_pt is not None]
    if below:
        gaps = sorted(f.gap_heading_to_rule_pt for f in below)
        content = sorted(
            f.gap_rule_to_content_pt for f in below if f.gap_rule_to_content_pt is not None
        )
        return TargetRuleDesign(
            placement="below",
            gap_heading_to_rule_pt=gaps[len(gaps) // 2],
            gap_rule_to_content_pt=content[len(content) // 2] if content else 0.0,
            rule_x0_pt=below[0].rule_x0_pt,
            rule_x1_pt=below[0].rule_x1_pt,
            per_heading=facts,
            evidence_ids=[e for f in facts for e in f.evidence_ids],
        )
    raise RuntimeError(
        "target PDF shows no rule-below-heading design; the initial D0 defect "
        "is not reproducible from this evidence (stopping per work order)"
    )


# --- deterministic mutation ---------------------------------------------------


def _hr_is_section_rule(element: Any) -> bool:
    """Section-owned rules are the green hr variants; the plain amber `.hr`
    after the header block is the header separator and is never touched."""
    return element.tag == "hr" and "hr--green" in (element.get("class") or "")


def apply_set_heading_rule(
    candidate_html: str, edit: SetHeadingRuleEdit, knobs: dict[str, dict[str, float]]
) -> str:
    """Apply one typed SetHeadingRule edit to a candidate HTML version.

    Deterministic: moves each section-owned `<hr class="hr hr--green">` from
    before the section to directly after the heading (placement "below"), or
    inserts a new one when the heading has no section rule of its own. The hr
    `margin-top` owns the measured heading→rule gap, `margin-bottom` the
    rule→content gap; knobs are fitted by `fit_heading_rule_gaps`. Only the
    diagnosed nodes and their declared dependent style attributes are
    touched — visible text is never modified.
    """
    if edit.changes.placement != "below":
        raise NotImplementedError("D0 supports placement='below' only")
    tree = lxml_html.document_fromstring(candidate_html)
    touched = 0
    for section in tree.xpath("//div[@class='section']"):
        node_id = f"section.{section.get('data-section')}.heading"
        if edit.scope == "node" and node_id != edit.target_node_id:
            continue
        headings = section.xpath("./h2[contains(@class,'section-heading')]")
        if not headings:
            continue
        h2 = headings[0]
        if h2.get("style"):
            raise RuntimeError(f"unexpected inline style on {node_id}; failing closed")
        knob = knobs.setdefault(node_id, {"margin_top": 0.0, "margin_bottom": 2.0})
        prev = section.getprevious()
        if prev is not None and _hr_is_section_rule(prev):
            section.getparent().remove(prev)
            h2.addnext(prev)
        else:
            rule = etree.Element("hr")
            rule.set("class", "hr hr--green")
            h2.addnext(rule)
        # h2 margin-bottom only governed the (now relocated) rule spacing; the
        # fitted hr margins own both local gaps from now on.
        h2.set("style", "margin-bottom:0")
        h2.getnext().set(
            "style",
            f'margin:{knob["margin_top"]:.3f}pt 0 {knob["margin_bottom"]:.3f}pt 0',
        )
        touched += 1
    if touched == 0:
        raise RuntimeError(f"SetHeadingRule matched no nodes for {edit.target_node_id!r}")
    return etree.tostring(tree, encoding="unicode", method="html")


def fit_heading_rule_gaps(
    base_html: str,
    edit: SetHeadingRuleEdit,
    render_and_measure: Any,
) -> tuple[str, dict[str, dict[str, float]], list[HeadingRuleFact], int]:
    """Zero-API deterministic knob fit (C1's knob approach).

    Each render: measure per-node local gaps, correct the linear margin knobs
    by the residual, verify. Bounded by MAX_FIT_RENDERS; tolerance per §10.
    """
    knobs: dict[str, dict[str, float]] = {}
    html = base_html
    facts: list[HeadingRuleFact] = []
    for render_number in range(MAX_FIT_RENDERS):
        html = apply_set_heading_rule(base_html, edit, knobs)
        facts = render_and_measure(html, render_number)
        residual = False
        for fact in facts:
            knob = knobs[fact.node_id]
            if fact.gap_heading_to_rule_pt is not None:
                delta = edit.changes.gap_heading_pt - fact.gap_heading_to_rule_pt
                if abs(delta) > REFLOW_TOLERANCE_PT:
                    knob["margin_top"] = round(knob["margin_top"] + delta, 3)
                    residual = True
            if fact.gap_rule_to_content_pt is not None:
                delta = edit.changes.gap_content_pt - fact.gap_rule_to_content_pt
                if abs(delta) > REFLOW_TOLERANCE_PT:
                    knob["margin_bottom"] = round(knob["margin_bottom"] + delta, 3)
                    residual = True
        if not residual:
            break
    return html, knobs, facts, render_number + 1


# --- deterministic validation -------------------------------------------------


def _pdf_text(pdf: Path) -> str:
    import pdfplumber

    with pdfplumber.open(pdf) as document:
        return "\n".join(page.extract_text() or "" for page in document.pages)


def _line_table(pdf: Path) -> list[dict[str, Any]]:
    lines, _ = _pdf_lines_and_marks(pdf)
    return [
        {
            "page": l["page"],
            "top": l["top"],
            "bottom": l["bottom"],
            "x0": l["x0"],
            "x1": l["x1"],
            "text": l["text"],
        }
        for l in lines
    ]


def _header_region_rules(pdf: Path, heading_verbatims: list[str]) -> list[dict[str, Any]]:
    """Rules NOT owned by a section heading (header-region separators). A rule
    adjacent to a section heading on either side is section-owned, so the
    comparison survives the placement repair itself."""
    lines, marks = _pdf_lines_and_marks(pdf)
    rules = [m for m in marks if m.get("kind") == "rule"]
    heading_keys = {v.strip().upper() for v in heading_verbatims}
    owned: set[int] = set()
    for index, rule in enumerate(rules):
        for line in lines:
            below_heading = 0 <= rule["top"] - line["bottom"] < 15
            above_heading = 0 <= line["top"] - rule["bottom"] < 15
            if (
                line["page"] == rule["page"]
                and (below_heading or above_heading)
                and line["text"].strip().upper() in heading_keys
            ):
                owned.add(index)
                break
    return [
        {"page": r["page"], "top": round(r["top"], 2), "x0": round(r["x0"], 2)}
        for index, r in enumerate(rules)
        if index not in owned
    ]


def validate_candidate(
    base_pdf: Path,
    first_pdf: Path,
    second_pdf: Path,
    base_html: str,
    candidate_html: str,
    nodes: list[dict[str, str]],
    design: TargetRuleDesign,
    facts: list[HeadingRuleFact],
) -> ValidationReport:
    """Independent quality gates for the candidate version (proposal §10)."""
    report = ValidationReport()
    verbatims = [n["heading_verbatim"] for n in nodes]

    # 1. dual-render stability (render-aware, owner ruling 2026-09-10)
    stability_ok, stability_details = per_line_render_stability(first_pdf, second_pdf)
    report.add("dual_render_stability", stability_ok, stability_details)

    # 2. candidate content unchanged (rendered text multiset)
    report.add("render_text_unchanged", _pdf_text(base_pdf) == _pdf_text(first_pdf))

    # 3. visible HTML text unchanged (the edit may not touch content)
    report.add(
        "html_visible_text_unchanged",
        _html_text(base_html)[0] == _html_text(candidate_html)[0],
    )

    base_lines = _line_table(base_pdf)
    cand_lines = _line_table(first_pdf)

    # 4. every line's x0 unchanged
    def _x0_index(table: list[dict[str, Any]]) -> dict[tuple[int, str], list[float]]:
        index: dict[tuple[int, str], list[float]] = {}
        for line in table:
            index.setdefault((line["page"], line["text"]), []).append(round(line["x0"], 2))
        return index

    report.add("line_x0_stability", _x0_index(base_lines) == _x0_index(cand_lines))

    # 5. page count unchanged
    pages_base = len({l["page"] for l in base_lines})
    pages_cand = len({l["page"] for l in cand_lines})
    report.add(
        "page_count_unchanged",
        pages_base == pages_cand,
        {"before": pages_base, "after": pages_cand},
    )

    # 6/7/8. repaired local gaps within §10 tolerance, placement below
    fact_by_node = {f.node_id: f for f in facts}
    gap_failures = []
    for node in nodes:
        fact = fact_by_node.get(node["node_id"])
        if fact is None or fact.placement != "below":
            gap_failures.append({"node": node["node_id"], "problem": "rule not below heading"})
            continue
        if abs((fact.gap_heading_to_rule_pt or 99.0) - design.gap_heading_to_rule_pt) > GAP_TOLERANCE_PT:
            gap_failures.append(
                {
                    "node": node["node_id"],
                    "gap_heading_to_rule_pt": fact.gap_heading_to_rule_pt,
                    "target": design.gap_heading_to_rule_pt,
                }
            )
        if abs((fact.gap_rule_to_content_pt or 99.0) - design.gap_rule_to_content_pt) > GAP_TOLERANCE_PT:
            gap_failures.append(
                {
                    "node": node["node_id"],
                    "gap_rule_to_content_pt": fact.gap_rule_to_content_pt,
                    "target": design.gap_rule_to_content_pt,
                }
            )
    report.add("heading_rule_target_gaps", not gap_failures, gap_failures)

    # 9. non-target (header-region) rules unchanged
    header_before = _header_region_rules(base_pdf, verbatims)
    header_after = _header_region_rules(first_pdf, verbatims)
    report.add(
        "header_rules_stability",
        header_before == header_after,
        {"before": header_before, "after": header_after},
    )

    # 10. downstream reflow bounded and explainable (non-negative, capped)
    base_by_key: dict[tuple[int, str], list[float]] = {}
    for line in base_lines:
        base_by_key.setdefault((line["page"], line["text"]), []).append(line["top"])
    bad_shifts = []
    for line in cand_lines:
        tops = base_by_key.get((line["page"], line["text"]))
        if not tops:
            continue
        shift = line["top"] - min(tops, key=lambda t: abs(t - line["top"]))
        if shift < -REFLOW_TOLERANCE_PT or shift > MAX_LINE_SHIFT_PT:
            bad_shifts.append({"line": line["text"][:40], "shift_pt": round(shift, 2)})
    report.add("downstream_reflow_bounded", not bad_shifts, bad_shifts[:10])

    # 11. no orphaned heading (each repaired heading keeps content on its page)
    orphans = [
        node_id
        for node_id, fact in fact_by_node.items()
        if fact.gap_rule_to_content_pt is None
    ]
    report.add("no_orphan_heading", not orphans, orphans)

    return report


# --- agent layer (PydanticAI, experiment-only dependency) ---------------------


def _facts_brief(facts: list[HeadingRuleFact]) -> str:
    return json.dumps([f.model_dump(mode="json") for f in facts], ensure_ascii=False, indent=1)


def build_orchestrator_tools(store: "EvidenceStore", specialists: Any) -> list[Any]:
    """Read-only evidence tools + two bounded delegation tools (§5, §11)."""

    def get_artifact_manifest() -> str:
        """Versioned artifact manifest: versions, active version, state history."""
        return json.dumps(store.manifest, ensure_ascii=False, indent=1)

    def get_target_layout() -> str:
        """Target heading-rule design: measured content-independent local gaps."""
        return store.target_design.model_dump_json(indent=1)

    def inspect_layout_node(node_id: str) -> str:
        """Measured rule facts for one section heading node in the active render."""
        fact = store.facts_for(node_id)
        return fact.model_dump_json(indent=1) if fact else f"unknown node {node_id!r}"

    def compare_target_and_render(node_id: str) -> str:
        """Target design vs active render facts for one node, with delta."""
        return json.dumps(store.compare(node_id), ensure_ascii=False, indent=1)

    async def delegate_evidence_investigation(ctx: RunContext[Any], question: str) -> str:
        """Delegate a bounded read-only diagnosis to the evidence investigator."""
        result = await specialists.investigator.run(store.diagnosis_prompt())
        store.diagnosis = result.output
        return result.output.model_dump_json(indent=1)

    async def delegate_repair_proposal(ctx: RunContext[Any], instruction: str) -> str:
        """Delegate a bounded SetHeadingRule repair proposal to the layout repair agent."""
        result = await specialists.repair.run(store.repair_prompt())
        if isinstance(result.output, PatchProposal) and result.output.edit is not None:
            store.proposals.append(result.output.edit)
        return result.output.model_dump_json(indent=1)

    return [
        get_artifact_manifest,
        get_target_layout,
        inspect_layout_node,
        compare_target_and_render,
        delegate_evidence_investigation,
        delegate_repair_proposal,
    ]


ORCHESTRATOR_INSTRUCTIONS = (
    "You are the main orchestrator of a bounded document-repair state machine. "
    "You choose ONE next bounded action per checkpoint from the allowed actions "
    "stated in the prompt. You may inspect versioned evidence with your read-only "
    "tools first. You must not modify content; repairs are typed edits applied by "
    "deterministic tools. Allowed actions: delegate_diagnosis, delegate_repair, "
    "accept, reject, needs_human_review."
)


class SpecialistAgents:
    """The three fixed specialist roles (proposal §11 item 3)."""

    def __init__(self, model: Any) -> None:
        from pydantic_ai import Agent

        self.investigator = Agent(
            model,
            output_type=DefectDiagnosis,
            name="evidence_investigator",
            model_settings=LIVE_MODEL_SETTINGS,
            instructions=(
                "You are a read-only evidence investigator for a resume layout "
                "experiment. You receive measured facts for the target design and "
                "the current render. Classify the defect. Only one defect "
                f"vocabulary entry exists: '{DEFECT_CLASS}' (the horizontal rule "
                "placement relative to the section heading does not match the "
                "target). Set measurable=true only when numeric deltas are "
                "provided. Cite node ids in node_ids."
            ),
        )
        self.repair = Agent(
            model,
            output_type=PatchProposal,
            name="layout_repair",
            model_settings=LIVE_MODEL_SETTINGS,
            instructions=(
                "You are a bounded layout repair agent. You may only return a "
                "SetHeadingRule proposal (type='SetHeadingRule', target_node_id, "
                "scope, base_layout_version_id, "
                "changes{placement,gap_heading_pt,gap_content_pt}). Use the "
                "measured target design values for the gaps. If the defect cannot "
                "be fixed with SetHeadingRule, return capability_gap instead of "
                "inventing an unsupported action. Never modify candidate text."
            ),
        )
        self.reviewer = Agent(
            model,
            output_type=ReviewFindings,
            name="visual_reviewer",
            model_settings=LIVE_MODEL_SETTINGS,
            instructions=(
                "You are an independent read-only visual reviewer (defect "
                "detector, not an acceptance authority). Inspect the side-by-side "
                "image (left=target, right=generated). Report localized findings, "
                "each with node_id, problem, severity, confidence. Do not demand "
                "target-sample facts as corrections; candidate wording is verbatim "
                "source content."
            ),
        )


def _live_model() -> Any:
    """DeepSeek-compatible model via PydanticAI's OpenAI provider."""
    from dotenv import load_dotenv
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    # experiment-only credential loading (same pattern as the A/B/C pipelines);
    # fall back to the main workspace .env when running from a linked worktree
    load_dotenv(ROOT / ".env")
    load_dotenv(MAIN_WORKSPACE / ".env")

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing live configuration: DEEPSEEK_API_KEY or OPENAI_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    model_name = (
        os.environ.get("D_PIPELINE_MODEL")
        or os.environ.get("C_PIPELINE_MODEL")
        or os.environ.get("B_PIPELINE_MODEL")
        or os.environ.get("A_PIPELINE_MODEL")
        or "deepseek-v4-flash-vision-exp"
    )
    provider = OpenAIProvider(
        openai_client=AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=300)
    )
    return OpenAIChatModel(model_name, provider=provider)


class ScriptedAgents:
    """Offline FunctionModel drivers — deterministic, zero-API rehearsal."""

    def __init__(
        self,
        diagnosis: DefectDiagnosis | None = None,
        proposal: SetHeadingRuleEdit | None = None,
        proposal_2: SetHeadingRuleEdit | None = None,
        final_action: str = "accept",
    ) -> None:
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel

        self.diagnosis = diagnosis
        self.proposals = [p for p in (proposal, proposal_2) if p is not None]
        self.final_action = final_action

        def single_shot(payload: dict[str, Any]) -> FunctionModel:
            def driver(messages: list[Any], info: Any) -> Any:
                from pydantic_ai.messages import ModelResponse, ToolCallPart

                return ModelResponse(parts=[ToolCallPart("final_result", payload)])

            return FunctionModel(driver)

        self.investigator = Agent(
            single_shot(self._diagnosis_payload()), output_type=DefectDiagnosis, name="scripted_investigator"
        )
        repair_calls = {"n": 0}

        def repair_driver(messages: list[Any], info: Any) -> Any:
            from pydantic_ai.messages import ModelResponse, ToolCallPart

            payload = self._proposal_payload(repair_calls["n"])
            repair_calls["n"] += 1
            return ModelResponse(parts=[ToolCallPart("final_result", payload)])

        self.repair = Agent(
            FunctionModel(repair_driver), output_type=PatchProposal, name="scripted_repair"
        )
        self.reviewer = Agent(
            single_shot({"findings": [], "overall_note": "scripted offline review"}),
            output_type=ReviewFindings,
            name="scripted_reviewer",
        )

    def _diagnosis_payload(self) -> dict[str, Any]:
        if self.diagnosis is not None:
            return self.diagnosis.model_dump(mode="json")
        return {
            "defect_class": DEFECT_CLASS,
            "node_ids": ["section.skills.heading"],
            "claim": "section rules placed above headings; target places them below",
            "measurable": True,
        }

    def _proposal_payload(self, call_index: int = 0) -> dict[str, Any]:
        if self.proposals:
            proposal = self.proposals[min(call_index, len(self.proposals) - 1)]
            return {"edit": proposal.model_dump(mode="json")}
        return {
            "edit": {
                "type": "SetHeadingRule",
                "target_node_id": "section.skills.heading",
                "scope": "template_role",
                "base_layout_version_id": "layout_v1",
                "changes": {"placement": "below", "gap_heading_pt": 1.742, "gap_content_pt": 10.463},
            }
        }

    def orchestrator_model_for(self, phase: str) -> Any:
        """Fresh scripted driver per checkpoint: a fixed tool-call plan followed
        by the phase's final decision, so state never leaks between runs."""
        from pydantic_ai.messages import ModelResponse, ToolCallPart
        from pydantic_ai.models.function import FunctionModel

        node = "section.skills.heading"
        steps = {
            "diagnosis": [
                ("tool", "compare_target_and_render", {"node_id": node}),
                ("tool", "delegate_evidence_investigation", {"question": "classify the defect"}),
                ("final", {"action": "delegate_diagnosis", "note": "scripted"}, None),
            ],
            "repair": [
                ("tool", "get_artifact_manifest", {}),
                ("tool", "delegate_repair_proposal", {"instruction": "propose the typed repair"}),
                ("final", {"action": "delegate_repair", "note": "scripted"}, None),
            ],
            "final": [
                ("tool", "inspect_layout_node", {"node_id": node}),
                ("final", {"action": self.final_action, "note": "scripted"}, None),
            ],
        }[phase]
        state = {"index": 0}

        def driver(messages: list[Any], info: Any) -> ModelResponse:
            kind, name_or_payload, args = steps[min(state["index"], len(steps) - 1)]
            state["index"] += 1
            if kind == "tool":
                return ModelResponse(parts=[ToolCallPart(name_or_payload, args)])
            return ModelResponse(parts=[ToolCallPart("final_result", name_or_payload)])

        return FunctionModel(driver)


# --- versioned artifact store --------------------------------------------------


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EvidenceStore:
    """References and authority for the run — never uncontrolled file access."""

    def __init__(self, base_run_dir: Path, out_dir: Path) -> None:
        self.base_run_dir = base_run_dir
        self.out_dir = out_dir
        self.base_html = (base_run_dir / "filled.html").read_text(encoding="utf-8")
        self.nodes = discover_section_nodes(self.base_html)
        self.target_design: TargetRuleDesign | None = None
        self.base_facts: list[HeadingRuleFact] = []
        self.active_facts: list[HeadingRuleFact] = []
        self.reviewing_version_id = "layout_v1"
        self.diagnosis: DefectDiagnosis | None = None
        self.proposals: list[SetHeadingRuleEdit] = []
        self.manifest: dict[str, Any] = {
            "experiment": "d_pipeline_d0",
            "base_run_dir": str(base_run_dir),
            "base_html_sha256": _sha256_file(base_run_dir / "filled.html"),
            "versions": [],
            "active_layout_version_id": "layout_v1",
            "state_history": [],
            "attempts": 0,
        }

    def register_version(self, version_id: str, path: Path, note: str) -> None:
        self.manifest["versions"].append(
            {"id": version_id, "path": path.name, "sha256": _sha256_file(path), "note": note}
        )

    def record_state(self, state: str, note: str = "") -> None:
        self.manifest["state_history"].append(
            {"state": state, "ts": datetime.now(UTC).isoformat(timespec="seconds"), "note": note}
        )

    def node_by_id(self, node_id: str) -> dict[str, str] | None:
        return next((n for n in self.nodes if n["node_id"] == node_id), None)

    def facts_for(self, node_id: str) -> HeadingRuleFact | None:
        return next((f for f in self.active_facts if f.node_id == node_id), None)

    def set_active_view(self, version_id: str, facts: list[HeadingRuleFact]) -> None:
        """Point the agent-facing evidence tools at the version under review.
        Rejection/rollback calls this again with layout_v1's measured facts."""
        self.reviewing_version_id = version_id
        self.active_facts = facts

    def compare(self, node_id: str) -> dict[str, Any]:
        fact = self.facts_for(node_id)
        design = self.target_design
        return {
            "node_id": node_id,
            "reviewing_version": self.reviewing_version_id,
            "target_design": design.model_dump(mode="json") if design else None,
            "active_render_fact": fact.model_dump(mode="json") if fact else None,
            "delta": (
                {"placement": f"target={design.placement} render={fact.placement}"}
                if design and fact
                else None
            ),
        }

    def diagnosis_prompt(self) -> str:
        return (
            "Classify the defect from these measured facts.\n"
            f"Target design:\n{self.target_design.model_dump_json(indent=1)}\n"
            f"Active render facts:\n{_facts_brief(self.active_facts)}\n"
            f"Node inventory: {json.dumps(self.nodes, ensure_ascii=False)}"
        )

    def repair_prompt(self) -> str:
        return (
            "Propose ONE SetHeadingRule edit fixing the diagnosed defect.\n"
            f"Diagnosis: {self.diagnosis.model_dump_json() if self.diagnosis else None}\n"
            f"Target design:\n{self.target_design.model_dump_json(indent=1)}\n"
            f"Active version id: {self.manifest['active_layout_version_id']}\n"
            f"Node inventory: {json.dumps(self.nodes, ensure_ascii=False)}\n"
            "Use scope='template_role' when the defect affects the section-heading "
            "role generally, or scope='node' for exactly one node."
        )


# --- deterministic outer state machine -----------------------------------------


def run_d0(
    base_run_dir: Path = DEFAULT_BASE_RUN,
    out_dir: Path | None = None,
    *,
    live: bool = False,
    max_attempts: int = MAX_REPAIR_ATTEMPTS,
    offline_agents: "ScriptedAgents | None" = None,
) -> tuple[Path, str]:
    """Run the bounded D0 experiment. Returns (run_dir, terminal_state)."""
    from pydantic_ai import Agent
    from pydantic_ai.usage import UsageLimits

    base_run_dir = base_run_dir.resolve()
    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("d_pipeline_d0_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    for name in ("filled.html", "format_summary.json", "body_scaffold.json", "target.pdf"):
        if not (base_run_dir / name).exists():
            raise RuntimeError(f"base run dir missing required artifact: {name}")

    store = EvidenceStore(base_run_dir, out_dir)
    shutil.copy2(base_run_dir / "filled.html", out_dir / "layout_v1.html")
    store.register_version("layout_v1", out_dir / "layout_v1.html", "committed accepted render")
    store.record_state("initialized", f"base={base_run_dir.name}")

    summary = json.loads((base_run_dir / "format_summary.json").read_text(encoding="utf-8"))
    environment = pinned_export_environment(summary)
    target_pdf = base_run_dir / "target.pdf"

    def render(html: str, name: str) -> Path:
        html_path = out_dir / name
        html_path.write_text(html, encoding="utf-8")
        return _export_pinned_html_to_pdf(html_path, html_path.with_suffix(".pdf"), environment)

    if live:
        model = _live_model()
        specialists: Any = SpecialistAgents(model)
        orchestrator_tools = build_orchestrator_tools(store, specialists)
        live_orchestrator = Agent(
            model,
            output_type=OrchestratorDecision,
            name="d0_orchestrator",
            instructions=ORCHESTRATOR_INSTRUCTIONS,
            model_settings=LIVE_MODEL_SETTINGS,
            tools=orchestrator_tools,
        )
    else:
        specialists = offline_agents or ScriptedAgents()
        orchestrator_tools = build_orchestrator_tools(store, specialists)
        live_orchestrator = None

    def run_checkpoint(prompt: str, phase: str, allowed: set[str], max_tries: int = 2) -> OrchestratorDecision:
        """Main agent at one explicit checkpoint; shell enforces the vocabulary."""
        prompt = f"{prompt}\nAt this checkpoint the ONLY allowed final actions are: {sorted(allowed)}."
        agent = live_orchestrator or Agent(
            specialists.orchestrator_model_for(phase),
            output_type=OrchestratorDecision,
            name=f"scripted_orchestrator_{phase}",
            tools=orchestrator_tools,
        )
        last_error = ""
        for _ in range(max_tries):
            text = prompt if not last_error else f"{prompt}\nYour previous decision {last_error!r} was not allowed here."
            decision = agent.run_sync(
                text, usage_limits=UsageLimits(request_limit=CHECKPOINT_REQUEST_LIMIT)
            ).output
            if decision.action in allowed:
                return decision
            last_error = decision.action
        return OrchestratorDecision(
            action="needs_human_review", note=f"out-of-vocabulary decisions: {last_error}"
        )

    # --- evidence_ready -------------------------------------------------------
    store.target_design = measure_target_design(target_pdf, base_run_dir)
    v1_pdf = render(store.base_html, "layout_v1.html")
    v1_pdf_second = render(store.base_html, "layout_v1_second.html")
    stability_ok, stability_details = per_line_render_stability(v1_pdf, v1_pdf_second)
    if not stability_ok:
        raise RuntimeError(f"committed base render is not reproducibly stable: {stability_details}")
    store.active_facts = [
        f
        for node in store.nodes
        if (f := measure_heading_rule_fact(v1_pdf, node["node_id"], node["heading_verbatim"]))
    ]
    (out_dir / "evidence_pack.json").write_text(
        json.dumps(
            {
                "target_design": store.target_design.model_dump(mode="json"),
                "base_render_facts": [f.model_dump(mode="json") for f in store.active_facts],
                "nodes": store.nodes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    store.record_state("evidence_ready")
    store.base_facts = list(store.active_facts)
    store.set_active_view("layout_v1", store.active_facts)

    defect_reproduced = store.target_design.placement == "below" and any(
        f.placement == "above" for f in store.active_facts
    )
    if not defect_reproduced:
        store.record_state("needs_human_review", "defect not reproduced from committed evidence")
        _write_reports(store, out_dir, "needs_human_review", base_run_dir, report=None)
        return out_dir, "needs_human_review"

    # --- diagnosis_ready --------------------------------------------------------
    decision = run_checkpoint(
        "Checkpoint 1 (state=evidence_ready). A measurable rule-placement defect is "
        "confirmed in the evidence pack. Choose the next action.",
        "diagnosis",
        {"delegate_diagnosis", "needs_human_review"},
    )
    if decision.action != "delegate_diagnosis" or store.diagnosis is None:
        store.record_state("needs_human_review", f"orchestrator: {decision.note}")
        _write_reports(store, out_dir, "needs_human_review", base_run_dir, report=None)
        return out_dir, "needs_human_review"

    # deterministic claim verifier: the diagnosis must be measurable and real
    verified_nodes = []
    if store.diagnosis.defect_class == DEFECT_CLASS and store.diagnosis.measurable:
        for node_id in store.diagnosis.node_ids:
            node = store.node_by_id(node_id)
            fact = store.facts_for(node_id)
            if node and fact and fact.placement == "above":
                verified_nodes.append(node_id)
    if not verified_nodes:
        store.record_state("needs_human_review", "diagnosis not verifiable against measured facts")
        _write_reports(store, out_dir, "needs_human_review", base_run_dir, report=None)
        return out_dir, "needs_human_review"
    (out_dir / "diagnosis.json").write_text(store.diagnosis.model_dump_json(indent=2), encoding="utf-8")
    store.record_state("diagnosis_ready", f"verified nodes: {','.join(verified_nodes)}")

    # --- bounded repair attempts -------------------------------------------------
    previous_fingerprint: str | None = None
    last_report: ValidationReport | None = None
    terminal = "needs_human_review"

    for attempt in range(1, max_attempts + 1):
        store.manifest["attempts"] = attempt
        decision = run_checkpoint(
            "Checkpoint 2 (state=diagnosis_ready). Delegate the bounded repair "
            f"proposal. Attempt {attempt} of {max_attempts}.",
            "repair",
            {"delegate_repair", "needs_human_review"},
        )
        if decision.action != "delegate_repair" or not store.proposals:
            store.record_state("needs_human_review", f"orchestrator: {decision.note}")
            terminal = "needs_human_review"
            break
        edit = store.proposals[-1]
        (out_dir / f"patch_proposal_attempt_{attempt}.json").write_text(
            edit.model_dump_json(indent=2), encoding="utf-8"
        )
        fingerprint = hashlib.sha256(
            edit.model_dump_json(exclude={"rationale", "evidence_ids"}).encode()
        ).hexdigest()
        policy_failure = _validate_patch_policy(store, edit)
        if policy_failure:
            store.record_state("needs_human_review", f"patch policy: {policy_failure}")
            terminal = "needs_human_review"
            break
        if fingerprint == previous_fingerprint:
            store.record_state("needs_human_review", "repeated repair fingerprint; stopping per proposal §10")
            terminal = "needs_human_review"
            break
        previous_fingerprint = fingerprint
        store.record_state("patch_proposed", f"attempt {attempt}: {edit.target_node_id} scope={edit.scope}")

        # --- candidate_rendered (deterministic apply + zero-API fit) -------------
        def render_and_measure(html: str, render_number: int) -> list[HeadingRuleFact]:
            pdf = render(html, f"fit_attempt_{attempt}_{render_number:02d}.html")
            return [
                f
                for node in store.nodes
                if (f := measure_heading_rule_fact(pdf, node["node_id"], node["heading_verbatim"]))
            ]

        candidate_html, knobs, fit_facts, fit_renders = fit_heading_rule_gaps(
            store.base_html, edit, render_and_measure
        )
        candidate_id = f"layout_v{attempt + 1}_candidate"
        candidate_path = out_dir / f"{candidate_id}.html"
        candidate_path.write_text(candidate_html, encoding="utf-8")
        first_pdf = render(candidate_html, f"{candidate_id}_first.html")
        second_pdf = render(candidate_html, f"{candidate_id}_second.html")
        store.register_version(candidate_id, candidate_path, f"attempt {attempt}")
        (out_dir / f"fit_knobs_attempt_{attempt}.json").write_text(
            json.dumps(knobs, indent=2), encoding="utf-8"
        )
        store.set_active_view(candidate_id, fit_facts)
        store.record_state("candidate_rendered", f"attempt {attempt}: fit renders={fit_renders}")

        # --- validated ------------------------------------------------------------
        report = validate_candidate(
            v1_pdf,
            first_pdf,
            second_pdf,
            store.base_html,
            candidate_html,
            store.nodes,
            store.target_design,
            fit_facts,
        )
        last_report = report
        (out_dir / f"validation_attempt_{attempt}.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )
        store.record_state("validated", f"attempt {attempt}: {'PASS' if report.passed else 'FAIL'}")

        if report.passed:
            _run_visual_reviewer(store, specialists, target_pdf, first_pdf, out_dir, attempt)
            decision = run_checkpoint(
                "Checkpoint 3 (state=validated). Deterministic validation passed on "
                f"the candidate render; your evidence tools now measure the "
                f"candidate version ({candidate_id}): inspect_layout_node and "
                "compare_target_and_render report the candidate's own facts. "
                "Review the evidence and decide: accept the candidate version, "
                "reject it, or needs_human_review.",
                "final",
                {"accept", "reject", "needs_human_review"},
            )
            if decision.action == "accept":
                store.manifest["active_layout_version_id"] = candidate_id
                store.record_state("accepted", f"active={candidate_id}")
                terminal = "accepted"
            elif decision.action == "reject":
                store.set_active_view("layout_v1", store.base_facts)
                store.record_state("rejected", decision.note or "orchestrator rejected")
                terminal = "rejected"
            else:
                store.record_state("needs_human_review", decision.note)
                terminal = "needs_human_review"
            _final_artifacts(out_dir, target_pdf, first_pdf)
            break

        # validation failed: bounded retry with failure feedback in the prompt
        failed_checks = [c.name for c in report.checks if not c.passed]
        store.diagnosis = store.diagnosis.model_copy(
            update={"claim": f"{store.diagnosis.claim} [attempt {attempt} validation failed: {failed_checks}]"}
        )
    else:
        # attempts exhausted without a break: failed validation on every attempt
        if last_report is not None and not last_report.passed:
            store.record_state("rejected", "validation failed on all attempts; layout_v1 stays active")
            terminal = "rejected"
    _write_reports(store, out_dir, terminal, base_run_dir, report=last_report)
    return out_dir, terminal


def _validate_patch_policy(store: EvidenceStore, edit: SetHeadingRuleEdit) -> str | None:
    """Schema/policy validation of the typed edit (proposal §2 boundary)."""
    if edit.type != "SetHeadingRule":
        return f"unsupported edit type {edit.type!r}"
    if edit.base_layout_version_id != store.manifest["active_layout_version_id"]:
        return (
            f"stale base version {edit.base_layout_version_id!r}; active is "
            f"{store.manifest['active_layout_version_id']!r}"
        )
    if edit.scope not in ("node", "template_role"):
        return f"unsupported scope {edit.scope!r}"
    if edit.scope == "node" and store.node_by_id(edit.target_node_id) is None:
        return f"unknown node {edit.target_node_id!r}"
    if edit.changes.placement != "below":
        return f"unsupported placement {edit.changes.placement!r} in D0"
    return None


def _run_visual_reviewer(
    store: EvidenceStore,
    specialists: Any,
    target_pdf: Path,
    first_pdf: Path,
    out_dir: Path,
    attempt: int,
) -> None:
    """Independent reviewer on the side-by-side image; deterministic verifier
    labels each finding verified / falsified / not_measurable (proposal §13).
    The reviewer never gates acceptance — deterministic validation does."""
    from pydantic_ai import BinaryContent

    target_pages = _render_pages(target_pdf, out_dir, f"review_target_{attempt}")
    cand_pages = _render_pages(first_pdf, out_dir, f"review_candidate_{attempt}")
    combined = out_dir / f"review_side_by_side_{attempt}.png"
    if not target_pages or not cand_pages:
        findings = ReviewFindings(overall_note="page rendering unavailable; reviewer skipped")
        (out_dir / f"review_attempt_{attempt}.json").write_text(
            findings.model_dump_json(indent=2), encoding="utf-8"
        )
        return
    _side_by_side(target_pages[0], cand_pages[0], combined)

    try:
        result = specialists.reviewer.run_sync(
            [
                "Compare target (left) and generated (right). List localized "
                "presentation findings, each with node_id, problem, severity, "
                "confidence. The known defect class under repair is: " + DEFECT_CLASS,
                BinaryContent(data=combined.read_bytes(), media_type="image/png"),
            ]
        )
        findings = result.output
    except Exception as error:  # reviewer is advisory; its failure must not crash the run
        findings = ReviewFindings(overall_note=f"reviewer unavailable: {error}")

    for finding in findings.findings:
        node_id = str(finding.get("node_id", ""))
        problem = str(finding.get("problem", "")).lower()
        fact = next((f for f in store.active_facts if f.node_id == node_id), None)
        if fact and "rule" in problem and "placement" in problem:
            finding["verdict"] = "falsified" if fact.placement == "below" else "verified"
        else:
            finding["verdict"] = "not_measurable"
    (out_dir / f"review_attempt_{attempt}.json").write_text(
        findings.model_dump_json(indent=2), encoding="utf-8"
    )


def _final_artifacts(out_dir: Path, target_pdf: Path, first_pdf: Path) -> None:
    _render_pages(first_pdf, out_dir, "generated")
    target_pages = _render_pages(target_pdf, out_dir, "target")
    _side_by_side(target_pages[0], out_dir / "generated_page_1.png", out_dir / "side_by_side.png")


def _write_reports(
    store: EvidenceStore,
    out_dir: Path,
    terminal: str,
    base_run_dir: Path,
    report: ValidationReport | None,
) -> None:
    store.manifest["terminal_state"] = terminal
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    checks = ""
    if report is not None:
        checks = "\n".join(f"- {'PASS' if c.passed else 'FAIL'} `{c.name}`" for c in report.checks)
    (out_dir / "REPORT.md").write_text(
        f"""# Pipeline D0 run — {out_dir.name}

- Terminal state: **{terminal}**
- Active layout version: `{store.manifest['active_layout_version_id']}`
- Base run (committed C1 matrix evidence): `{base_run_dir.name}`
- Attempts: {store.manifest['attempts']}

## State history

{"\n".join(f"- `{s['state']}` {s['note']}" for s in store.manifest['state_history'])}

## Deterministic validation

{checks or "- (no candidate reached validation)"}

Owner review: inspect `target.pdf` (in the base run), `generated_page_1.png`,
`side_by_side.png`, `evidence_pack.json`, `diagnosis.json`,
`patch_proposal_attempt_*.json`, `validation_attempt_*.json`.
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline D0 bounded repair experiment")
    parser.add_argument("--base-run", type=Path, default=DEFAULT_BASE_RUN)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--live", action="store_true", help="use live LLM agents (DeepSeek-compatible)")
    args = parser.parse_args()
    started = time.monotonic()
    run_dir, terminal = run_d0(args.base_run, args.out, live=args.live)
    print(f"D0 run {run_dir.name}: terminal state {terminal} in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
