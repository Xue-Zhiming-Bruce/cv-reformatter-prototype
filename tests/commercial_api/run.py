from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.extraction.candidate_schema import CandidateProfile, DisplayRule
from app.generation.html_renderer import render_html
from app.generation.template_mapper import build_client_render_context
from tests.helpers.adobe_evidence import build_synthetic_target_analysis
from app.template_analysis.visual_comparator import compare_pdf_layouts
from app.validation.missing_fields import apply_missing_field_detection
from tests.commercial_api.corpus import (
    CORPUS_DIR,
    case_by_id,
    cases_for_lane,
    load_corpus_manifest,
    materialize_case_input,
    sha256_file,
)
from tests.commercial_api.models import (
    ArtifactRef,
    CorpusCase,
    CorpusRef,
    GateDecision,
    ProviderAttempt,
    ProviderRunResult,
    RunConfiguration,
    RunManifest,
    RuntimeInfo,
    RunSummary,
    UsageRecord,
)
from tests.commercial_api.pricing import PricingConfig
from tests.commercial_api.redaction import assert_no_secrets, redact_artifact
from tests.commercial_api import adapters as _adapters  # noqa: F401 - registers adapters
from tests.commercial_api.registry import (
    InvalidProviderResult,
    ProviderAdapter,
    ProviderCallError,
    ProviderNotConfigured,
    _explicit_model,
    configuration_status,
    resolve_adapter,
)
from tests.commercial_api.scoring import (
    evaluate_design,
    evaluate_extraction,
    evaluate_layout,
    evaluate_rendering,
    render_context_terms,
)
from tests.commercial_api.runtime import (
    DEFAULT_ENV_FILE,
    dirty_worktree as _dirty_worktree,
    git_revision as _git_revision,
    load_canonical_environment,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = ROOT / "tests" / "test_results" / "commercial_api"
DEFAULT_TIMEOUT_SECONDS = 180.0

SUPPORTED_LANES = ("extraction", "layout", "rendering", "design")
UNSUPPORTED_LANES = ("visual_diagnosis", "e2e")

_EXTERNAL_REQUEST_COUNT = 0


class Runner:
    """Executes one evaluation run and writes a versioned run manifest."""

    def __init__(
        self,
        *,
        env_file: Path | None = DEFAULT_ENV_FILE,
        output_root: Path = DEFAULT_OUTPUT_ROOT,
        live: bool = False,
        max_cases: int | None = None,
        max_requests: int | None = None,
        timeout_seconds: float | None = None,
        max_retries: int = 0,
        pricing_file: Path | None = None,
        command: list[str] | None = None,
    ):
        self.env_file = env_file
        self.output_root = output_root
        self.live = live
        self.max_cases = max_cases
        self.max_requests = max_requests
        self.timeout_seconds = timeout_seconds or DEFAULT_TIMEOUT_SECONDS
        self.max_retries = max_retries
        self.pricing = PricingConfig(pricing_file)
        self.command = command or list(sys.argv)
        self.requests_used = 0

        # Canonical environment loading: root .env first (single source of
        # truth for shared credentials/Anthropic config, matching FastAPI's
        # bare load_dotenv()); explicit env files are used verbatim; missing
        # files are not an error - providers simply report not_configured.
        load_canonical_environment(self.env_file)
        os.environ["COMMERCIAL_BAKEOFF_TIMEOUT_SECONDS"] = str(self.timeout_seconds)

    # -- public API ---------------------------------------------------------

    def run(
        self,
        *,
        lanes: set[str],
        providers: set[str] | None = None,
        cases: set[str] | None = None,
    ) -> Path:
        self._validate_selection(lanes, providers, cases)
        manifest = load_corpus_manifest()
        run_id = datetime.now(timezone.utc).strftime("commercial_api_%Y%m%dT%H%M%S%fZ")
        run_dir = self.output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)

        inputs_dir = run_dir / "inputs"
        inputs_dir.mkdir()
        prep_dir = run_dir / "prep"
        prep_dir.mkdir()

        results: list[ProviderRunResult] = []
        for lane in sorted(lanes):
            lane_cases = [c for c in cases_for_lane(manifest, lane)]
            if cases is not None:
                lane_cases = [c for c in lane_cases if c.case_id in cases]
            if self.max_cases is not None:
                lane_cases = lane_cases[: self.max_cases]
            for case in lane_cases:
                case_input = self._materialize(case, inputs_dir, prep_dir, lane)
                lane_providers = sorted(
                    {
                        adapter.capabilities().provider
                        for (registered_lane, _), adapter in _all_adapters().items()
                        if registered_lane == lane
                    },
                    key=lambda provider: _provider_sort_key(lane, provider),
                )
                if providers is not None:
                    lane_providers = [p for p in lane_providers if p in providers]
                for provider in lane_providers:
                    adapter = resolve_adapter(lane, provider)
                    adapter.set_case(case)
                    adapter.set_timeout(self.timeout_seconds)
                    result = self._run_case(lane, case, case_input, adapter, run_dir)
                    results.append(result)

        summary_counts = _status_counts(results)
        run_manifest = RunManifest(
            run_id=run_id,
            created_at=_now_iso(),
            source_revision=_git_revision(),
            dirty_worktree=_dirty_worktree(),
            corpus=CorpusRef(
                corpus_version=manifest.corpus_version,
                schema_version=manifest.schema_version,
            ),
            command=self.command,
            configuration=self._run_configuration(lanes, providers, cases),
            runtime=_runtime_info(),
            results=results,
            status_counts=summary_counts,
        )
        _write_json(run_dir / "run_manifest.json", run_manifest.model_dump(mode="json"))
        _write_json(
            run_dir / "summary.json",
            _build_summary(run_manifest).model_dump(mode="json"),
        )
        from tests.commercial_api.report import write_report

        write_report(run_manifest, run_dir)
        return run_dir

    def run_layout_inputs(
        self,
        *,
        inputs: dict[str, Path],
        providers: set[str],
        dataset_classification: str,
    ) -> Path:
        """Evaluate authorized local target PDFs through the canonical layout lane.

        Local datasets remain ignored and are not promoted into the committed
        synthetic corpus. Their stable IDs and checksums are captured in the
        ordinary commercial run manifest and per-provider results.
        """

        self._validate_selection({"layout"}, providers, None)
        if not inputs:
            raise ValueError("At least one local layout input is required.")
        for case_id, source in inputs.items():
            if not source.is_file() or source.suffix.lower() != ".pdf":
                raise ValueError(f"Local layout input must be a PDF: {case_id}={source}")

        run_id = datetime.now(timezone.utc).strftime("commercial_api_%Y%m%dT%H%M%S%fZ")
        run_dir = self.output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        inputs_dir = run_dir / "inputs"
        inputs_dir.mkdir()

        results: list[ProviderRunResult] = []
        case_ids = sorted(inputs)
        for case_id in case_ids:
            source = inputs[case_id]
            case_input = inputs_dir / f"{case_id}.pdf"
            shutil.copy2(source, case_input)
            from pypdf import PdfReader

            page_count = len(PdfReader(case_input).pages)
            case = CorpusCase(
                case_id=case_id,
                source_format="pdf",
                lanes=["layout"],
                expected_layout={"page_count": page_count},
                thresholds={
                    "required_text_recall_min": 0.0,
                    "page_count_accuracy": 1.0,
                    "column_accuracy": 0.0,
                },
                requires_manual_review=True,
            )
            for provider in sorted(providers):
                adapter = resolve_adapter("layout", provider)
                adapter.set_case(case)
                adapter.set_timeout(self.timeout_seconds)
                results.append(
                    self._run_case("layout", case, case_input, adapter, run_dir)
                )

        run_manifest = RunManifest(
            run_id=run_id,
            created_at=_now_iso(),
            source_revision=_git_revision(),
            dirty_worktree=_dirty_worktree(),
            corpus=CorpusRef(
                corpus_version=f"local/{dataset_classification}",
                schema_version="commercial_api/local-layout-inputs/1",
            ),
            command=self.command,
            configuration=self._run_configuration(
                {"layout"}, providers, set(case_ids)
            ),
            runtime=_runtime_info(),
            results=results,
            status_counts=_status_counts(results),
        )
        _write_json(run_dir / "run_manifest.json", run_manifest.model_dump(mode="json"))
        _write_json(
            run_dir / "summary.json",
            _build_summary(run_manifest).model_dump(mode="json"),
        )
        from tests.commercial_api.report import write_report

        write_report(run_manifest, run_dir)
        return run_dir

    # -- selection validation -----------------------------------------------

    def _validate_selection(
        self, lanes: set[str], providers: set[str] | None, cases: set[str] | None
    ) -> None:
        unknown_lanes = sorted(lanes - set(SUPPORTED_LANES))
        if unknown_lanes:
            raise ValueError(
                f"Unknown lanes: {', '.join(unknown_lanes)}. Supported: "
                f"{', '.join(SUPPORTED_LANES)}. Unsupported (not implemented): "
                f"{', '.join(UNSUPPORTED_LANES)}."
            )
        if providers:
            for provider in providers:
                registered_lanes = {
                    registered_lane
                    for registered_lane, provider_name in _all_adapters()
                    if provider_name == provider
                }
                if not registered_lanes:
                    raise ValueError(
                        f"Unknown provider: {provider}. Registered: "
                        f"{', '.join(sorted({a.capabilities().provider for a in _all_adapters().values()}))}."
                    )
                if not (registered_lanes & lanes):
                    raise ValueError(
                        f"Provider '{provider}' is not registered for any of the "
                        f"selected lanes ({', '.join(sorted(lanes))}). "
                        f"Registered lanes: {', '.join(sorted(registered_lanes))}."
                    )
        if cases:
            manifest = load_corpus_manifest()
            known_cases = {c.case_id for c in manifest.cases}
            unknown_cases = sorted(cases - known_cases)
            if unknown_cases:
                raise ValueError(
                    f"Unknown cases: {', '.join(unknown_cases)}. Corpus cases: "
                    f"{', '.join(sorted(known_cases))}."
                )
        # Live gating is enforced per case inside _run_case: configured external
        # providers without --live produce skipped(live_calls_disabled) results
        # and are never called. Unconfigured providers produce not_configured.

    def _run_configuration(
        self, lanes: set[str], providers: set[str] | None, cases: set[str] | None
    ) -> RunConfiguration:
        config_status: dict[str, dict[str, object]] = {}
        for provider, status in configuration_status().items():
            selected = providers is None or provider in providers
            entry: dict[str, object] = {
                "configured": bool(status["configured"]),
                "external": status["execution"] == "external",
                "selected": selected,
            }
            if provider == "claude_designer":
                # Record the exact configured model ID (explicit, never a
                # moving *-latest value) for reproducibility.
                entry["model_id"] = _explicit_model()
            config_status[provider] = entry
        return RunConfiguration(
            lanes=sorted(lanes),
            providers=sorted(providers) if providers else [],
            cases=sorted(cases) if cases else None,
            live=self.live,
            max_cases=self.max_cases,
            max_requests=self.max_requests,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            output_dir=str(self.output_root.resolve()),
            env_file=str(self.env_file.resolve()) if self.env_file else None,
            pricing_file=(
                str(self.pricing.source) if self.pricing.source else None
            ),
            config_status=config_status,
        )

    # -- case materialization -----------------------------------------------

    def _materialize(
        self, case: Any, inputs_dir: Path, prep_dir: Path, lane: str
    ) -> Path:
        if case.case_id == "controlled_docx_to_pdf":
            return self._build_controlled_docx(inputs_dir, prep_dir)
        return materialize_case_input(case, inputs_dir)

    def _build_controlled_docx(self, inputs_dir: Path, prep_dir: Path) -> Path:
        expected = apply_missing_field_detection(
            CandidateProfile.model_validate_json(
                (CORPUS_DIR / "expected_candidate_profile.json").read_text(encoding="utf-8")
            )
        )
        expected.client_display_rules = {
            "salary_expectation": DisplayRule.PENDING_CONFIRMATION,
            "interview_availability": DisplayRule.PENDING_CONFIRMATION,
        }
        context = build_client_render_context(expected)
        styled_case = case_by_id(load_corpus_manifest(), "styled_two_column")
        target_pdf = materialize_case_input(styled_case, inputs_dir)
        target_analysis = build_synthetic_target_analysis(target_pdf, prep_dir / "target_analysis")
        html_path = render_html(
            context,
            target_analysis.style_spec,
            output_path=inputs_dir / "controlled_html_to_pdf.html",
        )
        return html_path

    # -- per-case execution -------------------------------------------------

    def _run_case(
        self,
        lane: str,
        case: Any,
        case_input: Path,
        adapter: ProviderAdapter,
        run_dir: Path,
    ) -> ProviderRunResult:
        started_at = _now_iso()
        case_dir = run_dir / "cases" / lane / case.case_id / adapter.capabilities().provider
        case_dir.mkdir(parents=True)
        artifact_dir = case_dir / "artifacts"
        artifact_dir.mkdir()

        if not adapter.is_configured():
            return self._result(
                lane=lane, case=case, adapter=adapter, case_input=case_input,
                started_at=started_at, status="not_configured",
                error_code="not_configured",
                error_message="; ".join(adapter.missing_config()) or "not configured",
                case_dir=case_dir,
            )

        if (
            adapter.capabilities().execution == "external"
            and not self.live
        ):
            return self._result(
                lane=lane, case=case, adapter=adapter, case_input=case_input,
                started_at=started_at, status="skipped",
                error_code="live_calls_disabled",
                error_message=(
                    f"{adapter.capabilities().provider} is an external provider and "
                    "live calls are disabled. Re-run with --live to execute it."
                ),
                case_dir=case_dir,
            )

        if (
            adapter.capabilities().execution == "external"
            and self.max_requests is not None
            and self.requests_used >= self.max_requests
        ):
            return self._result(
                lane=lane, case=case, adapter=adapter, case_input=case_input,
                started_at=started_at, status="skipped",
                error_code="request_budget_exhausted",
                error_message="Global request budget exhausted; provider not called.",
                case_dir=case_dir,
            )

        attempts: list[ProviderAttempt] = []
        final_attempt: ProviderAttempt | None = None
        warnings: list[str] = []
        outcome = None
        for attempt_number in range(1, self.max_retries + 2):
            if (
                adapter.capabilities().execution == "external"
                and self.max_requests is not None
                and self.requests_used >= self.max_requests
            ):
                # No provider call is made. If a prior attempt failed, preserve
                # that terminal provider failure; otherwise return a structured
                # budget-exhausted skip for this case.
                if attempts:
                    final_attempt = attempts[-1]
                    break
                return self._result(
                    lane=lane,
                    case=case,
                    adapter=adapter,
                    case_input=case_input,
                    started_at=started_at,
                    status="skipped",
                    error_code="request_budget_exhausted",
                    error_message="Global request budget exhausted; provider not called.",
                    case_dir=case_dir,
                )
            attempt_started = _now_iso()
            attempt_start = time.perf_counter()
            try:
                if adapter.capabilities().execution == "external":
                    self.requests_used += 1
                outcome = adapter.run(case_input, artifact_dir)
                latency = time.perf_counter() - attempt_start
                final_attempt = ProviderAttempt(
                    attempt_number=attempt_number,
                    started_at=attempt_started,
                    latency_seconds=round(latency, 4),
                    status="passed",
                    usage=outcome.usage,
                )
                attempts.append(final_attempt)
                warnings.extend(outcome.warnings)
                break
            except ProviderNotConfigured as exc:
                latency = time.perf_counter() - attempt_start
                final_attempt = ProviderAttempt(
                    attempt_number=attempt_number,
                    started_at=attempt_started,
                    latency_seconds=round(latency, 4),
                    status="not_configured",
                    error_code="not_configured",
                    error_message=_safe_message(exc),
                )
                attempts.append(final_attempt)
                break
            except ProviderCallError as exc:
                latency = time.perf_counter() - attempt_start
                attempts.append(
                    ProviderAttempt(
                        attempt_number=attempt_number,
                        started_at=attempt_started,
                        latency_seconds=round(latency, 4),
                        status="provider_failed",
                        error_code="provider_call_failed",
                        error_message=_safe_message(exc),
                    )
                )
                if attempt_number <= self.max_retries and adapter.capabilities().retryable:
                    continue
                final_attempt = attempts[-1]
                break
            except InvalidProviderResult as exc:
                latency = time.perf_counter() - attempt_start
                final_attempt = ProviderAttempt(
                    attempt_number=attempt_number,
                    started_at=attempt_started,
                    latency_seconds=round(latency, 4),
                    status="invalid_result",
                    error_code="invalid_result",
                    error_message=_safe_message(exc),
                )
                attempts.append(final_attempt)
                break
            except Exception as exc:  # internal runner/provider bug
                latency = time.perf_counter() - attempt_start
                final_attempt = ProviderAttempt(
                    attempt_number=attempt_number,
                    started_at=attempt_started,
                    latency_seconds=round(latency, 4),
                    status="failed",
                    error_code="internal_error",
                    error_message=_safe_message(exc),
                )
                attempts.append(final_attempt)
                break

        if final_attempt is None:  # pragma: no cover - defensive
            final_attempt = ProviderAttempt(attempt_number=1, started_at=_now_iso(), status="failed")
            attempts.append(final_attempt)

        retry_count = len(attempts) - 1
        if final_attempt.status != "passed":
            return self._result(
                lane=lane, case=case, adapter=adapter, case_input=case_input,
                started_at=started_at, status=final_attempt.status,
                error_code=final_attempt.error_code or final_attempt.status,
                error_message=final_attempt.error_message,
                attempts=attempts, retry_count=retry_count,
                case_dir=case_dir,
            )

        assert outcome is not None
        return self._finish_success(
            lane=lane, case=case, adapter=adapter, case_input=case_input,
            started_at=started_at, attempts=attempts, retry_count=retry_count,
            outcome=outcome, warnings=warnings, case_dir=case_dir, run_dir=run_dir,
        )

    # -- success path -------------------------------------------------------

    def _finish_success(
        self,
        *,
        lane: str,
        case: Any,
        adapter: ProviderAdapter,
        case_input: Path,
        started_at: str,
        attempts: list[ProviderAttempt],
        retry_count: int,
        outcome: Any,
        warnings: list[str],
        case_dir: Path,
        run_dir: Path,
    ) -> ProviderRunResult:
        artifact_dir = case_dir / "artifacts"
        artifacts: list[ArtifactRef] = []
        for name, content in outcome.extra_artifacts:
            path = artifact_dir / name
            path.write_bytes(content)
            artifacts.append(ArtifactRef(kind="output", path=_rel(path, run_dir), sha256=sha256_file(path)))

        normalized_path = case_dir / "normalized.json"
        normalized_payload = redact_artifact(outcome.normalized)
        assert_no_secrets(normalized_payload)
        _write_json(normalized_path, normalized_payload)
        artifacts.append(
            ArtifactRef(kind="normalized", path=_rel(normalized_path, run_dir), sha256=sha256_file(normalized_path))
        )

        raw_path = case_dir / "raw_redacted.json"
        raw_payload = redact_artifact(outcome.raw_debug)
        assert_no_secrets(raw_payload)
        _write_json(raw_path, raw_payload)
        artifacts.append(
            ArtifactRef(kind="raw_debug", path=_rel(raw_path, run_dir), sha256=sha256_file(raw_path))
        )

        metrics, gates = self._score(lane, case, case_input, outcome, artifacts, run_dir)
        failed_gates = [g for g in gates if g.outcome == "fail"]
        unverified = [
            g for g in gates
            if g.outcome == "not_evaluated" and g.kind == "deterministic"
        ]
        if failed_gates:
            status = "failed"
            error_code = "threshold_failed"
            error_message = "; ".join(
                f"{g.gate}: {g.actual} < {g.threshold}" for g in failed_gates
            )
        elif unverified:
            status = "manual_review_required"
            error_code = "evidence_missing"
            error_message = "; ".join(
                f"{g.gate} could not be evaluated" for g in unverified
            )
        else:
            status = "passed"
            error_code = None
            error_message = None

        requires_manual_review = (
            bool(case.requires_manual_review)
            or any(g.kind == "comparative" for g in gates)
        )
        total_latency = round(
            sum(a.latency_seconds or 0.0 for a in attempts), 4
        )
        result = ProviderRunResult(
            lane=lane,
            provider=adapter.capabilities().provider,
            adapter_version=adapter.capabilities().adapter_version,
            model=outcome.model,
            api_version=outcome.api_version,
            provider_version=outcome.provider_version,
            case_id=case.case_id,
            case_input_sha256=sha256_file(case_input),
            status=status,
            started_at=started_at,
            completed_at=_now_iso(),
            latency_seconds=total_latency,
            retry_count=retry_count,
            attempts=attempts,
            metrics=metrics,
            gates=gates,
            usage=outcome.usage,
            cost=self.pricing.estimate(
                adapter.capabilities().pricing_key, outcome.usage
            ),
            artifacts=artifacts,
            warnings=warnings,
            error_code=error_code,
            error_message=error_message,
            requires_manual_review=requires_manual_review,
            prompt_version=outcome.prompt_version,
            renderer=outcome.renderer,
            font_manifest=outcome.font_manifest,
        )
        _write_json(case_dir / "result.json", result.model_dump(mode="json"))
        return result

    def _score(
        self,
        lane: str,
        case: Any,
        case_input: Path,
        outcome: Any,
        artifacts: list[ArtifactRef],
        run_dir: Path,
    ) -> tuple[dict[str, Any], list[GateDecision]]:
        if lane == "extraction":
            return evaluate_extraction(outcome.normalized, case)
        if lane == "layout":
            return evaluate_layout(outcome.normalized, case)
        if lane == "rendering":
            expected_profile = apply_missing_field_detection(
                CandidateProfile.model_validate_json(
                    (CORPUS_DIR / "expected_candidate_profile.json").read_text(encoding="utf-8")
                )
            )
            expected_profile.client_display_rules = {
                "salary_expectation": DisplayRule.PENDING_CONFIRMATION,
                "interview_availability": DisplayRule.PENDING_CONFIRMATION,
            }
            context = build_client_render_context(expected_profile)
            expected_terms = render_context_terms(context.model_dump(mode="json"))
            pdf_name = str(outcome.normalized["pdf_path"])
            pdf_path = _output_artifact_path(artifacts, run_dir, pdf_name)
            visual_similarity = self._visual_similarity(lane, case, run_dir, pdf_path)
            return evaluate_rendering(
                pdf_path,
                case,
                expected_terms=expected_terms,
                privacy_exclusions=list(case.privacy_exclusions),
                visual_similarity=visual_similarity,
            )
        if lane == "design":
            return evaluate_design(outcome.normalized, case)
        raise ValueError(f"Unsupported lane: {lane}")  # pragma: no cover

    def _visual_similarity(
        self, lane: str, case: Any, run_dir: Path, pdf_path: Path
    ) -> float | None:
        if lane != "rendering" or case.case_id != "controlled_docx_to_pdf":
            return None
        baseline_candidates = list(
            (
                run_dir
                / "cases"
                / "rendering"
                / case.case_id
                / "libreoffice"
                / "artifacts"
            ).glob("*.pdf")
        )
        if not baseline_candidates:
            return None
        baseline_pdf = baseline_candidates[0]
        if baseline_pdf == pdf_path:
            return None
        try:
            comparison = compare_pdf_layouts(
                baseline_pdf,
                pdf_path,
                pdf_path.parent / "vs_libreoffice",
            )
        except Exception:
            return None
        return round(comparison.average_layout_similarity, 4)

    # -- helpers ------------------------------------------------------------

    def _result(
        self,
        *,
        lane: str,
        case: Any,
        adapter: ProviderAdapter,
        case_input: Path,
        started_at: str,
        status: str,
        error_code: str,
        error_message: str | None,
        case_dir: Path,
        attempts: list[ProviderAttempt] | None = None,
        retry_count: int = 0,
    ) -> ProviderRunResult:
        result = ProviderRunResult(
            lane=lane,
            provider=adapter.capabilities().provider,
            adapter_version=adapter.capabilities().adapter_version,
            case_id=case.case_id,
            case_input_sha256=sha256_file(case_input),
            status=status,
            started_at=started_at,
            completed_at=_now_iso(),
            retry_count=retry_count,
            attempts=attempts or [],
            error_code=error_code,
            error_message=error_message,
            requires_manual_review=False,
        )
        _write_json(case_dir / "result.json", result.model_dump(mode="json"))
        return result


