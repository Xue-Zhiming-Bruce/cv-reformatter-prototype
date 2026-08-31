from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tests.commercial_api import RESULT_SCHEMA, RUN_MANIFEST_SCHEMA, CORPUS_SCHEMA, SUMMARY_SCHEMA, COMPARE_SCHEMA

Status = Literal[
    "passed",
    "failed",
    "skipped",
    "not_configured",
    "unsupported",
    "provider_failed",
    "invalid_result",
    "manual_review_required",
]

ExecutionMethod = Literal["local", "external"]

# Stable, machine-readable failure codes. Exception class names are never used
# directly as result codes because they are not stable across refactors.
ErrorCode = Literal[
    "not_configured",
    "live_calls_disabled",
    "request_budget_exhausted",
    "unsupported_lane",
    "unsupported_format",
    "provider_call_failed",
    "timeout",
    "invalid_result",
    "internal_error",
    "threshold_failed",
    "evidence_missing",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class UsageRecord(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    pages: int | None = Field(default=None, ge=0)
    transactions: int | None = Field(default=None, ge=0)


class CostRecord(StrictModel):
    estimated_usd: float | None = Field(default=None, ge=0)
    currency: str = "USD"
    pricing_source: str | None = None


class ArtifactRef(StrictModel):
    kind: Literal["raw_debug", "normalized", "output", "preview", "other"] = "other"
    path: str
    sha256: str | None = None


class ProviderAttempt(StrictModel):
    attempt_number: int = Field(ge=1)
    started_at: str
    latency_seconds: float | None = Field(default=None, ge=0)
    status: Status
    error_code: str | None = None
    error_message: str | None = None
    usage: UsageRecord = Field(default_factory=UsageRecord)


class GateDecision(StrictModel):
    """A deterministic or comparative quality check on one dimension."""

    gate: str
    outcome: Literal["pass", "fail", "not_evaluated"]
    kind: Literal["deterministic", "comparative"] = "deterministic"
    threshold: str | None = None
    actual: str | None = None
    note: str | None = None


class ProviderRunResult(StrictModel):
    schema_version: Literal["commercial_api/result/1"] = RESULT_SCHEMA
    lane: str
    provider: str
    adapter_version: str
    model: str | None = None
    api_version: str | None = None
    provider_version: str | None = None
    case_id: str
    case_input_sha256: str
    status: Status
    started_at: str
    completed_at: str | None = None
    latency_seconds: float | None = Field(default=None, ge=0)
    retry_count: int = Field(default=0, ge=0)
    attempts: list[ProviderAttempt] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    gates: list[GateDecision] = Field(default_factory=list)
    usage: UsageRecord = Field(default_factory=UsageRecord)
    cost: CostRecord | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    requires_manual_review: bool = False
    non_comparable: bool = False
    prompt_version: str | None = None
    renderer: str | None = None
    font_manifest: dict[str, Any] = Field(default_factory=dict)


class CorpusRef(StrictModel):
    corpus_version: str
    schema_version: str


class RunConfiguration(StrictModel):
    lanes: list[str]
    providers: list[str]
    cases: list[str] | None = None
    live: bool
    max_cases: int | None = Field(default=None, ge=1)
    max_requests: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int = Field(default=0, ge=0)
    output_dir: str | None = None
    env_file: str | None = None
    pricing_file: str | None = None
    # Presence flags plus the exact configured Claude model ID (explicit,
    # never a moving *-latest value). Secret values are never recorded.
    config_status: dict[str, dict[str, bool | str | None]] = Field(default_factory=dict)


class RuntimeInfo(StrictModel):
    python_version: str
    platform: str
    os_name: str
    packages: dict[str, str] = Field(default_factory=dict)


class RunManifest(StrictModel):
    schema_version: Literal["commercial_api/run-manifest/1"] = RUN_MANIFEST_SCHEMA
    run_id: str
    created_at: str
    source_revision: str | None = None
    dirty_worktree: bool
    corpus: CorpusRef
    command: list[str]
    configuration: RunConfiguration
    runtime: RuntimeInfo
    results: list[ProviderRunResult]
    status_counts: dict[str, int] = Field(default_factory=dict)


class RunSummary(StrictModel):
    schema_version: Literal["commercial_api/summary/1"] = SUMMARY_SCHEMA
    run_id: str
    created_at: str
    corpus_version: str
    status_counts: dict[str, int]
    rows: list[dict[str, Any]]


class CorpusCase(StrictModel):
    case_id: str
    source_format: str
    lanes: list[str]
    input: str | None = None
    builder: str | None = None
    expected_sha256: str | None = None
    expected_profile: str | None = None
    expected_content: list[str] = Field(default_factory=list)
    expected_layout: dict[str, Any] = Field(default_factory=dict)
    negative_conditions: list[str] = Field(default_factory=list)
    privacy_exclusions: list[str] = Field(default_factory=list)
    layout_class: str | None = None
    thresholds: dict[str, Any] = Field(default_factory=dict)
    requires_manual_review: bool = False
    expected_capabilities: list[str] = Field(default_factory=list)


class CorpusManifest(StrictModel):
    schema_version: Literal["commercial_api/corpus/1"] = CORPUS_SCHEMA
    corpus_version: str
    description: str | None = None
    cases: list[CorpusCase]


class ProviderCapabilities(StrictModel):
    provider: str
    adapter_version: str
    lanes: list[str]
    formats: list[str]
    required_config: list[str]
    execution: ExecutionMethod
    retryable: bool
    pricing_key: str | None = None
    description: str | None = None


class CompareRow(StrictModel):
    lane: str
    case_id: str
    provider: str
    base_status: str | None = None
    candidate_status: str | None = None
    outcome: Literal[
        "regression",
        "improvement",
        "unchanged_pass",
        "unchanged_fail",
        "missing_in_base",
        "missing_in_candidate",
        "non_comparable",
    ]
    base_latency_seconds: float | None = None
    candidate_latency_seconds: float | None = None
    metric_deltas: dict[str, float] = Field(default_factory=dict)
    note: str | None = None


class CompareReport(StrictModel):
    schema_version: Literal["commercial_api/compare/1"] = COMPARE_SCHEMA
    base_run_id: str
    candidate_run_id: str
    base_corpus_version: str | None = None
    candidate_corpus_version: str | None = None
    comparable: bool
    rows: list[CompareRow]
    config_differences: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
