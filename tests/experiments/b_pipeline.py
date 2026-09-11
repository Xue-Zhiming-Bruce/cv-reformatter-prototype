"""Run the isolated B-pipeline Template Reviewer experiment."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal

import httpx
from dotenv import load_dotenv
from lxml import html as lxml_html
from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.pdf_reader import read_pdf_text
from tests.experiments.a_pipeline import (
    ROOT,
    RUNS,
    _analyze_target,
    _artifact_ref,
    _export_pinned_html_to_pdf,
    _html_text,
    _image_sources,
    _inject_icon_fonts,
    _inject_local_fonts,
    _missing_source_tokens,
    _normalized_text,
    _pinned_chrome_environment,
    _render_pages,
    _require_geometry,
    _sha256,
    _side_by_side,
    _source_fragments,
    _text_sha256,
    _visual_evidence_board,
    build_filler_context,
    build_format_summary,
    run_hard_gates,
)
from tests.experiments.deepseek import (
    PipelineCallError,
    _chat_content,
    _html_call,
    _structured_chat_call,
    visual_client,
)
from tests.experiments.fill_plan import (
    CONTACT_KIND_CHECKS,
    _ROLE_WORDS,
    _section_semantic,
    analyze_candidate_provenance,
    deduplicate_candidate_html,
    normalize_header_element,
    normalize_section_headings,
    provenance_report,
    source_blocks,
    source_lines,
    validate_candidate_html,
)
from tests.experiments.refinement import GateFailure, HardGateResult, validate_filler_conformance

DEFAULT_TARGET = ROOT / "tests/local_datasets/resume_matrix/resume_E.pdf"
DEFAULT_SOURCE = ROOT / "tests/local_datasets/resume_matrix/resume_D.pdf"
MAX_REVIEWER_REVISIONS = 5
MAX_FILLER_ATTEMPTS_PER_TEMPLATE = 5


class TemplateReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["revise", "ready_for_audit"]
    diagnosis: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    template_html: str = Field(min_length=20)


class FinalReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    diagnosis: list[str] = Field(default_factory=list)


_ICON_FAMILY_RULE = (
    ".fa, .fas, .far, .fab, .fa-brands, .fa-solid { "
    "font-family: 'Font Awesome 6 Free', 'Font Awesome 6 Brands', sans-serif; "
    "font-style: normal; }"
    ".fa-brands, .fab { font-weight: 400; }"
    ".fa-solid, .fas { font-weight: 900; }"
)


def _ensure_icon_font_family(rendered_html: str) -> str:
    """B-pipeline icon fix; root cause verified offline against the real render
    path (tmp/icon_repro, 2026-09-09): Reviewer templates style icons via the
    Font Awesome CSS convention (.fa/.fas/.far/.fab) while the Filler writes
    fa-brands/fa-solid elements, so NO rule applies the icon font family and
    brand glyphs render as tofu (round_5 render embedded no FA font). One
    inert rule appended after _inject_icon_fonts guarantees family
    application for both conventions; it names the exact families injection
    embeds. Owner finding 2026-09-09 (run 20260909T152115Z): the <i> element's
    default italic was never reset either, so Chrome synthesized oblique
    glyphs — the same convention-bridge bug class (§7.3): the rule now also
    sets font-style normal and the FA weights. Runs after filler conformance,
    so the Filler never sees it."""
    if not re.search(r'class="[^"]*\bfa-(brands|solid)\b', rendered_html):
        return rendered_html
    if _ICON_FAMILY_RULE in rendered_html:
        return rendered_html
    return re.sub(r"</style>", _ICON_FAMILY_RULE + "\n</style>", rendered_html, count=1, flags=re.I)


def validate_filler_header_structure(template_html: str, candidate_html: str) -> list[str]:
    """Close the Template Reviewer / Filler boundary for the header (owner
    finding 2026-09-09): the Filler added layout-level direct children to the
    header root (round_5 p.body-text nodes) and dropped template wrappers.
    The template owns header structure; the Filler may only fill declared
    slots and add entries INSIDE declared containers. Minimal targeted check:
    the direct-children signature (tag + class) of the header element must be
    unchanged. No generic DOM whitelist.
    Owner finding 2026-09-09 (run 20260909T160348Z): a template-declared slot
    whose placeholder has NO source content (e.g. a location slot for a source
    without a location line) may be dropped by the Filler — rendering the
    placeholder text would be worse — so the comparison filters template
    children that are placeholder-only and candidate children that are empty.
    Adding real content nodes and dropping filled slots stays rejected."""
    # The div.header -> header rename is deterministic bookkeeping
    # (normalize_header_element); judge structure on the renamed form.
    if not lxml_html.document_fromstring(template_html).xpath("//header"):
        candidate_html = normalize_header_element(candidate_html)
    try:
        template_tree = lxml_html.document_fromstring(template_html)
        candidate_tree = lxml_html.document_fromstring(candidate_html)
    except Exception:
        return ["filler header structure check: invalid HTML"]

    def _headers(tree: Any) -> list[Any]:
        found = tree.xpath("//header")
        return found or tree.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' header ')]")

    template_headers, candidate_headers = _headers(template_tree), _headers(candidate_tree)
    if not template_headers:
        return []  # no template header: nothing to protect
    if not candidate_headers:
        return ["Filler removed the header element"]

    def _visible(child: Any) -> str:
        return re.sub(r"\s+", " ", re.sub(r"\[[^]]+\]", "", " ".join(child.itertext()))).strip()

    def _rows(header: Any, *, strip_placeholders: bool) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for child in header:
            if not isinstance(child.tag, str):
                continue
            text = _visible(child) if strip_placeholders else re.sub(r"\s+", " ", " ".join(child.itertext())).strip()
            rows.append((str(child.tag).casefold(), child.get("class") or "", text))
        return rows

    template_rows = _rows(template_headers[0], strip_placeholders=True)
    candidate_rows = [
        (tag, cls) for tag, cls, text in _rows(candidate_headers[0], strip_placeholders=False)
        if text  # a candidate child with no visible text renders nothing
    ]
    # A template child whose text is placeholder-only (strips to nothing) is a
    # declared slot: the Filler may fill it (then it appears in the candidate)
    # or drop it when no source content maps to it. Real template children and
    # filled slots must correspond; extra candidate nodes are rejected.
    available = Counter(candidate_rows)
    matched = 0
    for tag, cls, text in template_rows:
        if available[(tag, cls)] == 0:
            if text:
                return [
                    "Filler changed the header root's direct children; the template owns "
                    "header structure and this child carries template content "
                    f"({tag}.{cls} with {text!r} is missing from the filled header)"
                ]
            continue  # unfilled placeholder slot legitimately dropped
        available[(tag, cls)] -= 1
        matched += 1
    if any(count > 0 for count in available.values()):
        extra = sorted(key for key, count in available.items() if count > 0)
        return [
            "Filler added header root children; the template owns header structure "
            f"(unexpected filled children: {extra!r})"
        ]
    if matched == 0 and candidate_rows:
        return ["Filler changed the header root's direct children; the template owns header structure"]
    return []


def _page_text_lines(pdf_path: Path, *, max_lines: int = 10) -> list[dict[str, Any]]:
    """Deterministic baseline grouping of page-1 text (pdfplumber), so the
    Reviewer can compare vertical line structure without guessing from raw
    HTML or images alone."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        words = pdf.pages[0].extract_words()
    lines: list[dict[str, Any]] = []
    for word in words:
        top = round(float(word["top"]), 1)
        line = next((row for row in lines if abs(row["top"] - top) <= 1.5), None)
        if line is None:
            line = {"top": top, "words": []}
            lines.append(line)
        line["words"].append({
            "text": word["text"],
            "x0": round(float(word["x0"]), 1),
            "x1": round(float(word["x1"]), 1),
            "height": round(float(word["bottom"]) - float(word["top"]), 1),
        })
    lines.sort(key=lambda row: row["top"])
    return lines[:max_lines]


