"""Shared pipeline runtime implementation."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_MODEL_REQUESTS = 5  # work order: ≤5 total live model requests per run

MAX_TOOL_CALLS = 24  # global tool-call budget for the whole run


ROOT = Path(__file__).resolve().parents[2]


class BudgetExhausted(RuntimeError):
    pass

class CheckpointBudgetExceeded(BudgetExhausted):
    pass

@dataclass
class RunBudget:
    """One global budget for the whole run: main agent, specialists, retries."""

    max_model_requests: int = MAX_MODEL_REQUESTS
    max_tool_calls: int = MAX_TOOL_CALLS
    max_request_bytes: int | None = None
    parent: RunBudget | None = field(default=None, repr=False)
    model_requests: int = 0
    tool_calls: int = 0
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
        if self.parent is not None:
            self.parent.spend_model(agent, requests=requests, usage=usage, mode=mode)
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
        if self.parent is not None:
            self.parent.spend_tool(tool)
        self.tool_calls += 1
        self.calls_by_tool[tool] += 1


    def to_json(self) -> dict[str, Any]:
        return {
            "max_model_requests": self.max_model_requests,
            "max_tool_calls": self.max_tool_calls,
            "max_request_bytes": self.max_request_bytes,
            "model_request_count": self.model_requests,
            "tool_call_count": self.tool_calls,
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


def _record_usage(
    budget: RunBudget, trace: RunTrace, agent: str, phase: str, result: Any
) -> None:
    usage = result.usage
    if callable(usage):  # pydantic-ai version differences: property vs method
        usage = usage()
    # HTTP hooks reserve requests, including failures and SDK retries.
    budget.spend_model(agent, requests=0, usage=usage, mode="live")
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

def _budgeted_http_client(budget: RunBudget, trace: RunTrace, agent: str) -> Any:
    """Reserve each HTTP attempt before I/O; never persist payload or headers."""
    import httpx

    async def reserve(request: httpx.Request) -> None:
        if budget.max_request_bytes is not None:
            body = json.loads(request.content)
            images = sum(
                part.get("type") == "image_url"
                for message in body.get("messages", [])
                for part in (message.get("content") or [])
                if isinstance(part, dict)
            )
            if (
                len(request.content) > budget.max_request_bytes
                or images > 8
                or body.get("max_tokens", body.get("max_completion_tokens", 0)) not in range(1, 16385)
            ):
                raise BudgetExhausted("live request exceeds the frozen byte/image/output envelope")
        budget.spend_model(agent, mode="live")
        trace.add(agent=agent, phase="request", action="http_request_reserved",
                  output={"request_number": budget.model_requests, "bytes": len(request.content)})
        trace.save()

    return httpx.AsyncClient(event_hooks={"request": [reserve]})


def _live_model(budget: RunBudget, trace: RunTrace, agent: str) -> Any:
    """DeepSeek-compatible model via PydanticAI's OpenAI provider."""
    from dotenv import load_dotenv
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    load_dotenv(ROOT / ".env")  # local environment configuration
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
        openai_client=AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=300,
            http_client=_budgeted_http_client(budget, trace, agent),
        )
    )
    return OpenAIChatModel(model_name, provider=provider)

def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

class EvidenceStore:
    """Version identities and terminal-state history for the current run."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.manifest: dict[str, Any] = {
            "pipeline_phase": "e5",
            "versions": [],
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