# -- module-level helpers ----------------------------------------------------


def _all_adapters() -> dict[tuple[str, str], ProviderAdapter]:
    from tests.commercial_api import registry

    return registry.REGISTRY


def _provider_sort_key(lane: str, provider: str) -> tuple[int, str]:
    """Run the accepted baselines before alternative providers."""
    baseline_rank = 0 if (
        (lane == "rendering" and provider == "libreoffice")
        or (lane == "design" and provider == "mock_designer")
    ) else 1
    return baseline_rank, provider


def _output_artifact_path(
    artifacts: list[ArtifactRef], run_dir: Path, filename: str
) -> Path:
    for artifact in artifacts:
        if artifact.kind == "output" and Path(artifact.path).name == filename:
            return run_dir / artifact.path
    raise ValueError(f"Rendered output artifact not found: {filename}")


def _status_counts(results: list[ProviderRunResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def _build_summary(run_manifest: RunManifest) -> RunSummary:
    rows: list[dict[str, Any]] = []
    for result in run_manifest.results:
        rows.append(
            {
                "lane": result.lane,
                "provider": result.provider,
                "case_id": result.case_id,
                "status": result.status,
                "latency_seconds": result.latency_seconds,
                "retry_count": result.retry_count,
                "error_code": result.error_code,
                "result_path": f"cases/{result.lane}/{result.case_id}/{result.provider}/result.json",
            }
        )
    return RunSummary(
        run_id=run_manifest.run_id,
        created_at=run_manifest.created_at,
        corpus_version=run_manifest.corpus.corpus_version,
        status_counts=run_manifest.status_counts,
        rows=rows,
    )


def _runtime_info() -> RuntimeInfo:
    package_names = (
        "pydantic",
        "openai",
        "anthropic",
        "httpx",
        "pypdf",
        "pypdfium2",
        "pdfplumber",
        "python-docx",
        "fastapi",
    )
    packages: dict[str, str] = {}
    for name in package_names:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not-installed"
    return RuntimeInfo(
        python_version=platform.python_version(),
        platform=platform.platform(),
        os_name=platform.system(),
        packages=packages,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path, run_dir: Path) -> str:
    return str(path.relative_to(run_dir))


def _safe_message(exc: Exception) -> str:
    from tests.commercial_api.redaction import sanitize_text

    return sanitize_text(f"{type(exc).__name__}: {exc}")[:2000]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