def _header_css_facts(template_html: str) -> list[dict[str, str]]:
    """CSS facts affecting the header, parsed from the template's style
    blocks: resolved display/flex/wrap/alignment per matching rule."""
    facts: list[dict[str, str]] = []
    relevant = {"display", "flex-direction", "flex-wrap", "align-items", "justify-content", "text-align"}
    for match in re.finditer(r"([^{}]+)\{([^}]*)\}", template_html):
        selector = " ".join(match.group(1).split())
        if not re.search(r"\b(header|contact-item|fa-)\b", selector):
            continue
        properties = {
            part.split(":", 1)[0].strip(): part.split(":", 1)[1].strip().rstrip(";")
            for part in match.group(2).split(";")
            if ":" in part
        }
        keep = {name: value for name, value in properties.items() if name in relevant}
        if keep:
            facts.append({"selector": selector, **keep})
    return facts


def _header_dom_facts(document_html: str) -> dict[str, Any] | None:
    try:
        tree = lxml_html.document_fromstring(document_html)
    except Exception:
        return None
    headers = tree.xpath("//header") or tree.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' header ')]")
    if not headers:
        return None
    header = headers[0]
    return {
        "tag": str(header.tag),
        "class": header.get("class") or "",
        "direct_children": [
            {
                "tag": str(child.tag),
                "class": child.get("class") or "",
                "slots": child.xpath(".//@data-slot"),
            }
            for child in header
            if isinstance(child.tag, str)
        ],
    }


def _render_diagnostic(target_pdf: Path, render_pdf: Path, rendered_html: str, template_html: str) -> dict[str, Any]:
    """Version-aligned, structured facts for the render the Reviewer is about
    to judge (task 2026-09-09: stop making the model guess geometry). Reuses
    pdfplumber + the template's own CSS; no second evidence framework."""
    return {
        "schema_version": "b-pipeline-render-facts/1",
        "target_page1_lines": _page_text_lines(target_pdf),
        "current_page1_lines": _page_text_lines(render_pdf),
        "current_header_dom": _header_dom_facts(rendered_html),
        "template_header_dom": _header_dom_facts(template_html),
        "header_css": _header_css_facts(template_html),
    }


@dataclass(frozen=True)
class BPipelineResult:
    accepted: bool
    stop_reason: str
    round_number: int | None
    template_html: str | None
    filled_html: str | None
    context: dict[str, Any]
    usage: dict[str, dict[str, int]]


