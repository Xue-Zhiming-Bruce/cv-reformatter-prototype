"""Bounded model budget, trace, and run-manifest plumbing for Pipeline."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
MAX_MODEL_REQUESTS = 5
MAX_TOOL_CALLS = 24
MAX_RAW_EVIDENCE_EXPANSIONS_PER_ATTEMPT = 1


class BudgetExhausted(RuntimeError):
    pass


class CheckpointBudgetExceeded(BudgetExhausted):
    pass


@dataclass
class RunBudget:
    max_model_requests: int = MAX_MODEL_REQUESTS
    max_tool_calls: int = MAX_TOOL_CALLS
    model_requests: int = 0
    tool_calls: int = 0
    raw_evidence_expansion_count: int = 0
    repair_attempt_count: int = 0
    calls_by_agent: dict[str, int] = field(default_factory=Counter)
    calls_by_tool: dict[str, int] = field(default_factory=Counter)
    calls_by_mode: dict[str, int] = field(
        default_factory=lambda: Counter({"live": 0, "scripted": 0})
    )
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
        if self.tool_calls + 1 > self.max_tool_calls:
            raise BudgetExhausted(
                f"global tool budget exhausted: {self.tool_calls}+1 would exceed {self.max_tool_calls}"
            )
        self.tool_calls += 1
        self.calls_by_tool[tool] += 1

    def spend_raw_evidence_expansion(self) -> None:
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
    if callable(usage):
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
    from dotenv import load_dotenv
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing live configuration: DEEPSEEK_API_KEY or OPENAI_API_KEY")
    provider = OpenAIProvider(
        openai_client=AsyncOpenAI(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
            timeout=300,
        )
    )
    return OpenAIChatModel(
        os.environ.get("PIPELINE_MODEL") or "deepseek-flash",
        provider=provider,
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RunStore:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.manifest: dict[str, Any] = {
            "versions": [],
            "active_layout_version_id": "layout_v1",
            "pending_candidate_id": None,
            "state_history": [],
        }

    def register_version(self, version_id: str, path: Path, note: str) -> None:
        self.manifest["versions"].append(
            {"id": version_id, "path": path.name, "sha256": _sha256_file(path), "note": note}
        )

    def record_state(self, state: str, note: str = "") -> None:
        self.manifest["state_history"].append(
            {"state": state, "ts": datetime.now(UTC).isoformat(timespec="seconds"), "note": note}
        )
