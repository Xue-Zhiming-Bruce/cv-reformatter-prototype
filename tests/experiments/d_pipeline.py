"""Pipeline D0/D1-0 — bounded agent-directed layout repair experiment.

D1-0 (observation-first review contract, proposal §6.4/§12): the visual
reviewer returns observation-first records — a literal `observation`, a typed
`location`, a `target`-vs-`generated` comparison, and a separate `hypothesis`.
The deterministic shell assigns stable run-scoped finding ids, resolves each
hypothesis in a separate resolution record (`observation_status` stays
`recorded`; `hypothesis_status` is confirmed/rejected/unresolved/not_tested),
and writes an owner-readable review table (`review_owner_attempt_*.md`). A
rejected hypothesis never erases, suppresses, or marks the observation itself
as false.

Owner-authorized experiment under `tests/experiments/` (see
D_PIPELINE_PROPOSAL.md §11 "Minimal Experimental Slice" and §12 "D0: prove
agent-directed routing"). Not part of the active product architecture.

What D0 demonstrates (evidence-supported wording, see
D0_EXPERIMENT_REPORT.md §7): a model can participate in a bounded
typed-repair workflow — inspect versioned evidence, delegate a narrow
diagnosis or repair task, and select a typed edit that fixes one known
layout defect without changing candidate content or regressing unaffected
nodes. It does not establish that model-directed routing adds value over a
deterministic rule table (the shell pre-verifies the defect and the repair
prompt carries the only supported action; see report §7).

Initial defect: `section_heading_rule_placement_mismatch`, reproduced from
the C1 matrix artifacts of the E→D terminal run
(`runs/c1_matrix_ED_B_20260911T044203Z`, workspace-local and ignored). A
deterministic synthetic fixture builder (`build_synthetic_fixture`)
reproduces the same measurable defect from committed source only, so a
clean checkout can run the experiment without any ignored artifact.

Runtime shape (deterministic outer state machine, agent only at explicit
checkpoints):

    initialized -> evidence_ready -> diagnosis_ready -> patch_proposed
    -> candidate_rendered -> validated -> awaiting_owner_review
    -> accepted | rejected        (owner decision only, via --decide)
    needs_human_review            (conflicts, budget exhaustion, escalation)

Authority boundary: agents reason and propose; deterministic tools measure,
mutate, render and validate; passing machine gates leave the candidate
INACTIVE and stop at `awaiting_owner_review`. Only an explicit deterministic
owner decision (`python -m tests.experiments.d_pipeline --decide <run_dir>
--decision accept|reject`) promotes or rejects. A model can never promote
its own repair.

Budget: one run-level budget shared by the main orchestrator and all
specialists — max 5 model requests and a bounded tool-call count for the
whole run; exhaustion produces `needs_human_review`. The 5-request envelope
fixes the default live shape: checkpoint 1 (main agent + investigator = 2
requests), checkpoint 2 (main agent + repair agent = 2), visual reviewer
(1). On gates+review pass the shell — not a model — holds the candidate for
the owner; model promotion-recommendation rounds do not fit the envelope
and are structurally unnecessary because the shell cannot promote anyway.

Dependencies: PydanticAI is an experiment/dev optional dependency
(`pydantic-ai-slim[openai]`, declared in pyproject.toml
`[project.optional-dependencies].experiments`; requires openai>=3.13,
which supersedes the base `openai>=2.44` pin when installed).

Usage:

    .venv/bin/python -m tests.experiments.d_pipeline \
        (--base-run DIR | --fixture-out DIR) [--live] [--out DIR]
    .venv/bin/python -m tests.experiments.d_pipeline \
        --decide RUN_DIR --decision accept|reject [--note "..."]

Without `--live` the agents run on scripted offline models (PydanticAI
FunctionModel) — a zero-API rehearsal that still performs real Chrome
renders and real measurement, and exercises the same run-level budget
counter. `--live` uses the DeepSeek-compatible endpoint configured via
DEEPSEEK_API_KEY / OPENAI_API_KEY.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from lxml import etree, html as lxml_html
from pydantic import BaseModel, Field, model_validator
from pydantic_ai import BinaryContent
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
MAX_REPAIR_ATTEMPTS = 2  # ≤2 automatic repair attempts per finding (budget-gated)
MAX_FIT_RENDERS = 4  # zero-API deterministic knob fitting budget
MAX_LINE_SHIFT_PT = 30.0  # downstream reflow must stay bounded/explainable
REFLOW_TOLERANCE_PT = 0.5
MAX_MODEL_REQUESTS = 5  # work order: ≤5 total live model requests per run
MAX_TOOL_CALLS = 24  # global tool-call budget for the whole run
MAX_RAW_EVIDENCE_EXPANSIONS_PER_ATTEMPT = 1
DEFAULT_BASE_RUN = RUNS / "c1_matrix_ED_B_20260911T044203Z"  # workspace-local, ignored
ROOT = Path(__file__).resolve().parents[2]

# DeepSeek rejects structured-output tool_choice in thinking mode; the A/B/C
# pipelines disable thinking the same way (deepseek.py extra_body).
LIVE_MODEL_SETTINGS: ModelSettings = {"extra_body": {"thinking": {"type": "disabled"}}}


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


REVIEW_CONTRACT_VERSION = "d1-review/1"  # proposal §6.4 observation-first contract


class FindingLocation(BaseModel):
    """Where the observation appears: page, optional bbox (pt), and either a
    canonical node_id from the run's node inventory or an explicit
    unresolved_region — never an invented identity (proposal §6.4)."""

    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float] | None = None
    node_id: str | None = None
    unresolved_region: str | None = None

    @model_validator(mode="after")
    def _identified(self) -> "FindingLocation":
        if self.node_id is None and self.unresolved_region is None:
            raise ValueError(
                "location needs a canonical node_id or an explicit unresolved_region"
            )
        if self.node_id is not None and self.unresolved_region is not None:
            raise ValueError("give node_id OR unresolved_region, not both")
        return self


class FindingComparison(BaseModel):
    """What the target and generated renders each show in that location."""

    target: str
    generated: str


class FindingHypothesis(BaseModel):
    """The reviewer's proposed structural interpretation — kept separate from
    the observation and never merged back into it. `kind` is a REQUIRED typed
    classification with no default, so an omitted or invalid classification
    fails schema validation instead of silently becoming not_tested."""

    kind: Literal["repaired_defect_persists", "other"]
    suspected_owner: str | None = None
    explanation: str


class ReviewerFinding(BaseModel):
    """Observation-first visual-reviewer finding (proposal §6.4, D1-0).

    `observation` is a literal visible fact without causal interpretation;
    causal claims live only in `hypothesis`. The shell — never the model —
    assigns the stable run-scoped `finding_id` on the resolution record."""

    observation: str
    location: FindingLocation
    comparison: FindingComparison
    hypothesis: FindingHypothesis
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)


class FindingResolution(BaseModel):
    """Deterministic resolution of one finding's hypothesis, persisted
    separately from the observation. `observation_status` is always
    `recorded`: rejecting a hypothesis must never delete, suppress, or mark
    the observation itself as false (proposal §6.4; D0-R evidence)."""

    finding_id: str
    observation_status: Literal["recorded"] = "recorded"
    hypothesis_status: Literal["confirmed", "rejected", "unresolved", "not_tested"]
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    follow_up: Literal["none", "inspect_region", "owner_review"] = "none"


class ResolvedFinding(BaseModel):
    """Raw reviewer finding (retained verbatim) plus its shell-assigned id,
    node resolution, and separate deterministic resolution."""

    finding_id: str
    finding: ReviewerFinding
    resolved_node_id: str | None = None
    resolution: FindingResolution


class ReviewFindings(BaseModel):
    """Visual reviewer is a defect detector, not an acceptance authority."""

    findings: list[ReviewerFinding] = Field(default_factory=list)
    overall_note: str = ""


class OrchestratorDecision(BaseModel):
    """A model can recommend, never promote: the shell has no model-promoted
    `accept` path. `request_owner_approval` is part of the declared vocabulary
    but is not reachable at any checkpoint within the 5-request budget; the
    owner gate after validated gates is deterministic (see module docstring)."""

    action: Literal[
        "delegate_diagnosis",
        "delegate_repair",
        "request_owner_approval",
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


# --- run-level budget (shared by main agent + specialists) --------------------


class BudgetExhausted(RuntimeError):
    pass


class CheckpointBudgetExceeded(BudgetExhausted):
    pass


class CheckpointHandoff(Exception):
    """Raised by a delegation tool after the specialist ran: carries the
    checkpoint decision so the main agent's run ends in a single request."""

    def __init__(self, decision: "OrchestratorDecision") -> None:
        super().__init__(decision.action)
        self.decision = decision