def template_contamination(
    template_html: str,
    candidate_text: str,
    target_text: str,
) -> list[dict[str, str]]:
    """Find visible candidate facts or non-heading target facts in an empty template.

    Heading-slot placeholders (`data-slot="heading"`) carry target-measured
    heading text by design — the Filler overwrites them with verbatim source
    headings and the provenance gates re-verify every line — so the target
    scan exempts them structurally: they are removed from the scanned text
    rather than recognized by the heading-vocabulary alias table (owner
    ruling 2026-09-10, E→F third freeze; the alias check remains only as an
    auxiliary skip for non-slot section-heading fragments and is never the
    exemption basis for slot text). Candidate facts stay forbidden everywhere.
    """
    stripped = re.sub(r"\[[^]]+\]", "", template_html)
    visible, _ = _html_text(stripped)
    heading_tree = lxml_html.document_fromstring(stripped)
    for heading in heading_tree.xpath("//*[@data-slot='heading']"):
        heading.getparent().remove(heading)
    target_visible, _ = _html_text(lxml_html.tostring(heading_tree, encoding="unicode"))
    findings: list[dict[str, str]] = []
    for origin, source in (("candidate", candidate_text), ("target", target_text)):
        scan_target = visible if origin == "candidate" else target_visible
        for fragment in _source_fragments(source):
            if _section_semantic(fragment):
                continue
            normalized = _normalized_text(fragment)
            if normalized and normalized in _normalized_text(scan_target):
                finding = {"origin": origin, "text": fragment}
                if finding not in findings:
                    findings.append(finding)
    return findings


def source_header_units(source_text: str) -> list[dict[str, str]]:
    """Typed inventory of document-block (header) content units (§7.5):
    contact kinds by content, a location line by City/State shape, a title by
    role words; of the remaining lines the first is the name and later ones
    are taglines. Sections are NOT part of this inventory: the render may
    legitimately need candidate-only extension sections, and section
    placement stays governed by the fill-time block gates."""
    lines = source_lines(source_text)
    blocks = source_blocks(source_text)
    units: list[dict[str, str]] = []
    untyped: list[tuple[str, str]] = []
    for line_id, line in lines.items():
        if blocks[line_id] != "document":
            continue
        # Owner ruling 2026-09-10 (E→F second freeze): a single header line may
        # carry several contact kinds (e.g. email + github + linkedin on one
        # line); every check judges independently, so the full kind set is
        # collected instead of the first match.
        kinds = [name for name, check in CONTACT_KIND_CHECKS.items() if check(line)]
        if not kinds and re.fullmatch(r"[A-Za-z .'-]+,\s*[A-Za-z .'-]+", line) and "|" not in line and not _ROLE_WORDS.search(line):
            kinds = ["location"]
        if not kinds and _ROLE_WORDS.search(line):
            kinds = ["title"]
        if kinds:
            for kind in kinds:
                units.append({"kind": kind, "source_line_id": line_id, "source_text": line})
        else:
            untyped.append((line_id, line))
    for index, (line_id, line) in enumerate(untyped):
        units.append({"kind": "name" if index == 0 else "tagline", "source_line_id": line_id, "source_text": line})
    return units


def template_slot_gaps(template_html: str, source_text: str, *, contact_icons_present: bool = True) -> list[dict[str, str]]:
    """Slot-inventory contract at B's template exit (owner decision 2026-09-09,
    §7.5 of PIPELINE_EVOLUTION_PROPOSAL): every header-type content unit the
    source requires needs a template-declared landing slot BEFORE filling —
    the Filler can only fill declared slots and must never invent header
    structure, so a missing slot is a template defect (measured D→E run
    20260909T103038Z burned five rounds putting 'Senior Business Person' into
    the location slot). Contact units match by kind (fa-* icon class); other
    units match by data-slot name. Candidate facts or target-sample facts in
    the template stay forbidden (template_contamination)."""
    units = source_header_units(source_text)
    if not units:
        return []
    try:
        tree = lxml_html.document_fromstring(normalize_header_element(template_html))
    except Exception:
        return [{**unit, "detail": "invalid template HTML"} for unit in units]
    headers = tree.xpath("//header")
    if not headers:
        return [{**unit, "detail": "template declares no header element"} for unit in units]
    slots = set(headers[0].xpath(".//@data-slot"))
    kinds: set[str] = set()
    for item in headers[0].xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' contact-item ')]"):
        classes = " ".join(item.xpath(".//i/@class"))
        kind = next((name for name in CONTACT_KIND_CHECKS if f"fa-{name}" in classes), None)
        if kind:
            kinds.add(kind)
    gaps: list[dict[str, str]] = []
    for unit in units:
        kind = unit["kind"]
        # Owner ruling 2026-09-10 (E→F twelfth freeze): when the target's own
        # contact line has no icon glyphs (compiler suppresses icon elements),
        # contact kinds land by their declared data-slot instead of the fa-*
        # icon class — the slot contract follows the target-measured design.
        has_landing = (
            (kind in slots if not contact_icons_present else kind in kinds)
            if kind in CONTACT_KIND_CHECKS
            else (kind in slots)
        )
        if has_landing:
            continue
        gaps.append({
            **unit,
            "detail": (
                f"no contact-item slot with a fa-{kind} icon"
                if kind in CONTACT_KIND_CHECKS
                else f"no data-slot='{kind}' element in the header"
            ),
        })
    return gaps


