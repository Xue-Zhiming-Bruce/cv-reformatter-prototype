from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.commercial_api.models import RunManifest


def render_report(manifest: RunManifest, *, title: str | None = None) -> str:
    """Deterministic markdown report from a stored run manifest.

    No provider calls are made. The report separates deterministic gates,
    comparative metrics, warnings, manual-review items, and unavailable
    evidence instead of hiding them behind a single score.
    """
    lines: list[str] = [
        f"# {title or 'Commercial API Evaluation Report'}",
        "",
        f"- Run: `{manifest.run_id}`",
        f"- Created: `{manifest.created_at}`",
        f"- Source revision: `{manifest.source_revision or 'unavailable'}`",
        f"- Dirty worktree: `{'yes' if manifest.dirty_worktree else 'no'}`",
        f"- Corpus: `{manifest.corpus.corpus_version}` (schema `{manifest.corpus.schema_version}`)",
        f"- Schema: `{manifest.schema_version}`",
        f"- Command: `{' '.join(manifest.command)}`",
        "",
        "This is evaluation evidence, not a provider selection or an architecture decision.",
        "",
        "## Status summary",
        "",
    ]
    counts = manifest.status_counts or {}
    if not counts:
        lines.append("No results recorded.")
    else:
        lines.append("| Status | Count |")
        lines.append("| --- | ---: |")
        for status in sorted(counts):
            lines.append(f"| {status} | {counts[status]} |")
    lines.append("")

    for lane in _ordered_lanes(manifest):
        lane_results = [r for r in manifest.results if r.lane == lane]
        if not lane_results:
            continue
        lines.extend([f"## Lane: {lane}", ""])
        lines.append(
            "| Provider | Case | Status | Latency (s) | Retries | Gates | Key metrics | Usage | Cost (USD) |"
        )
        lines.append("| --- | --- | --- | ---: | ---: | --- | --- | ---: | ---: |")
        for result in sorted(lane_results, key=lambda r: (r.provider, r.case_id)):
            latency = f"{result.latency_seconds:.4f}" if result.latency_seconds is not None else "—"
            gates = _gate_summary(result.gates)
            metrics = _compact_metrics(lane, result.metrics)
            usage = _usage_summary(result.usage)
            cost = f"{result.cost.estimated_usd:.6f}" if result.cost and result.cost.estimated_usd is not None else "—"
            lines.append(
                f"| {result.provider} | {result.case_id} | {result.status} | {latency} | "
                f"{result.retry_count} | {gates} | {metrics} | {usage} | {cost} |"
            )
        lines.append("")

    non_passed = [r for r in manifest.results if r.status != "passed"]
    lines.extend(["## Failures, skips, and unconfigured", ""])
    if not non_passed:
        lines.append("None.")
    else:
        for result in non_passed:
            lines.append(
                f"- `{result.lane}/{result.case_id}/{result.provider}`: "
                f"status=`{result.status}` code=`{result.error_code or '—'}` — "
                f"{result.error_message or ''}"
            )
    lines.append("")

    manual_review = [r for r in manifest.results if r.requires_manual_review]
    lines.extend(["## Manual review", ""])
    if not manual_review:
        lines.append("None required by this run.")
    else:
        for result in manual_review:
            lines.append(
                f"- `{result.lane}/{result.case_id}/{result.provider}` "
                f"(status=`{result.status}`): open the output artifacts and review "
                "content, privacy, and visual fidelity before any product decision."
            )
    lines.append("")

    warnings = [
        (r, w) for r in manifest.results for w in r.warnings
    ]
    lines.extend(["## Warnings", ""])
    if not warnings:
        lines.append("None.")
    else:
        for result, warning in warnings:
            lines.append(
                f"- `{result.lane}/{result.case_id}/{result.provider}`: {warning}"
            )
    lines.append("")

    not_evaluated = [
        (r, g)
        for r in manifest.results
        for g in r.gates
        if g.outcome == "not_evaluated"
    ]
    lines.extend(["## Unavailable evidence", ""])
    if not not_evaluated:
        lines.append("None.")
    else:
        for result, gate in not_evaluated:
            lines.append(
                f"- `{result.lane}/{result.case_id}/{result.provider}` gate `{gate.gate}`: "
                f"{gate.note or 'not evaluated'} (actual={gate.actual or '—'})."
            )
    lines.append("")

    lines.extend(
        [
            "## Decision guardrail",
            "",
            "There is no single aggregate score. Review the deterministic gates, "
            "comparative metrics, warnings, manual-review items, and raw artifacts. "
            "Provider selection is a human architecture decision supported by results; "
            "it is never an automatic outcome of this report. A skipped or unconfigured "
            "provider is not a pass.",
            "",
        ]
    )
    return "\n".join(lines)


def _ordered_lanes(manifest: RunManifest) -> list[str]:
    order = ["extraction", "layout", "rendering", "design", "visual_diagnosis", "e2e"]
    present = {r.lane for r in manifest.results}
    return [lane for lane in order if lane in present]


def _gate_summary(gates: list[Any]) -> str:
    if not gates:
        return "—"
    return ", ".join(
        f"{g.gate}={g.outcome}" for g in gates
    )


def _compact_metrics(lane: str, metrics: dict[str, Any]) -> str:
    keys_by_lane = {
        "extraction": ("field_accuracy", "null_safety", "missing_field_accuracy"),
        "layout": ("required_text_recall", "inferred_columns", "bounds_coverage", "semantic_heading_recall"),
        "rendering": ("content_recall", "page_count", "visual_similarity_to_baseline"),
        "design": (
            "mapping_accuracy",
            "unsupported_preservation",
            "fact_contamination",
            "invalid_reference_rate",
            "review_escalation_accuracy",
            "compiler_acceptance",
        ),
    }
    parts = []
    for key in keys_by_lane.get(lane, ()):
        if key in metrics:
            parts.append(f"{key}={metrics[key]}")
    return ", ".join(parts) or "—"


def _usage_summary(usage: Any) -> str:
    if usage.total_tokens is not None:
        return f"{usage.total_tokens} tokens"
    if usage.pages is not None:
        return f"{usage.pages} pages"
    if usage.transactions is not None:
        return f"{usage.transactions} tx"
    return "—"


def write_report(manifest: RunManifest, run_dir: Path) -> Path:
    report_path = run_dir / "report.md"
    report_path.write_text(render_report(manifest), encoding="utf-8")
    return report_path