@dataclass
class RunBudget:
    """One global budget for the whole run: main agent, specialists, retries."""

    max_model_requests: int = MAX_MODEL_REQUESTS
    max_tool_calls: int = MAX_TOOL_CALLS
    model_requests: int = 0
    tool_calls: int = 0
    raw_evidence_expansion_count: int = 0
    repair_attempt_count: int = 0
    calls_by_agent: dict[str, int] = field(default_factory=Counter)
    calls_by_tool: dict[str, int] = field(default_factory=Counter)
    # Pipeline E4 evidence bookkeeping (Phase 0): scripted agent invocations and
    # LIVE provider model calls are counted separately. spend_model defaults to
    # "scripted" (offline rehearsals); the PydanticAI live paths report
    # mode="live" through _record_usage.
    calls_by_mode: dict[str, int] = field(default_factory=lambda: Counter({"live": 0, "scripted": 0}))
    usage: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "requests": 0,
            "tool_calls": 0,
        }
    )

    def remaining_model_requests(self) -> int:
        return self.max_model_requests - self.model_requests

    def spend_model(
        self, agent: str, requests: int = 1, usage: Any = None, mode: str = "scripted"
    ) -> None:
        """Hard pre-execution cap: reject BEFORE incrementing when the action
        would exceed the limit, so persisted executed counts never do."""
        if self.model_requests + requests > self.max_model_requests:
            raise BudgetExhausted(
                f"global model budget exhausted: {self.model_requests}+{requests} "
                f"would exceed {self.max_model_requests}"
            )
        self.model_requests += requests
        self.calls_by_agent[agent] += requests
        self.calls_by_mode[mode] = self.calls_by_mode.get(mode, 0) + requests
        self.usage["requests"] += requests
        if usage is not None:
            self.usage["input_tokens"] += int(getattr(usage, "input_tokens", 0) or 0)
            self.usage["output_tokens"] += int(getattr(usage, "output_tokens", 0) or 0)
            self.usage["tool_calls"] += int(getattr(usage, "tool_calls", 0) or 0)

    def spend_tool(self, tool: str) -> None:
        """Hard pre-execution cap, as spend_model."""
        if self.tool_calls + 1 > self.max_tool_calls:
            raise BudgetExhausted(
                f"global tool budget exhausted: {self.tool_calls}+1 would exceed {self.max_tool_calls}"
            )
        self.tool_calls += 1
        self.calls_by_tool[tool] += 1

    def spend_raw_evidence_expansion(self) -> None:
        """Hard pre-execution cap on raw-evidence expansions (≤1 per attempt)."""
        allowed = MAX_RAW_EVIDENCE_EXPANSIONS_PER_ATTEMPT * max(1, self.repair_attempt_count)
        if self.raw_evidence_expansion_count + 1 > allowed:
            raise BudgetExhausted("raw-evidence expansion budget exceeded")
        self.raw_evidence_expansion_count += 1

    def to_json(self) -> dict[str, Any]:
        return {
            "max_model_requests": self.max_model_requests,
            "max_tool_calls": self.max_tool_calls,
            "model_request_count": self.model_requests,
            "tool_call_count": self.tool_calls,
            "raw_evidence_expansion_count": self.raw_evidence_expansion_count,
            "repair_attempt_count": self.repair_attempt_count,
            "calls_by_agent": dict(self.calls_by_agent),
            "calls_by_tool": dict(self.calls_by_tool),
            "calls_by_mode": dict(self.calls_by_mode),
            "usage": dict(self.usage),
        }