def run_reviewer_loop(
    initial_template: str,
    candidate_text: str,
    target_text: str,
    review_template: Callable[[dict[str, Any]], tuple[TemplateReview, dict[str, int]]],
    fill: Callable[[str, dict[str, Any]], tuple[str, dict[str, int]]],
    gate: Callable[[int, str, str], HardGateResult],
    review_final: Callable[[dict[str, Any]], tuple[FinalReview, dict[str, int]]],
    *,
    normalize_candidate: Callable[[str, str], str] | None = None,
    artifact_root: Path | None = None,
    max_revisions: int = MAX_REVIEWER_REVISIONS,
    max_filler_attempts: int = MAX_FILLER_ATTEMPTS_PER_TEMPLATE,
    rendered_rounds: set[int] | None = None,
    owner_feedback: list[str] | None = None,
) -> BPipelineResult:
    """Small reviewer-owned loop; presentation never routes through Builder operations."""
    context: dict[str, Any] = {
        "objective": "Match target presentation without changing candidate facts",
        "current_template_version": 0,
        "unresolved_issues": [],
        "failed_approaches": [],
        "rounds": [],
    }
    usage = {name: Counter() for name in ("template_reviewer", "filler", "final_reviewer")}
    current_template = initial_template
    revision_count = 0
    final_reopens = 0
    last_filled: str | None = None
    last_valid_round: int | None = None
    initial_template_sha = _text_sha256(initial_template)

    def persist() -> None:
        if artifact_root:
            artifact_root.joinpath("context.json").write_text(
                json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def reviewed_versions(round_number: int) -> dict[str, Any]:
        """Version alignment for one review call (owner finding 2026-09-09:
        a round's diagnosis describes the PREVIOUS round's render, while the
        template in the same directory is the NEXT version — record both so no
        one can misread the pairing as hallucination)."""
        previous = round_number - 1
        reviewed_render = previous if previous >= 1 and rendered_rounds and previous in rendered_rounds else None
        return {
            "reviewed_render_version": reviewed_render,
            "reviewed_template_version": previous if previous >= 1 else 0,
            "reviewed_template_sha256": initial_template_sha if previous < 1 else (
                context["rounds"][previous - 1]["produced_template_sha256"]
                if previous - 1 < len(context["rounds"]) else None
            ),
        }

    def run_final_review(
        render_round: int,
        template_version: int,
        template_sha: str,
        phase: str,
        round_dir: Path | None,
    ) -> tuple[FinalReview, dict[str, Any]]:
        final_context = {
            "objective": context["objective"],
            "phase": phase,
            "round": render_round,
            "reviewed_render_version": render_round,
            "reviewed_template_version": template_version,
            "reviewed_template_sha256": template_sha,
            "template_revisions_used": revision_count,
            "hard_gates": context["rounds"][render_round - 1]["hard_gates"] if render_round - 1 < len(context["rounds"]) else None,
        }
        final_review, final_usage = review_final(final_context)
        usage["final_reviewer"].update(final_usage)
        record = {**final_context, "result": final_review.model_dump(mode="json")}
        if round_dir:
            name = "final_review.json" if phase != "budget_exhausted" or not round_dir.joinpath("final_review.json").exists() else "final_review_budget_exhausted.json"
            round_dir.joinpath(name).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return final_review, record

    for round_number in range(1, max_revisions + 2):
        versions = reviewed_versions(round_number)
        reviewer_context = {
            **context,
            "current_template_html": current_template,
            "last_round_number": context["rounds"][-1]["round"] if context["rounds"] else None,
            # Owner finding 2026-09-09 (run 20260909T143203Z): the Reviewer
            # ignores gate failures buried in rounds history and burns the
            # budget on geometry — surface the slot contract at the top level.
            "slot_contract_violations": template_slot_gaps(current_template, candidate_text),
            "owner_feedback": owner_feedback or [],
            **versions,
            "final_review_feedback": context.get("final_review_feedback"),
            "template_revisions_remaining": max_revisions - revision_count,
        }
        review, review_usage = review_template(reviewer_context)
        usage["template_reviewer"].update(review_usage)
        changed_template = review.template_html != current_template
        if revision_count >= max_revisions and (review.decision == "revise" or changed_template):
            # Owner rule 2026-09-09: budget exhaustion is not quality acceptance.
            # Even when the Template Reviewer never returned ready_for_audit, the
            # last valid render gets one read-only final review.
            context["terminal_template_review"] = {**review.model_dump(mode="json"), **versions}
            if last_valid_round is not None:
                final_review, final_record = run_final_review(
                    last_valid_round,
                    last_valid_round,
                    context["rounds"][last_valid_round - 1]["produced_template_sha256"],
                    "budget_exhausted",
                    artifact_root / f"round_{last_valid_round}" if artifact_root else None,
                )
                context["terminal_final_review"] = final_record
                if final_review.accepted:
                    context["unresolved_issues"] = []
                    context["stop_reason"] = "final_reviewer_accepted"
                    persist()
                    return BPipelineResult(
                        True, "final_reviewer_accepted", last_valid_round,
                        current_template, last_filled, context,
                        {name: dict(values) for name, values in usage.items()},
                    )
                context["unresolved_issues"] = final_review.diagnosis
                context["stop_reason"] = "final_reviewer_rejected_after_budget_exhausted"
                persist()
                return BPipelineResult(
                    False, "final_reviewer_rejected_after_budget_exhausted", last_valid_round,
                    current_template, last_filled, context,
                    {name: dict(values) for name, values in usage.items()},
                )
            context["stop_reason"] = "max_template_reviewer_revisions"
            persist()
            return BPipelineResult(
                False, "max_template_reviewer_revisions", last_valid_round,
                current_template, last_filled, context,
                {name: dict(values) for name, values in usage.items()},
            )
        if review.decision == "revise" or changed_template:
            revision_count += 1
        current_template = review.template_html
        context["current_template_version"] = round_number
        context["template_revisions_used"] = revision_count
        round_dir = artifact_root / f"round_{round_number}" if artifact_root else None
        if round_dir:
            round_dir.mkdir(parents=True, exist_ok=True)
            round_dir.joinpath("template_reviewer.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")
            round_dir.joinpath("empty_template.html").write_text(current_template, encoding="utf-8")

        failures: list[GateFailure] = []
        filler_attempts: list[dict[str, Any]] = []
        gate_result: HardGateResult | None = None
        contamination = template_contamination(current_template, candidate_text, target_text)
        slot_gaps = template_slot_gaps(current_template, candidate_text)
        if contamination or slot_gaps:
            failures = []
            if contamination:
                failures.append(GateFailure(code="template_contamination", details=contamination))
            if slot_gaps:
                failures.append(GateFailure(code="template_missing_slot", details=slot_gaps))
            gate_result = HardGateResult(passed=False, failures=failures)
        else:
            for filler_attempt in range(1, max_filler_attempts + 1):
                # Owner feedback and slot diagnostics belong to the Template
                # Reviewer; a Filler that sees them starts editing presentation
                # to satisfy them (run 20260909T163618Z round 1).
                fill_context = {
                    **{key: value for key, value in reviewer_context.items()
                       if key not in {"owner_feedback", "slot_contract_violations"}},
                    "filler_attempt": filler_attempt,
                    "filler_gate_failures": [failure.model_dump(mode="json") for failure in failures],
                }
                raw_filled, filler_usage = fill(current_template, fill_context)
                usage["filler"].update(filler_usage)
                if round_dir:
                    round_dir.joinpath(f"filler_raw_attempt_{filler_attempt}.html").write_text(raw_filled, encoding="utf-8")
                presentation_errors = validate_filler_conformance(current_template, raw_filled)
                # The header tag rename (div.header -> header) is deterministic
                # bookkeeping done by normalize_header_element; judge structure
                # on the normalized form so the check does not fire on it.
                presentation_errors += validate_filler_header_structure(
                    current_template, normalize_header_element(raw_filled)
                )
                if presentation_errors:
                    failures = [GateFailure(code="filler_presentation_changed", details=presentation_errors)]
                    gate_result = HardGateResult(passed=False, failures=failures)
                else:
                    last_filled = normalize_candidate(raw_filled, current_template) if normalize_candidate else raw_filled
                    if round_dir:
                        round_dir.joinpath(f"filled_attempt_{filler_attempt}.html").write_text(last_filled, encoding="utf-8")
                    gate_result = gate(round_number, current_template, last_filled)
                    failures = list(gate_result.failures)
                filler_attempts.append({
                    "attempt": filler_attempt,
                    "gate_failures": [failure.model_dump(mode="json") for failure in failures],
                })
                if not failures or any(
                    failure.code == "unstable_render"
                    or failure.code.startswith("layout_render_")
                    or failure.code.startswith("independent_render_")
                    for failure in failures
                ):
                    break
            if not failures:
                last_valid_round = round_number

        hard_gates = gate_result or HardGateResult(passed=not failures, failures=failures)
        if round_dir:
            round_dir.joinpath("hard_gates.json").write_text(hard_gates.model_dump_json(indent=2), encoding="utf-8")
        row = {
            "round": round_number,
            **versions,
            "decision": review.decision,
            "diagnosis": review.diagnosis,
            "changes": review.changes,
            "gate_failures": [failure.model_dump(mode="json") for failure in failures],
            "filler_attempts": filler_attempts,
            "hard_gates": hard_gates.model_dump(mode="json"),
            "template_sha256": _text_sha256(current_template),
            "produced_template_version": round_number,
            "produced_template_sha256": _text_sha256(current_template),
        }
        if round_dir:
            round_dir.joinpath("review_alignment.json").write_text(json.dumps({
                "round": round_number,
                **versions,
                "produced_template_version": round_number,
                "produced_template_sha256": row["produced_template_sha256"],
                "note": (
                    "diagnosis in this round describes reviewed_render_version "
                    "(rendered from reviewed_template_version); files in this round "
                    "directory (empty_template.html and its renders) were produced "
                    "from produced_template_version"
                ),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        context["rounds"].append(row)
        context["unresolved_issues"] = (
            [f"{failure.code}: {failure.details}" for failure in failures]
            if failures else list(review.diagnosis) if review.decision == "revise" else []
        )
        if failures:
            context["failed_approaches"].append({"round": round_number, "changes": review.changes})
        persist()

        if review.decision != "ready_for_audit" or failures:
            continue
        final_review, final_record = run_final_review(
            round_number, round_number, row["produced_template_sha256"], "ready_for_audit", round_dir,
        )
        if final_review.accepted:
            context["unresolved_issues"] = []
            context["stop_reason"] = "final_reviewer_accepted"
            persist()
            return BPipelineResult(
                True, "final_reviewer_accepted", round_number, current_template, last_filled,
                context, {name: dict(values) for name, values in usage.items()},
            )
        if final_reopens or revision_count >= max_revisions:
            context["unresolved_issues"] = final_review.diagnosis
            stop_reason = (
                "final_reviewer_rejected_after_reopen"
                if final_reopens else "final_reviewer_rejected_at_revision_limit"
            )
            context["stop_reason"] = stop_reason
            persist()
            return BPipelineResult(
                False, stop_reason, last_valid_round,
                current_template, last_filled, context,
                {name: dict(values) for name, values in usage.items()},
            )
        final_reopens = 1
        context["final_review_feedback"] = final_review.model_dump(mode="json")
        context["unresolved_issues"] = final_review.diagnosis
        context["failed_approaches"].append({"round": round_number, "changes": review.changes, "final_review": final_review.diagnosis})
        persist()

    context["stop_reason"] = "max_template_reviewer_revisions"
    persist()
    return BPipelineResult(
        False, "max_template_reviewer_revisions", last_valid_round,
        current_template, last_filled, context,
        {name: dict(values) for name, values in usage.items()},
    )


def _seed_template(target: Path) -> tuple[str, Path]:
    candidates: list[tuple[str, Path]] = []
    target_hash = _sha256(target)
    for metadata_path in RUNS.glob("template_cache/*/metadata.json"):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        template_path = metadata_path.with_name("empty_template.html")
        if metadata.get("target_sha256") == target_hash and template_path.exists():
            candidates.append((str(metadata.get("template_prompt_version", "")), template_path))
    if not candidates:
        raise RuntimeError("No existing A-pipeline empty-template evidence for this target")
    path = max(candidates, key=lambda row: row[0])[1]
    return path.read_text(encoding="utf-8"), path


def _template_reviewer_prompt(context: dict[str, Any], summary: dict[str, Any]) -> str:
    return """You are the B-pipeline Template Reviewer. Return one strict JSON object matching the supplied schema.
You own the COMPLETE empty HTML/CSS template and may rewrite all of it. Diagnose the current visual mismatch, then return the complete next empty template.
Use the target evidence and measured format summary for presentation only. Never copy candidate facts or target-sample facts. Keep generic placeholders in every content slot.
Do not use the target page image as a background. Keep flowing print HTML. Do not put data-source-* attributes in the empty template.
The Filler alone owns candidate content and provenance. Deterministic gate failures are authoritative; do not weaken or bypass them.
SLOT CONTRACT: the template must declare a typed landing slot for EVERY header-type content unit in the SOURCE, regardless of what the target header happens to show: data-slot="name" for the name line, data-slot="title" and/or data-slot="tagline" for those source lines when present, data-slot="location" for a location line, and one contact-item per required contact kind with its fa-phone/fa-envelope/fa-github/fa-linkedin icon. A template missing a required landing slot is rejected deterministically (template_missing_slot) before any filling; the Filler can only fill declared slots and must never invent header structure, so a missing slot in the template is YOUR defect to fix.
FIRST OBLIGATION: when slot_contract_violations is non-empty, your template_html MUST add those exact elements INSIDE the header element (e.g. <div data-slot="title">[JOB TITLE]</div>) in this round, before any other change. Geometry or style work that leaves any listed violation unfixed WILL be rejected again; data-slot attributes in the header are structural, so adding them is allowed and expected even when you otherwise keep the template unchanged.
OWNER FEEDBACK (authoritative — the human judge's confirmed findings on a previous render of THIS target; fix every item before any other change and never regress them in later rounds): when owner_feedback is non-empty, each item describes a confirmed visual defect, usually in the header. Fix them structurally in template_html: centering is a template CSS decision (text-align/justify-content on the header and its slots), and the contact row belongs BELOW the name/location lines, not beside the name.
Use decision revise while another template round is needed. Use ready_for_audit only when the current render is visually acceptable for a fresh final audit.
TARGET is the first image. When present, CURRENT CANDIDATE is the second image. Judge the current images; history is context, not proof that an old defect remains.
RENDER FACTS (when reviewed_render_version is not null) gives deterministic page-1 text lines (top/x0/x1/height, grouped by baseline) extracted from the actual current render PDF and from the target PDF, the current header DOM, and the header CSS. Compare baselines and line grouping directly against the target; do not guess margins, font sizes, or element identity.
Every earlier round's diagnosis described that round's reviewed_render_version — an OLDER render. A defect listed in history that is absent from the current RENDER FACTS and current images is already resolved; do not repeat it, and never contradict the current facts.
When no further template change is needed, return decision ready_for_audit and copy current_template_html byte-for-byte into template_html with an empty changes list.
When template_revisions_remaining is 0, this is audit-only: do not change the template. Return ready_for_audit unchanged if acceptable, otherwise revise (the caller will stop at the bound).
JSON SCHEMA:
""" + json.dumps(TemplateReview.model_json_schema(), ensure_ascii=False) + "\nCONTEXT:\n" + json.dumps({
        "format_summary": summary,
        **context,
    }, ensure_ascii=False, sort_keys=True)


def _review_call(
    model: str,
    messages: list[dict[str, Any]],
    schema: type[BaseModel],
    client: Any,
) -> tuple[BaseModel, dict[str, int]]:
    for transport_attempt in range(1, 4):  # 3 outer retries: the visual endpoint flaps (2026-09-09)
        try:
            result, usage = _structured_chat_call(model, messages, schema, client=client, stream=True)
            return result, {**usage, "transport_attempts": transport_attempt}
        except (httpx.TransportError, PipelineCallError) as error:
            if isinstance(error, PipelineCallError) and error.kind != "transport":
                raise
            if transport_attempt == 3:
                raise
            time.sleep(30 * transport_attempt)  # the visual endpoint flaps; ride out short outages
    raise AssertionError("unreachable")


def _filler_call(prompt: str, model: str) -> tuple[str, dict[str, int]]:
    for transport_attempt in range(1, 4):  # 3 outer retries: endpoints flap (2026-09-09)
        try:
            document, usage = _html_call(prompt, model)
            return document, {**usage, "transport_attempts": transport_attempt}
        except PipelineCallError as error:
            if error.kind != "transport" or transport_attempt == 3:
                raise
            time.sleep(30 * transport_attempt)
    raise AssertionError("unreachable")


def run(target: Path = DEFAULT_TARGET, source: Path = DEFAULT_SOURCE, output: Path | None = None, *, owner_feedback: list[str] | None = None) -> Path:
    load_dotenv(ROOT / ".env")
    if not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")):
        raise RuntimeError("Missing live configuration: DEEPSEEK_API_KEY or OPENAI_API_KEY")
    capable_model = os.getenv("B_PIPELINE_MODEL") or os.getenv("A_PIPELINE_MODEL") or "deepseek-v4-flash-vision-exp"
    reviewer_override = visual_client()
    reviewer_client, reviewer_model = reviewer_override or (None, capable_model)
    run_dir = output or RUNS / datetime.now(UTC).strftime("b_pipeline_D_to_E_%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    shutil.copy2(target, run_dir / "target.pdf")

    evidence, raw = _analyze_target(target, run_dir, use_persistent_cache=True)
    summary = build_format_summary(evidence, raw, target)
    _require_geometry(summary)
    run_dir.joinpath("format_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    chrome_environment = _pinned_chrome_environment(summary)
    run_dir.joinpath("render_environment.json").write_text(json.dumps(chrome_environment, indent=2), encoding="utf-8")
    target_pages = _render_pages(run_dir / "target.pdf", run_dir, "target")
    target_board = _visual_evidence_board(run_dir / "target.pdf", target_pages, summary, run_dir, "target")
    initial_template, seed_path = _seed_template(target)
    initial_template = normalize_header_element(initial_template)
    run_dir.joinpath("seed_template.html").write_text(initial_template, encoding="utf-8")
    source_text, target_text = read_pdf_text(source), read_pdf_text(target)
    run_dir.joinpath("source_text.txt").write_text(source_text, encoding="utf-8")
    artifacts: dict[int, dict[str, Any]] = {}
    previous_fill = ""
    rendered_rounds: set[int] = set()

    def review_template(context: dict[str, Any]) -> tuple[TemplateReview, dict[str, int]]:
        last_round = context.get("last_round_number")
        images = [target_board]
        prompt_context = dict(context)
        if last_round and artifacts.get(last_round, {}).get("board"):
            images.append(artifacts[last_round]["board"])
            prompt_context["render_facts"] = _render_diagnostic(
                run_dir / "target.pdf",
                artifacts[last_round]["pdf"],
                artifacts[last_round]["rendered_html"].read_text(encoding="utf-8"),
                run_dir.joinpath(f"round_{last_round}", "empty_template.html").read_text(encoding="utf-8"),
            )
        result, usage = _review_call(
            reviewer_model,
            [
                {"role": "system", "content": "You are a visual template reviewer. Return JSON only; candidate and target facts are forbidden in template_html."},
                {"role": "user", "content": _chat_content(_template_reviewer_prompt(context, summary), images)},
            ],
            TemplateReview,
            reviewer_client,
        )
        assert isinstance(result, TemplateReview)
        return result, usage

    def fill(template: str, loop_context: dict[str, Any]) -> tuple[str, dict[str, int]]:
        nonlocal previous_fill
        prior_failures = loop_context.get("filler_gate_failures") or (
            loop_context["rounds"][-1]["gate_failures"] if loop_context["rounds"] else []
        )
        from tests.experiments.a_pipeline import _filler_prompt

        filler_context = build_filler_context(
            source_text=source_text,
            template_html=template,
            champion_filled_html=previous_fill,
            issue=None,
            action="initial_fill",
            resolved_measurements=[],
            target_evidence=_artifact_ref(target_board) or {},
            champion_evidence=None,
            protected_rubrics=[],
            gate_failures=prior_failures,
            same_issue_outcomes=[],
            repair_outcomes=[],
        )
        # Owner finding 2026-09-09 (run 20260909T173255Z): given content
        # placement failures, the Filler burns every attempt hiding content
        # with style="display:none", which the presentation check correctly
        # rejects — state the lever ban explicitly.
        filler_context["hard_constraints"] = [
            "NEVER add or change a style attribute or <style> content; the presentation "
            "check rejects any style change and the attempt is wasted.",
            "Fix content failures only by moving or deleting content nodes.",
        ]
        previous_fill, usage = _filler_call(_filler_prompt(filler_context), capable_model)
        return previous_fill, usage

    def normalize_candidate(candidate: str, template: str) -> str:
        candidate = normalize_header_element(candidate)
        candidate = normalize_section_headings(candidate, template, source_text)
        candidate, _ = deduplicate_candidate_html(candidate, source_text)
        from tests.experiments.a_pipeline import _normalize_provenance_annotations, _normalize_section_order

        candidate = _normalize_provenance_annotations(candidate, source_text)
        return _normalize_section_order(candidate, template, source_text)

    def gate(round_number: int, template: str, candidate: str) -> HardGateResult:
        round_dir = run_dir / f"round_{round_number}"
        fixed_template_text, _ = _html_text(re.sub(r"\[[^]]+\]", "", template))
        analysis = analyze_candidate_provenance(candidate, source_text, allowed_text=fixed_template_text)
        errors, ownership = validate_candidate_html(candidate, source_text, analysis=analysis)
        new_images = sorted(_image_sources(candidate) - _image_sources(template))
        if new_images:
            errors.append("Filler introduced image assets not present in the empty template")
        failures = [GateFailure(code=finding["code"], details=finding) for finding in analysis["findings"]]
        if errors or new_images:
            failures.append(GateFailure(code="invalid_candidate_html", details={"errors": errors, "new_image_assets": new_images}))
        if failures:
            return HardGateResult(passed=False, failures=failures)
        round_dir.joinpath("source_provenance.json").write_text(json.dumps({
            **provenance_report(ownership, source_text), "warnings": analysis["warnings"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        rendered_html = _inject_local_fonts(_ensure_icon_font_family(_inject_icon_fonts(candidate)))
        html_path = round_dir / "rendered.html"
        html_path.write_text(rendered_html, encoding="utf-8")
        first_pdf = _export_pinned_html_to_pdf(html_path, round_dir / "render_1.pdf", chrome_environment)
        second_pdf = _export_pinned_html_to_pdf(html_path, round_dir / "render_2.pdf", chrome_environment)
        first_pages = _render_pages(first_pdf, round_dir, "render_1")
        second_pages = _render_pages(second_pdf, round_dir, "render_2")
        board = _visual_evidence_board(first_pdf, first_pages, summary, round_dir, "render_1")
        _visual_evidence_board(second_pdf, second_pages, summary, round_dir, "render_2")
        result = run_hard_gates(
            source_text, template, rendered_html, first_pdf, second_pdf,
            first_pages, second_pages, target, summary, allow_slot_splitting=True,
        )
        artifacts[round_number] = {
            "pdf": first_pdf, "pages": first_pages, "board": board,
            "rendered_html": html_path, "gates": result,
        }
        rendered_rounds.add(round_number)
        return result

    def review_final(context: dict[str, Any]) -> tuple[FinalReview, dict[str, int]]:
        round_number = int(context["round"])
        prompt = """You are a fresh, read-only final visual reviewer. Compare TARGET with CANDIDATE across all pages.
The deterministic content and provenance gates passed and are authoritative. Judge only whether the candidate faithfully follows the target's typography, spacing, alignment, icons, section order, entry structure, rules, and page behavior.
You are auditing the EXACT final render recorded in context (reviewed_render_version); it is the latest render that passed the deterministic gates. You cannot edit or suggest code. Return JSON matching this schema:
""" + json.dumps(FinalReview.model_json_schema()) + "\nCONTEXT:\n" + json.dumps(context, sort_keys=True)
        result, usage = _review_call(
            reviewer_model,
            [
                {"role": "system", "content": "You are an independent read-only acceptance reviewer. Return JSON only."},
                {"role": "user", "content": _chat_content(prompt, [target_board, artifacts[round_number]["board"]])},
            ],
            FinalReview,
            reviewer_client,
        )
        assert isinstance(result, FinalReview)
        return result, usage

    try:
        result = run_reviewer_loop(
            initial_template, source_text, target_text, review_template, fill, gate, review_final,
            normalize_candidate=normalize_candidate, artifact_root=run_dir,
            rendered_rounds=rendered_rounds, owner_feedback=owner_feedback,
        )
        run_dir.joinpath("usage.json").write_text(json.dumps(result.usage, indent=2), encoding="utf-8")
        run_dir.joinpath("run_metadata.json").write_text(json.dumps({
            "experiment": "b_pipeline",
            "source": str(source),
            "target": str(target),
            "seed_template": str(seed_path),
            "adobe_calls": 0,
            "owner_feedback": owner_feedback or [],
            "models": {"template_reviewer": reviewer_model, "filler": capable_model, "final_reviewer": reviewer_model},
            "accepted": result.accepted,
            "stop_reason": result.stop_reason,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }, indent=2), encoding="utf-8")
        if result.round_number is not None and result.round_number in artifacts:
            champion = artifacts[result.round_number]
            shutil.copy2(run_dir / f"round_{result.round_number}" / "empty_template.html", run_dir / "empty_template.html")
            shutil.copy2(champion["rendered_html"], run_dir / "filled.html")
            shutil.copy2(champion["pdf"], run_dir / "generated.pdf")
            generated_pages = _render_pages(run_dir / "generated.pdf", run_dir, "generated")
            for index, generated_page in enumerate(generated_pages):
                target_page = target_pages[min(index, len(target_pages) - 1)]
                _side_by_side(target_page, generated_page, run_dir / f"side_by_side_page_{index + 1:03d}.png")
            _side_by_side(target_pages[0], generated_pages[0], run_dir / "side_by_side.png")
            missing = _missing_source_tokens(source_text, read_pdf_text(run_dir / "generated.pdf"))
            run_dir.joinpath("content_verification.json").write_text(json.dumps({"passed": not missing, "missing_tokens": missing}, indent=2), encoding="utf-8")
        run_dir.joinpath("RUN_README.md").write_text(
            f"# B-pipeline D→E experiment\n\n"
            f"- Final Reviewer: **{'ACCEPTED' if result.accepted else 'REJECTED'}**\n"
            f"- Stop reason: `{result.stop_reason}`\n"
            f"- Adobe evidence: reused existing cache; no Adobe call\n"
            f"- Icon root cause (verified offline, tmp/icon_repro): template icon CSS convention (.fa/.fas/.far/.fab) vs Filler fa-brands/fa-solid elements — icon font-family never applied; fixed by one deterministic family rule after injection (_ensure_icon_font_family).\n"
            f"- Owner review: inspect [target](target.pdf), [generated](generated.pdf), and [side-by-side](side_by_side.png).\n"
            f"- Automated acceptance does not decide owner visual approval.\n",
            encoding="utf-8",
        )
        if not result.accepted:
            raise RuntimeError(f"B-pipeline D-to-E did not pass final review: {result.stop_reason}")
        return run_dir
    except Exception:
        run_dir.joinpath("run_failed.txt").write_text("See context.json and round artifacts.\n", encoding="utf-8")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="required acknowledgement for real model calls")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--feedback", action="append", default=[], help="owner feedback item; repeatable")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required because this experiment makes live model calls")
    print(run(args.target.resolve(), args.source.resolve(), args.output.resolve() if args.output else None, owner_feedback=args.feedback))


if __name__ == "__main__":
    main()
