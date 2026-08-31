from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.commercial_api.models import (
    CompareReport,
    CompareRow,
    ProviderRunResult,
    RunManifest,
)


class CompareError(RuntimeError):
    """A run directory cannot be read as a run manifest."""


# Only metrics with an unambiguous direction belong here. Counts, page count,
# inferred columns, and file size are intentionally excluded because a change
# is not inherently better or worse without case-specific context.
HIGHER_IS_BETTER_METRICS = {
    "field_accuracy",
    "null_safety",
    "missing_field_accuracy",
    "required_text_recall",
    "bounds_coverage",
    "block_coverage",
    "style_coverage",
    "role_coverage",
    "semantic_heading_recall",
    "content_recall",
    "visual_similarity_to_baseline",
}
METRIC_DELTA_EPSILON = 0.0001


def load_run_manifest(run_dir: Path) -> RunManifest:
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.is_file():
        return RunManifest.model_validate(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    raise CompareError(
        f"No run_manifest.json found in {run_dir}. This directory is not a commercial_api run."
    )


def compare_runs(
    base: RunManifest,
    candidate: RunManifest,
) -> CompareReport:
    """Compare two completed runs without calling any provider.

    Highlights regressions, improvements, missing cases, configuration
    differences, and non-comparable results. Corpus mismatches make a run
    non-comparable at the run level; result rows are still listed.
    """
    comparable = base.corpus.corpus_version == candidate.corpus.corpus_version
    notes: list[str] = []
    if not comparable:
        notes.append(
            f"Corpus versions differ ({base.corpus.corpus_version} vs "
            f"{candidate.corpus.corpus_version}); metric deltas are not comparable."
        )

    base_by_key = {(r.lane, r.case_id, r.provider): r for r in base.results}
    candidate_by_key = {(r.lane, r.case_id, r.provider): r for r in candidate.results}
    all_keys = sorted(set(base_by_key) | set(candidate_by_key))

    rows: list[CompareRow] = []
    for key in all_keys:
        base_result = base_by_key.get(key)
        candidate_result = candidate_by_key.get(key)
        lane, case_id, provider = key
        if base_result is None:
            rows.append(
                CompareRow(
                    lane=lane, case_id=case_id, provider=provider,
                    candidate_status=candidate_result.status,
                    outcome="missing_in_base",
                    candidate_latency_seconds=candidate_result.latency_seconds,
                    note="Case present only in candidate run.",
                )
            )
            continue
        if candidate_result is None:
            rows.append(
                CompareRow(
                    lane=lane, case_id=case_id, provider=provider,
                    base_status=base_result.status,
                    outcome="missing_in_candidate",
                    base_latency_seconds=base_result.latency_seconds,
                    note="Case present only in base run.",
                )
            )
            continue

        deltas = _metric_deltas(base_result, candidate_result)
        if not comparable:
            rows.append(
                CompareRow(
                    lane=lane, case_id=case_id, provider=provider,
                    base_status=base_result.status,
                    candidate_status=candidate_result.status,
                    outcome="non_comparable",
                    base_latency_seconds=base_result.latency_seconds,
                    candidate_latency_seconds=candidate_result.latency_seconds,
                    metric_deltas=deltas,
                    note="Corpus versions differ.",
                )
            )
            continue

        outcome, note = _transition_outcome(base_result, candidate_result, deltas)
        rows.append(
            CompareRow(
                lane=lane, case_id=case_id, provider=provider,
                base_status=base_result.status,
                candidate_status=candidate_result.status,
                outcome=outcome,
                base_latency_seconds=base_result.latency_seconds,
                candidate_latency_seconds=candidate_result.latency_seconds,
                metric_deltas=deltas,
                note=note,
            )
        )

    config_differences = _config_differences(base, candidate)
    return CompareReport(
        base_run_id=base.run_id,
        candidate_run_id=candidate.run_id,
        base_corpus_version=base.corpus.corpus_version,
        candidate_corpus_version=candidate.corpus.corpus_version,
        comparable=comparable,
        rows=rows,
        config_differences=config_differences,
        notes=notes,
    )


def render_compare_markdown(report: CompareReport) -> str:
    lines = [
        "# Run Comparison",
        "",
        f"- Base: `{report.base_run_id}`",
        f"- Candidate: `{report.candidate_run_id}`",
        f"- Base corpus: `{report.base_corpus_version or '—'}`",
        f"- Candidate corpus: `{report.candidate_corpus_version or '—'}`",
        f"- Comparable: `{'yes' if report.comparable else 'no'}`",
        "",
    ]
    if report.notes:
        lines.append("## Notes")
        lines.append("")
        for note in report.notes:
            lines.append(f"- {note}")
        lines.append("")

    if report.config_differences:
        lines.extend(["## Configuration differences", ""])
        for difference in report.config_differences:
            lines.append(f"- {difference}")
        lines.append("")

    lines.extend(
        [
            "## Per-case transitions",
            "",
            "| Lane | Case | Provider | Base | Candidate | Outcome | Metric Δ | Latency Δ (s) |",
            "| --- | --- | --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for row in report.rows:
        base = row.base_status or "—"
        candidate = row.candidate_status or "—"
        latency_delta = _latency_delta(row.base_latency_seconds, row.candidate_latency_seconds)
        metric_delta = _format_metric_deltas(row.metric_deltas)
        lines.append(
            f"| {row.lane} | {row.case_id} | {row.provider} | {base} | {candidate} | "
            f"{row.outcome} | {metric_delta} | {latency_delta} |"
        )
    lines.append("")
    regressions = [r for r in report.rows if r.outcome == "regression"]
    improvements = [r for r in report.rows if r.outcome == "improvement"]
    missing = [r for r in report.rows if r.outcome.startswith("missing")]
    non_comparable = [r for r in report.rows if r.outcome == "non_comparable"]
    lines.extend(
        [
            "## Highlights",
            "",
            f"- Regressions: **{len(regressions)}**",
            f"- Improvements: **{len(improvements)}**",
            f"- Missing on one side: **{len(missing)}**",
            f"- Non-comparable rows: **{len(non_comparable)}**",
            "",
        ]
    )
    if regressions:
        lines.append("### Regressions")
        for row in regressions:
            lines.append(f"- {row.lane}/{row.case_id}/{row.provider}: {row.base_status} → {row.candidate_status}")
        lines.append("")
    return "\n".join(lines)


def _transition_outcome(
    base: ProviderRunResult,
    candidate: ProviderRunResult,
    metric_deltas: dict[str, float],
) -> tuple[str, str | None]:
    base_passed = base.status == "passed"
    candidate_passed = candidate.status == "passed"
    if base_passed and not candidate_passed:
        return "regression", f"{base.status} → {candidate.status}"
    if not base_passed and candidate_passed:
        return "improvement", f"{base.status} → {candidate.status}"
    if base_passed and candidate_passed:
        regressions = {
            key: delta
            for key, delta in metric_deltas.items()
            if key in HIGHER_IS_BETTER_METRICS and delta < -METRIC_DELTA_EPSILON
        }
        improvements = {
            key: delta
            for key, delta in metric_deltas.items()
            if key in HIGHER_IS_BETTER_METRICS and delta > METRIC_DELTA_EPSILON
        }
        if regressions:
            return "regression", _metric_change_note("quality regression", regressions)
        if improvements:
            return "improvement", _metric_change_note("quality improvement", improvements)
        return "unchanged_pass", None
    return "unchanged_fail", f"{base.status} → {candidate.status}"


def _metric_deltas(base: ProviderRunResult, candidate: ProviderRunResult) -> dict[str, float]:
    deltas: dict[str, float] = {}
    for key, value in candidate.metrics.items():
        if key not in base.metrics:
            continue
        base_value = base.metrics[key]
        if isinstance(value, (int, float)) and isinstance(base_value, (int, float)):
            delta = round(float(value) - float(base_value), 4)
            if delta != 0.0:
                deltas[key] = delta
    return deltas


def _config_differences(base: RunManifest, candidate: RunManifest) -> list[str]:
    differences: list[str] = []
    base_config = base.configuration
    candidate_config = candidate.configuration
    for field in ("lanes", "providers", "cases", "max_cases", "max_requests", "timeout_seconds", "max_retries", "live", "env_file", "pricing_file"):
        base_value = getattr(base_config, field)
        candidate_value = getattr(candidate_config, field)
        if base_value != candidate_value:
            differences.append(
                f"{field}: {base_value!r} -> {candidate_value!r}"
            )
    return differences


def _latency_delta(base: float | None, candidate: float | None) -> str:
    if base is None or candidate is None:
        return "—"
    return f"{candidate - base:+.4f}"


def _format_metric_deltas(deltas: dict[str, float]) -> str:
    if not deltas:
        return "—"
    return ", ".join(f"{key}={value:+.4f}" for key, value in sorted(deltas.items()))


def _metric_change_note(label: str, deltas: dict[str, float]) -> str:
    details = ", ".join(
        f"{key}={value:+.4f}" for key, value in sorted(deltas.items())
    )
    return f"{label}: {details}"