class RunTrace:
    """Compact structured decision/tool trace, persisted as trace.json."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.entries: list[dict[str, Any]] = []

    def add(
        self,
        agent: str,
        phase: str,
        action: str,
        *,
        tool: str | None = None,
        input: Any = None,
        output: Any = None,
        note: str | None = None,
        persist_output: bool = False,
    ) -> str | None:
        sequence = len(self.entries) + 1
        output_artifact: str | None = None
        if persist_output and output is not None:
            output_artifact = f"trace_{sequence:04d}_{action}.json"
            self.out_dir.joinpath(output_artifact).write_text(
                json.dumps(output, ensure_ascii=False, indent=1, default=str),
                encoding="utf-8",
            )
        self.entries.append(
            {
                "sequence": sequence,
                "agent": agent,
                "phase": phase,
                "action": action,
                **({"tool": tool} if tool else {}),
                "input": input,
                **({"output_artifact": output_artifact} if output_artifact else {}),
                **({"note": note} if note else {}),
                "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        return output_artifact

    def save(self) -> None:
        self.out_dir.joinpath("trace.json").write_text(
            json.dumps(self.entries, ensure_ascii=False, indent=1), encoding="utf-8"
        )


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

    Heading verbatims come from the base run's body scaffold (Adobe-derived
    evidence for a real C1 run; the deterministic fixture builder writes the
    same shape). The local gaps come from fresh pdfplumber measurement of the
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


# --- deterministic synthetic fixture (clean-checkout reproduction) ------------

FIXTURE_HEADINGS = {"skills": "Skills", "education": "Education & Certifications"}

_FIXTURE_CSS = """
body { margin: 36pt; font-family: Helvetica, Arial, sans-serif; }
.c1-header { text-align: center; font-weight: bold; font-size: 18pt;
  border-bottom: 0.4pt solid #A16F0B; padding-bottom: 4pt; margin-bottom: 14pt; }
.header-rule { border: 0; border-top: 0.4pt solid #A16F0B; margin: 4.5pt 0 2pt 0; }
.section-heading { font-size: 12pt; font-weight: bold; margin: 0 0 12.34pt 0; }
p { font-size: 10.5pt; margin: 0; }
"""


def build_synthetic_fixture(out_dir: Path, *, render: Any = None) -> Path:
    """Deterministically build a minimal D0 base-run fixture (no corpus, no
    ignored artifacts): a target PDF with the rule-BELOW-heading design and a
    candidate `filled.html` whose section rules sit ABOVE the headings — the
    same measurable inversion as the real E→D defect.

    `render(html_text, pdf_path)` defaults to the pinned-Chrome exporter; tests
    may inject a stub. Returns the base-run directory.
    """
    out_dir.mkdir(parents=True, exist_ok=False)
    if render is None:

        def render(html_text: str, pdf_path: Path) -> Path:
            html_path = pdf_path.with_suffix(".html")
            html_path.write_text(html_text, encoding="utf-8")
            return _export_pinned_html_to_pdf(
                html_path, pdf_path, pinned_export_environment({})
            )

    sections_target: list[str] = []
    sections_render: list[str] = []
    for name, verbatim in FIXTURE_HEADINGS.items():
        sections_target.append(
            f'<div class="section" data-section="{name}">'
            f'<h2 class="section-heading" data-source-line="L0010" style="margin-bottom:0">{verbatim}</h2>'
            '<hr class="hr hr--green" style="margin:1.742pt 0 8.011pt 0">'
            f"<p>Content line one for the {verbatim.lower()} section.</p>"
            f"<p>Content line two for the {verbatim.lower()} section.</p>"
            "</div>"
        )
        sections_render.append(
            '<hr class="hr hr--green">\n'
            f'<div class="section" data-section="{name}">'
            f'<h2 class="section-heading" data-source-line="L0010">{verbatim}</h2>'
            f"<p>Content line one for the {verbatim.lower()} section.</p>"
            f"<p>Content line two for the {verbatim.lower()} section.</p>"
            "</div>"
        )

    def document(body: str) -> str:
        return (
            "<html><head><meta charset='utf-8'><style>"
            f"{_FIXTURE_CSS}</style></head><body>"
            '<header class="c1-header">J. Doe</header>'
            '<hr class="header-rule">'
            f"{body}</body></html>"
        )

    render(document("".join(sections_target)), out_dir / "target.pdf")
    (out_dir / "filled.html").write_text(document("".join(sections_render)), encoding="utf-8")
    (out_dir / "format_summary.json").write_text("{}", encoding="utf-8")
    (out_dir / "body_scaffold.json").write_text(
        json.dumps({"headings": [{"verbatim": v} for v in FIXTURE_HEADINGS.values()]}),
        encoding="utf-8",
    )
    return out_dir


# --- deterministic mutation ---------------------------------------------------


def _hr_is_section_rule(element: Any) -> bool:
    """Section-owned rules are the green hr variants; other header-region
    rules are never touched."""
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

    # 10. downstream reflow bounded and explainable (|shift| capped; moving
    # up is expected when the above-heading rule block is removed)
    base_by_key: dict[tuple[int, str], list[float]] = {}
    for line in base_lines:
        base_by_key.setdefault((line["page"], line["text"]), []).append(line["top"])
    bad_shifts = []
    for line in cand_lines:
        tops = base_by_key.get((line["page"], line["text"]))
        if not tops:
            continue
        shift = line["top"] - min(tops, key=lambda t: abs(t - line["top"]))
        if abs(shift) > MAX_LINE_SHIFT_PT:
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


# --- agent layer (PydanticAI, experiment/dev dependency) -----------------------


def _facts_brief(facts: list[HeadingRuleFact]) -> str:
    return json.dumps([f.model_dump(mode="json") for f in facts], ensure_ascii=False, indent=1)


def _spend_main_or_die(budget: RunBudget, trace: RunTrace, phase: str) -> None:
    try:
        budget.spend_model("main_orchestrator", requests=1)
    except BudgetExhausted as error:
        trace.add(agent="main_orchestrator", phase=phase, action="budget_exhausted", note=str(error))
        raise CheckpointBudgetExceeded(str(error)) from error


def _limits(budget: RunBudget) -> Any:
    from pydantic_ai.usage import UsageLimits

    return UsageLimits(
        request_limit=max(1, budget.remaining_model_requests()),
        tool_calls_limit=max(1, budget.max_tool_calls - budget.tool_calls),
    )


def _record_usage(
    budget: RunBudget, trace: RunTrace, agent: str, phase: str, result: Any
) -> None:
    usage = result.usage
    if callable(usage):  # pydantic-ai version differences: property vs method
        usage = usage()
    budget.spend_model(agent, requests=int(usage.requests or 1), usage=usage, mode="live")
    trace.add(
        agent=agent,
        phase=phase,
        action="model_usage",
        output={
            "requests": int(usage.requests or 0),
            "input_tokens": int(usage.input_tokens or 0),
            "output_tokens": int(usage.output_tokens or 0),
        },
    )


def build_orchestrator_tools(
    store: "EvidenceStore", specialists: Any, budget: RunBudget, trace: RunTrace
) -> list[Any]:
    """Read-only evidence tools + two bounded delegation tools (§5, §11).
    Every tool call is budget-counted and traced. The delegation tools run the
    specialist in the SAME model request budget and then raise
    `CheckpointHandoff` so the main agent's checkpoint costs exactly one main
    request (pydantic-ai wraps the tool exception; the shell unwraps it)."""

    def traced(phase: str, fn: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            budget.spend_tool(fn.__name__)
            result = fn(*args, **kwargs)
            small = isinstance(result, str) and len(result) < 400
            trace.add(
                agent="main_orchestrator",
                phase=phase,
                action="tool_call",
                tool=fn.__name__,
                input=kwargs or {"args": [str(a)[:120] for a in args]},
                output=result if small else None,
                note=(result[:380] if small else None),
                persist_output=not small,
            )
            return result

        wrapper.__name__ = fn.__name__  # pydantic-ai derives the tool name from it
        wrapper.__doc__ = fn.__doc__
        return wrapper

    def get_artifact_manifest() -> str:
        """Versioned artifact manifest: versions, active version, state history."""
        return json.dumps(store.manifest, ensure_ascii=False, indent=1)

    def get_target_layout() -> str:
        """Target heading-rule design: measured content-independent local gaps."""
        return store.target_design.model_dump_json(indent=1)

    def inspect_layout_node(node_id: str) -> str:
        """Measured rule facts for one section heading node in the reviewed version."""
        fact = store.facts_for(node_id)
        return fact.model_dump_json(indent=1) if fact else f"unknown node {node_id!r}"

    def compare_target_and_render(node_id: str) -> str:
        """Target design vs reviewed-version facts for one node, with delta."""
        return json.dumps(store.compare(node_id), ensure_ascii=False, indent=1)

    async def delegate_evidence_investigation(ctx: RunContext[Any], question: str) -> str:
        """Delegate a bounded read-only diagnosis to the evidence investigator."""
        budget.spend_tool("delegate_evidence_investigation")
        if budget.remaining_model_requests() < 1:
            raise BudgetExhausted("no model budget left for the evidence investigator")
        trace.add(
            agent="main_orchestrator",
            phase=store.current_phase,
            action="tool_call",
            tool="delegate_evidence_investigation",
            input={"delegated_to": "evidence_investigator"},
        )
        try:
            result = await specialists.investigator.run(
                store.diagnosis_prompt(), usage_limits=_limits(budget)
            )
        except BudgetExhausted:
            raise
        _record_usage(budget, trace, "evidence_investigator", store.current_phase, result)
        store.diagnosis = result.output
        output_artifact = trace.add(
            agent="evidence_investigator",
            phase=store.current_phase,
            action="diagnosis",
            output=result.output.model_dump(mode="json"),
            persist_output=True,
        )
        raise CheckpointHandoff(
            OrchestratorDecision(
                action="delegate_diagnosis",
                note=f"investigator diagnosis recorded ({output_artifact})",
            )
        )

    async def delegate_repair_proposal(ctx: RunContext[Any], instruction: str) -> str:
        """Delegate a bounded SetHeadingRule repair proposal to the layout repair agent."""
        budget.spend_tool("delegate_repair_proposal")
        if budget.remaining_model_requests() < 1:
            raise BudgetExhausted("no model budget left for the layout repair agent")
        trace.add(
            agent="main_orchestrator",
            phase=store.current_phase,
            action="tool_call",
            tool="delegate_repair_proposal",
            input={"delegated_to": "layout_repair"},
        )
        try:
            result = await specialists.repair.run(
                store.repair_prompt(), usage_limits=_limits(budget)
            )
        except BudgetExhausted:
            raise
        _record_usage(budget, trace, "layout_repair", store.current_phase, result)
        proposal = result.output
        if isinstance(proposal, PatchProposal) and proposal.edit is not None:
            store.proposals.append(proposal.edit)
        output_artifact = trace.add(
            agent="layout_repair",
            phase=store.current_phase,
            action="patch_proposal",
            output=proposal.model_dump(mode="json"),
            persist_output=True,
        )
        raise CheckpointHandoff(
            OrchestratorDecision(
                action="delegate_repair",
                note=f"repair proposal recorded ({output_artifact})",
            )
        )

    return [
        traced("evidence", get_artifact_manifest),
        traced("evidence", get_target_layout),
        traced("evidence", inspect_layout_node),
        traced("evidence", compare_target_and_render),
        delegate_evidence_investigation,
        delegate_repair_proposal,
    ]


ORCHESTRATOR_INSTRUCTIONS = (
    "You are the main orchestrator of a bounded document-repair state machine. "
    "You choose ONE next bounded action per checkpoint from the allowed actions "
    "stated in the prompt. You may inspect versioned evidence with your read-only "
    "tools first, but every request consumes the run's global 5-request budget. "
    "You must not modify content; repairs are typed edits applied by "
    "deterministic tools. You can never approve or promote your own repair: the "
    "owner decides promotion. Calling a delegation tool immediately commits that "
    "checkpoint's decision. Allowed actions: delegate_diagnosis, delegate_repair, "
    "request_owner_approval, reject, needs_human_review."
)


class SpecialistAgents:
    """The three fixed specialist roles (proposal §11 item 3)."""

    def __init__(self, model: Any, model_settings: ModelSettings | None = None) -> None:
        from pydantic_ai import Agent

        self.investigator = Agent(
            model,
            output_type=DefectDiagnosis,
            name="evidence_investigator",
            model_settings=model_settings,
            instructions=(
                "You are a read-only evidence investigator for a resume layout "
                "experiment. You receive measured facts for the target design and "
                "the current render. Classify the defect. Only one defect "
                f"vocabulary entry exists: '{DEFECT_CLASS}' (the horizontal rule "
                "placement relative to the section heading does not match the "
                "target). Set measurable=true only when numeric deltas are "
                "provided. Cite node ids in node_ids. If the evidence does not "
                "match that vocabulary, return defect_class='unclassified'."
            ),
        )
        self.repair = Agent(
            model,
            output_type=PatchProposal,
            name="layout_repair",
            model_settings=model_settings,
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
            model_settings=model_settings,
            instructions=(
                "You are an independent read-only visual reviewer (defect "
                "detector, not an acceptance authority and not a geometry "
                "oracle). Inspect the side-by-side image (left=target, "
                "right=generated). Report one record per localized finding, "
                "separating what you see from what you think caused it: "
                "'observation' = literal visible fact with NO causal "
                "interpretation; 'location' = page, optional bbox in pt, and "
                "EITHER a canonical node_id from the provided node inventory "
                "(format 'section.<name>.heading') OR an explicit "
                "unresolved_region string when you cannot map the region — "
                "never invent an id; 'comparison' = what target and generated "
                "each show there; 'hypothesis' = your typed structural "
                "interpretation, with kind 'repaired_defect_persists' ONLY "
                "when you assert the repaired rule-placement defect is still "
                "present, otherwise 'other', plus suspected_owner and "
                "explanation. Every finding also carries severity and "
                "confidence. Do not demand target-sample facts as "
                "corrections; candidate wording is verbatim source content."
            ),
        )


def _live_model() -> Any:
    """DeepSeek-compatible model via PydanticAI's OpenAI provider."""
    from dotenv import load_dotenv
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    load_dotenv(ROOT / ".env")  # experiment-only credential loading
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing live configuration: DEEPSEEK_API_KEY or OPENAI_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    model_name = (
        os.environ.get("D_PIPELINE_MODEL")
        or os.environ.get("C_PIPELINE_MODEL")
        or os.environ.get("B_PIPELINE_MODEL")
        or os.environ.get("A_PIPELINE_MODEL")
        or "deepseek-flash"
    )
    provider = OpenAIProvider(
        openai_client=AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=300)
    )
    return OpenAIChatModel(model_name, provider=provider)


class ScriptedAgents:
    """Offline FunctionModel drivers — deterministic, zero-API rehearsal that
    exercises the same run-level budget counter as live runs."""

    def __init__(
        self,
        diagnosis: DefectDiagnosis | None = None,
        proposal: SetHeadingRuleEdit | None = None,
        proposal_2: SetHeadingRuleEdit | None = None,
        repair_action: str = "delegate_repair",
        review_findings: list[ReviewerFinding] | None = None,
        review_raises: bool = False,
    ) -> None:
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel

        self.diagnosis = diagnosis
        self.proposals = [p for p in (proposal, proposal_2) if p is not None]
        self.repair_action = repair_action
        self.review_findings = review_findings
        self.review_raises = review_raises

        def single_shot(payload: dict[str, Any]) -> FunctionModel:
            def driver(messages: list[Any], info: Any) -> Any:
                from pydantic_ai.messages import ModelResponse, ToolCallPart

                return ModelResponse(parts=[ToolCallPart("final_result", payload)])

            return FunctionModel(driver)

        self.investigator = Agent(
            single_shot(self._diagnosis_payload()),
            output_type=DefectDiagnosis,
            name="scripted_investigator",
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
        if self.review_raises:

            def broken_driver(messages: list[Any], info: Any) -> Any:
                raise RuntimeError("scripted reviewer transport failure")

            self.reviewer = Agent(
                FunctionModel(broken_driver),
                output_type=ReviewFindings,
                name="scripted_reviewer",
            )
        else:
            self.reviewer = Agent(
                single_shot(
                    ReviewFindings(findings=self.review_findings or []).model_dump(mode="json")
                ),
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
                "changes": {
                    "placement": "below",
                    "gap_heading_pt": 1.742,
                    "gap_content_pt": 10.463,
                },
            }
        }

    def orchestrator_model_for(self, phase: str) -> Any:
        """One request per checkpoint: either the delegation tool call (the
        specialist raises the handoff) or a direct final decision."""
        from pydantic_ai.messages import ModelResponse, ToolCallPart
        from pydantic_ai.models.function import FunctionModel

        def driver(messages: list[Any], info: Any) -> Any:
            from pydantic_ai.messages import ModelResponse, ToolCallPart

            if phase == "repair" and self.repair_action != "delegate_repair":
                payload = {"action": self.repair_action, "note": "scripted"}
                return ModelResponse(parts=[ToolCallPart("final_result", payload)])
            if phase == "diagnosis":
                tool, args = "delegate_evidence_investigation", {"question": "classify the defect"}
            else:
                tool, args = "delegate_repair_proposal", {"instruction": "propose the typed repair"}
            return ModelResponse(parts=[ToolCallPart(tool, args)])

        return FunctionModel(driver)


# --- versioned artifact store --------------------------------------------------


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EvidenceStore:
    """References and authority for the run — never uncontrolled file access.

    `base_html` may be injected for runs that have no C1 base render (Pipeline E1
    target understanding has no `filled.html`); the default path keeps reading
    the D0/D1 base run unchanged.
    """

    def __init__(self, base_run_dir: Path, out_dir: Path, *, base_html: str | None = None) -> None:
        self.base_run_dir = base_run_dir
        self.out_dir = out_dir
        if base_html is None:
            base_html = (base_run_dir / "filled.html").read_text(encoding="utf-8")
            base_html_sha256 = _sha256_file(base_run_dir / "filled.html")
        else:
            base_html_sha256 = hashlib.sha256(base_html.encode("utf-8")).hexdigest()
        self.base_html = base_html
        self.nodes = discover_section_nodes(self.base_html)
        self.target_design: TargetRuleDesign | None = None
        self.base_facts: list[HeadingRuleFact] = []
        self.active_facts: list[HeadingRuleFact] = []
        self.reviewing_version_id = "layout_v1"
        self.current_phase = "evidence"
        self.diagnosis: DefectDiagnosis | None = None
        self.proposals: list[SetHeadingRuleEdit] = []
        self.next_finding_seq = 1  # shell-assigned run-scoped finding ids
        self.manifest: dict[str, Any] = {
            "experiment": "d_pipeline_d0",
            "pipeline_phase": "d1_0",
            "base_run_dir": str(base_run_dir),
            "base_html_sha256": base_html_sha256,
            "versions": [],
            "active_layout_version_id": "layout_v1",
            "pending_candidate_id": None,
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

    def review_prompt_nodes(self) -> str:
        return json.dumps(self.nodes, ensure_ascii=False)


# --- deterministic outer state machine -----------------------------------------


def run_d0(
    base_run_dir: Path,
    out_dir: Path | None = None,
    *,
    live: bool = False,
    max_attempts: int = MAX_REPAIR_ATTEMPTS,
    offline_agents: "ScriptedAgents | None" = None,
    budget: RunBudget | None = None,
) -> tuple[Path, str]:
    """Run the bounded D0 experiment. Returns (run_dir, terminal_state).

    The terminal state after passing machine gates + review is
    `awaiting_owner_review` with the candidate INACTIVE; only `owner_decide`
    promotes or rejects. Budget exhaustion produces `needs_human_review`.
    """
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded

    base_run_dir = base_run_dir.resolve()
    out_dir = out_dir or RUNS / datetime.now(UTC).strftime("d_pipeline_d0_%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=False)

    for name in ("filled.html", "format_summary.json", "body_scaffold.json", "target.pdf"):
        if not (base_run_dir / name).exists():
            raise RuntimeError(f"base run dir missing required artifact: {name}")

    budget = budget or RunBudget()
    trace = RunTrace(out_dir)
    store = EvidenceStore(base_run_dir, out_dir)
    shutil.copy2(base_run_dir / "filled.html", out_dir / "layout_v1.html")
    store.register_version("layout_v1", out_dir / "layout_v1.html", "committed accepted render")
    store.record_state("initialized", f"base={base_run_dir.name}")
    trace.add(agent="shell", phase="initialized", action="state", note=f"base={base_run_dir.name}")

    summary = json.loads((base_run_dir / "format_summary.json").read_text(encoding="utf-8"))
    environment = pinned_export_environment(summary)
    target_pdf = base_run_dir / "target.pdf"
    shutil.copy2(target_pdf, out_dir / "target.pdf")  # self-contained owner review

    def render(html: str, name: str) -> Path:
        html_path = out_dir / name
        html_path.write_text(html, encoding="utf-8")
        return _export_pinned_html_to_pdf(html_path, html_path.with_suffix(".pdf"), environment)

    if live:
        model = _live_model()
        specialists: Any = SpecialistAgents(model, model_settings=LIVE_MODEL_SETTINGS)
        orchestrator_tools = build_orchestrator_tools(store, specialists, budget, trace)
        live_orchestrator: Agent | None = Agent(
            model,
            output_type=OrchestratorDecision,
            name="d0_orchestrator",
            instructions=ORCHESTRATOR_INSTRUCTIONS,
            model_settings=LIVE_MODEL_SETTINGS,
            tools=orchestrator_tools,
        )
    else:
        specialists = offline_agents or ScriptedAgents()
        orchestrator_tools = build_orchestrator_tools(store, specialists, budget, trace)
        live_orchestrator = None

    def run_checkpoint(prompt: str, phase: str, allowed: set[str]) -> OrchestratorDecision:
        """Main agent at one explicit checkpoint — exactly ONE main-model
        request. A delegation tool call hands the decision to the shell via
        CheckpointHandoff; pydantic-ai wraps the tool exception, so unwrap it.
        The shell enforces the vocabulary; a model can never promote."""
        store.current_phase = phase
        prompt = (
            f"{prompt}\nAt this checkpoint the ONLY allowed final actions are: "
            f"{sorted(allowed)}. A delegation tool call commits the delegation."
        )
        agent = live_orchestrator or Agent(
            specialists.orchestrator_model_for(phase),
            output_type=OrchestratorDecision,
            name=f"scripted_orchestrator_{phase}",
            tools=orchestrator_tools,
        )
        if budget.remaining_model_requests() < 1:
            trace.add(
                agent="shell", phase=phase, action="budget_exhausted", note="before checkpoint request"
            )
            raise CheckpointBudgetExceeded("global model budget exhausted before checkpoint")
        try:
            result = agent.run_sync(prompt, usage_limits=_limits(budget))
        except CheckpointHandoff as handoff:
            # delegation happened inside the tool; the aborted run consumed one
            # main-model request (provider usage is not recoverable from the
            # raised exception, so count it conservatively as one request)
            _spend_main_or_die(budget, trace, phase)
            trace.add(
                agent="main_orchestrator",
                phase=phase,
                action="decision",
                output=handoff.decision.model_dump(mode="json"),
                note="via delegation handoff",
            )
            return handoff.decision
        except UnexpectedModelBehavior as error:
            cause = error.__cause__
            if isinstance(cause, CheckpointHandoff):
                _spend_main_or_die(budget, trace, phase)
                trace.add(
                    agent="main_orchestrator",
                    phase=phase,
                    action="decision",
                    output=cause.decision.model_dump(mode="json"),
                    note="via delegation handoff",
                )
                return cause.decision
            if isinstance(cause, BudgetExhausted):
                trace.add(agent="main_orchestrator", phase=phase, action="budget_exhausted", note=str(cause))
                raise CheckpointBudgetExceeded(str(cause)) from cause
            raise
        except (UsageLimitExceeded, BudgetExhausted) as error:
            trace.add(agent="main_orchestrator", phase=phase, action="budget_exhausted", note=str(error))
            raise CheckpointBudgetExceeded(str(error)) from error
        _record_usage(budget, trace, "main_orchestrator", phase, result)
        decision = result.output
        trace.add(agent="main_orchestrator", phase=phase, action="decision", output=decision.model_dump(mode="json"))
        return decision

    def checkpoint_decision(
        prompt: str, phase: str, allowed: set[str], max_tries: int = 2
    ) -> OrchestratorDecision:
        last_error = ""
        for _ in range(max_tries):
            text = prompt if not last_error else f"{prompt}\nYour previous decision {last_error!r} was not allowed here."
            decision = run_checkpoint(text, phase, allowed)
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
        raise RuntimeError(f"base render is not reproducibly stable: {stability_details}")
    store.active_facts = [
        f
        for node in store.nodes
        if (f := measure_heading_rule_fact(v1_pdf, node["node_id"], node["heading_verbatim"]))
    ]
    store.base_facts = list(store.active_facts)
    store.set_active_view("layout_v1", store.active_facts)
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
    trace.add(agent="shell", phase="evidence", action="state", note="evidence_ready")

    defect_reproduced = store.target_design.placement == "below" and any(
        f.placement == "above" for f in store.active_facts
    )
    if not defect_reproduced:
        store.record_state("needs_human_review", "defect not reproduced from evidence")
        _finish(store, out_dir, trace, budget, "needs_human_review", base_run_dir, report=None)
        return out_dir, "needs_human_review"

    # --- diagnosis_ready --------------------------------------------------------
    try:
        decision = checkpoint_decision(
            "Checkpoint 1 (state=evidence_ready). A measurable rule-placement defect is "
            "confirmed in the evidence pack. Choose the next action.",
            "diagnosis",
            {"delegate_diagnosis", "needs_human_review"},
        )
    except CheckpointBudgetExceeded as error:
        store.record_state("needs_human_review", f"budget: {error}")
        _finish(store, out_dir, trace, budget, "needs_human_review", base_run_dir, report=None)
        return out_dir, "needs_human_review"
    if decision.action != "delegate_diagnosis" or store.diagnosis is None:
        store.record_state("needs_human_review", f"orchestrator: {decision.note}")
        _finish(store, out_dir, trace, budget, "needs_human_review", base_run_dir, report=None)
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
        store.record_state(
            "needs_human_review",
            "diagnosis not verifiable against measured facts (capability gap or unsupported claim)",
        )
        _finish(store, out_dir, trace, budget, "needs_human_review", base_run_dir, report=None)
        return out_dir, "needs_human_review"
    (out_dir / "diagnosis.json").write_text(store.diagnosis.model_dump_json(indent=2), encoding="utf-8")
    store.record_state("diagnosis_ready", f"verified nodes: {','.join(verified_nodes)}")
    trace.add(agent="shell", phase="diagnosis", action="state", note=f"verified={verified_nodes}")

    # --- bounded repair attempts -------------------------------------------------
    previous_fingerprint: str | None = None
    last_report: ValidationReport | None = None
    last_first_pdf: Path | None = None
    terminal = "needs_human_review"
    stop_note = ""

    for attempt in range(1, max_attempts + 1):
        budget.repair_attempt_count = attempt
        store.manifest["attempts"] = attempt
        try:
            decision = checkpoint_decision(
                "Checkpoint 2 (state=diagnosis_ready). Delegate the bounded repair "
                f"proposal, or refuse. Attempt {attempt} of {max_attempts}.",
                "repair",
                {"delegate_repair", "reject", "needs_human_review"},
            )
        except CheckpointBudgetExceeded as error:
            stop_note = f"budget: {error}"
            terminal = "needs_human_review"
            break
        if decision.action == "reject":
            stop_note = f"orchestrator recommended rejection: {decision.note}"
            terminal = "rejected"
            break
        if decision.action != "delegate_repair" or not store.proposals:
            stop_note = f"orchestrator: {decision.note}"
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
            stop_note = f"patch policy: {policy_failure}"
            terminal = "needs_human_review"
            break
        if fingerprint == previous_fingerprint:
            stop_note = "repeated repair fingerprint; stopping per proposal §10"
            terminal = "needs_human_review"
            break
        previous_fingerprint = fingerprint
        store.record_state("patch_proposed", f"attempt {attempt}: {edit.target_node_id} scope={edit.scope}")
        trace.add(agent="shell", phase="repair", action="state", note=f"patch_proposed attempt {attempt}")

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
        last_first_pdf = first_pdf
        store.register_version(candidate_id, candidate_path, f"attempt {attempt}")
        (out_dir / f"fit_knobs_attempt_{attempt}.json").write_text(
            json.dumps(knobs, indent=2), encoding="utf-8"
        )
        store.set_active_view(candidate_id, fit_facts)
        store.record_state("candidate_rendered", f"attempt {attempt}: fit renders={fit_renders}")
        trace.add(agent="shell", phase="repair", action="state", note=f"candidate_rendered {candidate_id}")

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
        trace.add(
            agent="shell",
            phase="repair",
            action="validation",
            output={c.name: c.passed for c in report.checks},
        )

        if not report.passed:
            # validation failed: bounded retry with failure feedback in the prompt
            failed_checks = [c.name for c in report.checks if not c.passed]
            store.diagnosis = DefectDiagnosis(
                defect_class=store.diagnosis.defect_class,
                node_ids=store.diagnosis.node_ids,
                claim=f"{store.diagnosis.claim} [attempt {attempt} validation failed: {failed_checks}]",
                measurable=store.diagnosis.measurable,
                evidence_ids=store.diagnosis.evidence_ids,
            )
            continue

        # Candidate passed machine gates. Independent visual review next;
        # reviewer failure or an unfalsifiable claim that the defect persists
        # must NOT produce an automatically promotable candidate.
        review_status, review_note = _run_visual_reviewer(
            store, specialists, target_pdf, first_pdf, out_dir, attempt, budget, trace
        )
        if review_status != "ok":
            store.set_active_view("layout_v1", store.base_facts)
            stop_note = f"visual reviewer {review_status}: {review_note}"
            terminal = "needs_human_review"
            break
        # Deterministic owner gate (no model involved): the candidate stays
        # INACTIVE until the owner decides via --decide.
        store.manifest["pending_candidate_id"] = candidate_id
        store.record_state("awaiting_owner_review", f"pending={candidate_id}")
        trace.add(
            agent="shell",
            phase="final",
            action="state",
            note=f"awaiting_owner_review pending={candidate_id} (deterministic owner gate)",
        )
        terminal = "awaiting_owner_review"
        break

    if terminal == "needs_human_review" and not stop_note and last_report is not None and not last_report.passed:
        stop_note = "validation failed on all attempts; layout_v1 stays active"
        terminal = "rejected"
        store.record_state("rejected", stop_note)
    elif stop_note and store.manifest["state_history"][-1]["state"] not in (
        "needs_human_review",
        "rejected",
        "accepted",
        "awaiting_owner_review",
    ):
        store.record_state(terminal, stop_note)
    if terminal in ("rejected", "needs_human_review"):
        store.set_active_view("layout_v1", store.base_facts)
    if terminal == "awaiting_owner_review" and last_first_pdf is not None:
        _final_artifacts(out_dir, target_pdf, last_first_pdf)
    _finish(store, out_dir, trace, budget, terminal, base_run_dir, report=last_report)
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


def _resolve_location_node(store: EvidenceStore, location: FindingLocation) -> str | None:
    """Deterministic resolver: a canonical node id only. An unresolved_region
    is kept as an explicit region and never promoted to an identity."""
    if location.node_id is not None and store.node_by_id(location.node_id):
        return location.node_id
    return None


def resolve_finding(
    store: EvidenceStore, finding_id: str, finding: ReviewerFinding
) -> ResolvedFinding:
    """Resolve one finding's hypothesis deterministically (proposal §6.4).

    The observation is always retained (`observation_status='recorded'`);
    only the hypothesis is judged. Measurable `repaired_defect_persists`
    hypotheses are confirmed or rejected against the measured candidate facts
    and the target design (§10 tolerance). `other` hypotheses have no
    deterministic verifier and stay `not_tested` (advisory). A rejected
    hypothesis never erases or downgrades the observation, which may still
    identify a real defect owned by another structure (D0-R evidence)."""
    node = _resolve_location_node(store, finding.location)
    if finding.hypothesis.kind != "repaired_defect_persists":
        return ResolvedFinding(
            finding_id=finding_id,
            finding=finding,
            resolved_node_id=node,
            resolution=FindingResolution(
                finding_id=finding_id,
                hypothesis_status="not_tested",
                reason=(
                    "No deterministic verifier exists for this hypothesis; "
                    "recorded as advisory evidence for the owner."
                ),
                follow_up="owner_review",
            ),
        )
    if node is None:
        return ResolvedFinding(
            finding_id=finding_id,
            finding=finding,
            resolved_node_id=None,
            resolution=FindingResolution(
                finding_id=finding_id,
                hypothesis_status="unresolved",
                reason=(
                    "The claim cannot be checked: the location has no canonical "
                    "node. The observation is retained for later region "
                    "inspection and ownership remapping."
                ),
                follow_up="inspect_region",
            ),
        )
    fact = store.facts_for(node)
    design = store.target_design

    def _unresolved(missing: str, evidence_ids: list[str]) -> ResolvedFinding:
        return ResolvedFinding(
            finding_id=finding_id,
            finding=finding,
            resolved_node_id=node,
            resolution=FindingResolution(
                finding_id=finding_id,
                hypothesis_status="unresolved",
                reason=(
                    f"The claim cannot be checked: {missing}. The observation "
                    "is retained for later region inspection."
                ),
                evidence_ids=evidence_ids,
                follow_up="inspect_region",
            ),
        )

    # evidence provenance: cite only measurements that actually exist — no
    # fabricated measurement IDs on unresolved results.
    if fact is None:
        return _unresolved("no measured heading-rule fact exists for this node", [])
    evidence_ids = list(fact.evidence_ids)
    if design is None:
        return _unresolved("the target design measurement is unavailable", evidence_ids)
    if fact.placement != "below":
        # placement alone is a sufficient measurement that contradicts the
        # target's rule-below design; no gap is needed to decide.
        return ResolvedFinding(
            finding_id=finding_id,
            finding=finding,
            resolved_node_id=node,
            resolution=FindingResolution(
                finding_id=finding_id,
                hypothesis_status="confirmed",
                reason=(
                    f"Sufficient measurements exist and contradict the target "
                    f"for {node}: the section rule placement is "
                    f"{fact.placement!r} but the target design is "
                    f"{design.placement!r}; the repaired-defect claim stands. "
                    "The observation is retained."
                ),
                evidence_ids=evidence_ids + [f"measure.{node}.rule_placement"],
                follow_up="owner_review",
            ),
        )
    gap = fact.gap_heading_to_rule_pt
    if gap is None:
        return _unresolved("the heading-to-rule gap was not measured", evidence_ids)
    evidence_ids.append(f"measure.{node}.heading_rule_gap")
    if abs(gap - design.gap_heading_to_rule_pt) <= GAP_TOLERANCE_PT:
        return ResolvedFinding(
            finding_id=finding_id,
            finding=finding,
            resolved_node_id=node,
            resolution=FindingResolution(
                finding_id=finding_id,
                hypothesis_status="rejected",
                reason=(
                    f"Measured geometry refutes the hypothesis: the {node} section "
                    f"rule sits below the heading ({gap:.3f}pt "
                    f"vs target {design.gap_heading_to_rule_pt:.3f}pt, tolerance "
                    f"{GAP_TOLERANCE_PT}pt). The visible lines may belong to another "
                    "owner (e.g. the header); the observation itself remains recorded."
                ),
                evidence_ids=evidence_ids,
                follow_up="inspect_region",
            ),
        )
    return ResolvedFinding(
        finding_id=finding_id,
        finding=finding,
        resolved_node_id=node,
        resolution=FindingResolution(
            finding_id=finding_id,
            hypothesis_status="confirmed",
            reason=(
                f"Sufficient measurements exist and contradict the target for "
                f"{node}: the section rule gap is {gap:.3f}pt vs target "
                f"{design.gap_heading_to_rule_pt:.3f}pt (tolerance "
                f"{GAP_TOLERANCE_PT}pt); the repaired-defect claim stands. "
                "The observation is retained."
            ),
            evidence_ids=evidence_ids,
            follow_up="owner_review",
        ),
    )


def _run_visual_reviewer(
    store: EvidenceStore,
    specialists: Any,
    target_pdf: Path,
    first_pdf: Path,
    out_dir: Path,
    attempt: int,
    budget: RunBudget | None = None,
    trace: RunTrace | None = None,
) -> tuple[str, str]:
    """Independent reviewer on the side-by-side image. Returns
    (status, note) with status in {"ok", "conflict", "failed"}.

    The reviewer is advisory; it can never accept. D1-0 contract: the raw
    reviewer output is preserved (`review_raw_attempt_N.json` + trace) and
    each finding gets a shell-assigned run-scoped id plus a separate
    deterministic resolution record (`review_attempt_N.json`). A confirmed or
    unresolved repaired-defect hypothesis blocks automatic progress; a
    rejected hypothesis does not — the observation stays recorded either way."""
    try:
        target_pages = _render_pages(target_pdf, out_dir, f"review_target_{attempt}")
        cand_pages = _render_pages(first_pdf, out_dir, f"review_candidate_{attempt}")
    except Exception as error:
        return "failed", f"page rendering failed: {error}"
    if not target_pages or not cand_pages:
        return "failed", "page rendering produced no pages"
    combined = out_dir / f"review_side_by_side_{attempt}.png"
    _side_by_side(target_pages[0], cand_pages[0], combined)

    try:
        if budget is not None and budget.remaining_model_requests() < 1:
            return "failed", "no model budget left for the visual reviewer"
        result = specialists.reviewer.run_sync(
            [
                "Compare target (left) and generated (right). List localized "
                "presentation findings. Node inventory (canonical node ids): "
                + store.review_prompt_nodes()
                + ". The known defect class under repair is: " + DEFECT_CLASS,
                BinaryContent(data=combined.read_bytes(), media_type="image/png"),
            ],
            usage_limits=_limits(budget) if budget else None,
        )
        findings = result.output
        if budget is not None and trace is not None:
            _record_usage(budget, trace, "visual_reviewer", "final", result)
    except Exception as error:  # reviewer is advisory; failure must not auto-promote
        return "failed", f"reviewer unavailable: {error}"

    raw = findings.model_dump(mode="json")
    (out_dir / f"review_raw_attempt_{attempt}.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if trace is not None:
        trace.add(
            agent="visual_reviewer",
            phase="final",
            action="review",
            output=raw,
            persist_output=True,
        )
    resolved: list[ResolvedFinding] = []
    status = "ok"
    for finding in findings.findings:
        finding_id = f"review.finding.{store.next_finding_seq}"
        store.next_finding_seq += 1
        entry = resolve_finding(store, finding_id, finding)
        if entry.resolution.hypothesis_status in ("confirmed", "unresolved"):
            status = "conflict"
        resolved.append(entry)
        if trace is not None:
            trace.add(
                agent="shell",
                phase="final",
                action="review_resolution",
                output=entry.model_dump(mode="json"),
            )
    (out_dir / f"review_attempt_{attempt}.json").write_text(
        json.dumps(
            {
                "review_contract": REVIEW_CONTRACT_VERSION,
                "findings": [entry.model_dump(mode="json") for entry in resolved],
                "overall_note": findings.overall_note,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_owner_review(out_dir, attempt, resolved, findings.overall_note)
    note = "; ".join(
        f"{entry.resolved_node_id or entry.finding.location.unresolved_region}:"
        f"{entry.resolution.hypothesis_status}"
        for entry in resolved
    ) or findings.overall_note
    return status, note


def _md_cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _write_owner_review(
    out_dir: Path, attempt: int, resolved: list[ResolvedFinding], overall_note: str
) -> None:
    """Owner-readable review artifact (proposal §6.4): observation, location,
    target-vs-generated comparison, reviewer hypothesis and confidence,
    deterministic resolution, and recommended follow-up side by side — never
    reduced to a pass/fail verdict. A rejected hypothesis never marks the
    observation itself as false."""
    lines = [
        f"# Visual review — attempt {attempt} ({out_dir.name})",
        "",
        f"Review contract: `{REVIEW_CONTRACT_VERSION}`. Observations below are "
        "reviewer-reported visual observations, preserved verbatim; they are "
        "not asserted to be true. The deterministic resolution judges only the "
        "reviewer's structural hypothesis — it never deletes, suppresses, or "
        "marks the observation itself as false. The reviewer is advisory; "
        "visual acceptance stays with the owner.",
        "",
        "| Location | Observation | Target vs generated | Reviewer hypothesis (confidence) | Deterministic resolution | Recommended follow-up |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in resolved:
        finding = entry.finding
        location = f"page {finding.location.page}; "
        if entry.resolved_node_id:
            location += f"node `{entry.resolved_node_id}`"
        elif finding.location.node_id:
            location += f"claimed node `{finding.location.node_id}` (unresolved)"
        else:
            location += f"region: {finding.location.unresolved_region}"
        if finding.location.bbox is not None:
            location += "; bbox [" + ", ".join(f"{v:.1f}" for v in finding.location.bbox) + "]"
        hypothesis = (
            f"{finding.hypothesis.kind} (owner: "
            f"{finding.hypothesis.suspected_owner or 'n/a'}; confidence "
            f"{finding.confidence:.2f}; severity {finding.severity}): "
            f"{finding.hypothesis.explanation}"
        )
        resolution = (
            f"observation {entry.resolution.observation_status}; hypothesis "
            f"**{entry.resolution.hypothesis_status}** — {entry.resolution.reason}"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(location),
                    _md_cell(finding.observation),
                    _md_cell(
                        f"target: {finding.comparison.target} / "
                        f"generated: {finding.comparison.generated}"
                    ),
                    _md_cell(hypothesis),
                    _md_cell(resolution),
                    _md_cell(entry.resolution.follow_up),
                ]
            )
            + " |"
        )
    if not resolved:
        lines.append("| — | _No findings recorded for this attempt._ | — | — | — | — |")
    if overall_note:
        lines += ["", f"Reviewer overall note: {overall_note}"]
    (out_dir / f"review_owner_attempt_{attempt}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _final_artifacts(out_dir: Path, target_pdf: Path, first_pdf: Path) -> None:
    _render_pages(first_pdf, out_dir, "generated")
    target_pages = _render_pages(target_pdf, out_dir, "target")
    _side_by_side(target_pages[0], out_dir / "generated_page_1.png", out_dir / "side_by_side.png")


def _finish(
    store: EvidenceStore,
    out_dir: Path,
    trace: RunTrace,
    budget: RunBudget,
    terminal: str,
    base_run_dir: Path,
    report: ValidationReport | None,
) -> None:
    store.manifest["terminal_state"] = terminal
    store.manifest["trace"] = "trace.json"
    store.manifest["budget"] = budget.to_json()
    store.manifest["review_contract"] = REVIEW_CONTRACT_VERSION
    (out_dir / "manifest.json").write_text(
        json.dumps(store.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace.save()
    checks = ""
    if report is not None:
        checks = "\n".join(f"- {'PASS' if c.passed else 'FAIL'} `{c.name}`" for c in report.checks)
    (out_dir / "REPORT.md").write_text(
        f"""# Pipeline D1-0 run (review contract {REVIEW_CONTRACT_VERSION}) — {out_dir.name}

- Terminal state: **{terminal}**
- Active layout version: `{store.manifest['active_layout_version_id']}`
- Pending candidate (inactive): `{store.manifest['pending_candidate_id']}`
- Base run: `{base_run_dir.name}`
- Attempts: {store.manifest['attempts']}
- Model requests used: {budget.model_requests}/{budget.max_model_requests};
  tool calls: {budget.tool_calls}/{budget.max_tool_calls}
- Trace: `trace.json` (referenced by manifest.json)

## State history

{"\n".join(f"- `{s['state']}` {s['note']}" for s in store.manifest['state_history'])}

## Deterministic validation

{checks or "- (no candidate reached validation)"}

## Visual review

Observation-first review records (contract `{REVIEW_CONTRACT_VERSION}`):
resolved records in `review_attempt_*.json`, raw reviewer output in
`review_raw_attempt_*.json`, owner-readable tables in
`review_owner_attempt_*.md`. Observations are retained even when their
hypothesis is rejected; visual acceptance stays with the owner.

## Owner decision

If terminal state is `awaiting_owner_review`, promote or reject the pending
candidate explicitly (never via a model):

    .venv/bin/python -m tests.experiments.d_pipeline --decide {out_dir} --decision accept
    .venv/bin/python -m tests.experiments.d_pipeline --decide {out_dir} --decision reject
""",
        encoding="utf-8",
    )


def owner_decide(run_dir: Path, decision: Literal["accept", "reject"], note: str = "") -> str:
    """Explicit deterministic owner decision — the ONLY path that promotes.

    accept: promotes the pending candidate version; reject: leaves layout_v1
    active. Refuses to act on runs not in `awaiting_owner_review`.
    """
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("terminal_state") != "awaiting_owner_review":
        raise RuntimeError(
            f"run {run_dir.name} is not awaiting owner review "
            f"(terminal_state={manifest.get('terminal_state')!r})"
        )
    pending = manifest.get("pending_candidate_id")
    if not pending:
        raise RuntimeError("no pending candidate recorded")
    if decision == "accept":
        manifest["active_layout_version_id"] = pending
    # reject: active layout version stays layout_v1
    manifest["pending_candidate_id"] = None
    manifest["terminal_state"] = "accepted" if decision == "accept" else "rejected"
    manifest["owner_decision"] = {
        "decision": decision,
        "note": note,
        "source": "cli:deterministic_owner_decision",
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    manifest["state_history"].append(
        {
            "state": manifest["terminal_state"],
            "ts": manifest["owner_decision"]["ts"],
            "note": f"owner decision ({decision}); source=cli",
        }
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    trace_path = run_dir / "trace.json"
    if trace_path.exists():
        trace_entries = json.loads(trace_path.read_text(encoding="utf-8"))
        trace_entries.append(
            {
                "sequence": len(trace_entries) + 1,
                "agent": "owner",
                "phase": "final",
                "action": "decision",
                "input": {"decision": decision, "note": note},
                "output_artifact": "OWNER_DECISION.json",
                "timestamp": manifest["owner_decision"]["ts"],
            }
        )
        trace_path.write_text(json.dumps(trace_entries, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "OWNER_DECISION.json").write_text(
        json.dumps(manifest["owner_decision"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest["terminal_state"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline D0 bounded repair experiment")
    parser.add_argument("--base-run", type=Path, default=None,
                        help="base run dir (a C1 run dir or a build_synthetic_fixture output)")
    parser.add_argument("--fixture-out", type=Path, default=None,
                        help="build the deterministic synthetic fixture at this dir and run on it")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--live", action="store_true", help="use live LLM agents (DeepSeek-compatible)")
    parser.add_argument("--decide", type=Path, default=None,
                        help="explicit deterministic owner decision on a run awaiting review")
    parser.add_argument("--decision", choices=["accept", "reject"], default=None)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    if args.decide:
        if not args.decision:
            parser.error("--decide requires --decision accept|reject")
        terminal = owner_decide(args.decide, args.decision, args.note)
        print(f"owner decision recorded: {args.decide.name} -> {terminal}")
        return

    if args.fixture_out:
        base_run_dir = build_synthetic_fixture(args.fixture_out)
    elif args.base_run:
        base_run_dir = args.base_run
    else:
        parser.error("provide --base-run DIR or --fixture-out DIR")
    started = time.monotonic()
    run_dir, terminal = run_d0(base_run_dir, args.out, live=args.live)
    print(f"D0 run {run_dir.name}: terminal state {terminal} in {time.monotonic() - started:.1f}s")
    if terminal == "awaiting_owner_review":
        print(
            "candidate held INACTIVE for owner review:\n"
            f"  .venv/bin/python -m tests.experiments.d_pipeline --decide {run_dir} --decision accept|reject"
        )


if __name__ == "__main__":
    main()
