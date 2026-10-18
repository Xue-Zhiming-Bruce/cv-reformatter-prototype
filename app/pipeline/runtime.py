"""Shared pipeline runtime implementation."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from lxml import html as lxml_html
from pydantic import BaseModel, Field

MAX_MODEL_REQUESTS = 5  # work order: ≤5 total live model requests per run

MAX_TOOL_CALLS = 24  # global tool-call budget for the whole run

MAX_RAW_EVIDENCE_EXPANSIONS_PER_ATTEMPT = 1

ROOT = Path(__file__).resolve().parents[2]

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

class DefectDiagnosis(BaseModel):
    defect_class: Literal[
        "section_heading_rule_placement_mismatch",
        "unclassified",
    ]
    node_ids: list[str]
    claim: str
    measurable: bool
    evidence_ids: list[str] = Field(default_factory=list)

class BudgetExhausted(RuntimeError):
    pass

class CheckpointBudgetExceeded(BudgetExhausted):
    pass

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

def _facts_brief(facts: list[HeadingRuleFact]) -> str:
    return json.dumps([f.model_dump(mode="json") for f in facts], ensure_ascii=False, indent=1)

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
