"""Stage 5 deterministic target and rendered-PDF geometry measurements."""
from __future__ import annotations
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from tests.experiments.pipeline.shared import DefectFinding, DualSourcePod, MeasurementRequest, MeasurementResult, TargetStructureDraft, _page_pt_size, _pdf_line_rows
from tests.experiments.resume_layout.scaffold import _pdf_lines_and_marks
from tests.experiments.resume_layout.runtime import RunBudget, RunTrace
from tests.experiments.pipeline.measurement_loop import E2_IMPROVEMENT_TOLERANCE_PT, MeasureController

def _header_vertical_rule_evidence(
    pdf: Path, page_number: int, *, name_text: str | None = None
) -> dict[str, Any] | None:
    """Measure header rules against the exact candidate-name row when known.

    Target PDFs have no candidate lineage, so their bounded fallback is the
    single tallest header line. This stays one metric, not a visual framework.
    """
    import pdfplumber

    page_width, page_height = _page_pt_size(pdf, page_number)
    lines = [
        line for line in _pdf_lines_and_marks(pdf)[0]
        if line["page"] == page_number and line["top"] < page_height * 0.25
    ]
    if not lines:
        return None
    name_lines: list[dict[str, Any]] = []
    exact_name_match = False
    if name_text:
        expected = re.sub(r"\s+", " ", name_text).strip().casefold()
        for start in range(len(lines)):
            selected: list[dict[str, Any]] = []
            for line in lines[start:start + 3]:
                selected.append(line)
                observed = re.sub(
                    r"\s+", " ", " ".join(item["text"] for item in selected)
                ).strip().casefold()
                if observed == expected:
                    name_lines = selected
                    exact_name_match = True
                    break
                if not expected.startswith(observed + " "):
                    break
            if name_lines:
                break
    if not name_lines:
        max_height = max(float(line["bottom"]) - float(line["top"]) for line in lines)
        name_lines = [
            line for line in lines
            if math.isclose(
                float(line["bottom"]) - float(line["top"]),
                max_height,
                abs_tol=0.25,
            )
        ]
    name = {
        "x0": min(float(line["x0"]) for line in name_lines),
        "x1": max(float(line["x1"]) for line in name_lines),
        "top": min(float(line["top"]) for line in name_lines),
        "bottom": max(float(line["bottom"]) for line in name_lines),
    }
    header_top = max(0.0, name["top"] - 80.0)
    header_bottom = min(page_height, name["bottom"] + 80.0)
    rules: list[dict[str, Any]] = []
    with pdfplumber.open(pdf) as document:
        page = document.pages[page_number - 1]
        objects = [*page.rects, *page.curves, *(getattr(page, "lines", None) or [])]
        seen: set[tuple[float, float, float]] = set()
        for mark in objects:
            x0, x1 = float(mark["x0"]), float(mark["x1"])
            top, bottom = float(mark["top"]), float(mark["bottom"])
            width, height = abs(x1 - x0), abs(bottom - top)
            center_x = (x0 + x1) / 2
            if width > 4.0 or height < 8.0 or bottom < header_top or top > header_bottom:
                continue
            horizontal_gap = max(name["x0"] - x1, x0 - name["x1"], 0.0)
            if horizontal_gap > 80.0 or center_x < 12.0 or center_x > page_width - 12.0:
                continue
            key = (round(center_x, 2), round(top, 2), round(bottom, 2))
            if key in seen:
                continue
            seen.add(key)
            dx = horizontal_gap
            dy = max(name["top"] - bottom, top - name["bottom"], 0.0)
            overlap = max(0.0, min(bottom, name["bottom"]) - max(top, name["top"]))
            rules.append({
                "x": round(center_x, 3),
                "top": round(top, 3),
                "bottom": round(bottom, 3),
                "height": round(height, 3),
                "relative_x_to_name_left": round(center_x - name["x0"], 3),
                "relative_top_to_name_top": round(top - name["top"], 3),
                "relative_bottom_to_name_top": round(bottom - name["top"], 3),
                "nearest_distance_to_name_bbox": round(math.hypot(dx, dy), 3),
                "name_vertical_overlap_pt": round(overlap, 3),
            })
    rules.sort(key=lambda rule: (rule["relative_top_to_name_top"], rule["relative_x_to_name_left"]))
    return {
        "page": page_number,
        "name_anchor_source": (
            "candidate_name_text" if exact_name_match else "tallest_header_line"
        ),
        "name_region": {key: round(value, 3) for key, value in name.items()},
        "vertical_rule_count": len(rules),
        "vertical_rules": rules,
    }


def _header_vertical_rule_target_context(pdf: Path) -> dict[str, Any] | None:
    """Small Builder context for the already-supported header-rule metric."""
    evidence = _header_vertical_rule_evidence(pdf, 1)
    if evidence is None:
        return None
    rules = evidence["vertical_rules"]
    return {
        "scope": "header_name",
        "target_final_pdf_evidence": evidence,
        "preserve_separate_name_gap": (
            len(rules) == 2
            and all(rule["name_vertical_overlap_pt"] == 0 for rule in rules)
        ),
        "forbidden_interpretation": "continuous_or_page_spanning_vertical_rail",
    }


def _target_body_text_color_context(pdf: Path) -> dict[str, Any] | None:
    """Measured dominant text fill by visible-character volume."""
    import pdfplumber

    colors: Counter[tuple[float, float, float]] = Counter()
    with pdfplumber.open(pdf) as document:
        for page in document.pages:
            for char in page.chars:
                color = char.get("non_stroking_color")
                if not str(char.get("text") or "").strip():
                    continue
                channels = (color,) if isinstance(color, (int, float)) else color
                if not isinstance(channels, (list, tuple)):
                    continue
                if len(channels) == 1:  # PDF DeviceGray, including ordinary black text
                    rgb = (float(channels[0]),) * 3
                elif len(channels) == 3:
                    rgb = tuple(float(channel) for channel in channels)
                elif len(channels) == 4:  # PDF DeviceCMYK
                    cyan, magenta, yellow, black = map(float, channels)
                    rgb = tuple((1 - channel) * (1 - black) for channel in (cyan, magenta, yellow))
                else:
                    continue
                rgb = tuple(round(channel, 4) for channel in rgb)
                if all(0.0 <= channel <= 1.0 for channel in rgb):
                    colors[rgb] += 1
    if not colors:
        return None
    rgb, count = colors.most_common(1)[0]
    return {
        "scope": "ordinary_body_copy",
        "source": "target_final_pdf_character_fill",
        "selection": "dominant_non_whitespace_character_color",
        "dominant_color_rgb": list(rgb),
        "dominant_color_hex": "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb),
        "supporting_character_count": count,
    }


def _measure_header_vertical_rule_geometry(
    request: MeasurementRequest,
    *,
    target_pdf: Path,
    current_pdf: Path,
    current_name_text: str | None = None,
) -> MeasurementResult:
    """One transparent scalar error over the complete detected rule sets.

    Count and name-overlap penalties prevent an extra retained rule from being
    ignored; raw geometry remains in evidence for owner inspection.
    """
    intent = (request.intent or "").casefold()
    if not (
        any(word in intent for word in ("header", "name"))
        and "vertical" in intent
        and any(word in intent for word in ("rule", "line", "bar"))
    ):
        return MeasurementResult(
            request_id=request.request_id,
            status="not_measurable",
            reason="header_vertical_rule_geometry requires an explicit header/name vertical-rule intent",
            method="header_vertical_rule_geometry/1",
        )
    target = _header_vertical_rule_evidence(target_pdf, request.page)
    current = _header_vertical_rule_evidence(
        current_pdf, request.page, name_text=current_name_text
    )
    if target is None or current is None:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            reason="the name/header region could not be located in one of the final PDFs",
            method="header_vertical_rule_geometry/1",
        )
    target_rules = target["vertical_rules"]
    current_rules = current["vertical_rules"]
    if not target_rules or not current_rules:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            target_evidence=[target],
            current_evidence=[current],
            reason="a header vertical-rule set is empty; deletion is not automatic improvement",
            method="header_vertical_rule_geometry/1",
        )
    paired_error = sum(
        abs(current_rule["relative_x_to_name_left"] - target_rule["relative_x_to_name_left"])
        + abs(current_rule["relative_top_to_name_top"] - target_rule["relative_top_to_name_top"])
        + abs(current_rule["relative_bottom_to_name_top"] - target_rule["relative_bottom_to_name_top"])
        for target_rule, current_rule in zip(target_rules, current_rules)
    )
    target_overlap = sum(rule["name_vertical_overlap_pt"] > 0 for rule in target_rules)
    current_overlap = sum(rule["name_vertical_overlap_pt"] > 0 for rule in current_rules)
    error = (
        100.0 * abs(len(current_rules) - len(target_rules))
        + 100.0 * abs(current_overlap - target_overlap)
        + paired_error
    )
    formula = (
        "error_pt = 100*abs(rule_count-target_rule_count) + "
        "100*abs(name_overlap_count-target_name_overlap_count) + "
        "sum paired abs(relative_x, relative_top, relative_bottom) deltas; "
        "all detected header vertical rules are included"
    )
    target["error_calculation"] = formula
    current["error_calculation"] = formula
    return MeasurementResult(
        request_id=request.request_id,
        status="confirmed",
        target_value_pt=0.0,
        current_value_pt=round(error, 3),
        delta_pt=round(error, 3),
        target_evidence=[target],
        current_evidence=[current],
        method="header_vertical_rule_geometry/1",
        warnings=["coordinates are page-local pt; geometry is relative to the detected name region"],
    )


def _measurement_improvement(
    before: MeasurementResult, after: MeasurementResult
) -> tuple[bool, str]:
    if after.status != "confirmed":
        return False, f"identical re-measurement was {after.status}"
    if any(value is None for value in (
        before.current_value_pt, before.target_value_pt,
        after.current_value_pt, after.target_value_pt,
    )):
        return False, "identical re-measurement left scalar evidence missing"
    if before.method == after.method == "header_vertical_rule_geometry/1":
        before_rules = (before.current_evidence[0].get("vertical_rules", []) if before.current_evidence else [])
        after_rules = (after.current_evidence[0].get("vertical_rules", []) if after.current_evidence else [])
        if len(after_rules) > len(before_rules):
            retained_crossing_rule = any(
                old.get("name_vertical_overlap_pt", 0) > 0
                and any(
                    new.get("name_vertical_overlap_pt", 0) > 0
                    and abs(new.get("height", 0) - old.get("height", 0)) <= 2.0
                    for new in after_rules
                )
                for old in before_rules
            )
            if retained_crossing_rule:
                return False, "added a vertical rule while retaining the original long name-crossing rule"
    improved = abs(after.current_value_pt - after.target_value_pt) < abs(
        before.current_value_pt - before.target_value_pt
    )
    return improved, (
        "identical deterministic error decreased" if improved
        else "identical deterministic error did not decrease"
    )



def _bind_role_gap_anchors(
    request: MeasurementRequest,
    *,
    target_pdf: Path,
    current_pdf: Path,
    region_span: tuple[float, float] | None,
    render_leaf_texts: list[str],
) -> MeasurementRequest | MeasurementResult:
    """Phase 0 measurement binding: resolve a SEMANTIC measurement intent
    into actual final-PDF objects (exact line-prefix anchors on BOTH sides).
    The render side binds against the render's own candidate entry-head
    texts; the target side binds against the target's measured content rows
    within the region span. A Reviewer OCR mismatch never becomes an
    automatic measurement_failure: an unresolvable intent returns
    `evidence_missing` for re-verification, with the binding method recorded."""
    intent = (request.intent or "").casefold()
    if not (
        any(word in intent for word in ("gap", "distance", "spacing"))
        and any(word in intent for word in ("entry", "role", "job"))
        and not any(word in intent for word in (
            "contact", "typography", "font", "vertical rule",
        ))
    ):
        return MeasurementResult(
            request_id=request.request_id,
            status="not_measurable",
            reason="the current deterministic metric measures entry-head spacing only",
            method="role_gap_binding/1",
            warnings=["a different visual dimension requires its own deterministic metric"],
        )
    render_rows = _pdf_line_rows(current_pdf, request.page)

    def _norm_row(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()

    prefixes = [
        _norm_row(text)[:12]
        for text in render_leaf_texts
        if text and len(text.strip()) >= 6
    ]
    render_anchors: list[str] = []
    for row in render_rows:
        row_text = _norm_row(row["text"])
        for prefix in prefixes:
            if row_text.startswith(prefix) and prefix not in render_anchors:
                render_anchors.append(prefix)
                break
        if len(render_anchors) == 2:
            break
    if len(render_anchors) < 2:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            reason=(
                "the semantic intent could not be bound to two render entry-head "
                "rows in the final PDF (binding, not OCR, decides)"
            ),
            method="role_gap_binding/1",
            warnings=["binding resolves the Reviewer's semantic intent against actual PDF rows"],
        )
    target_rows = _pdf_line_rows(target_pdf, request.page)
    if region_span is not None:
        span_top, span_bottom = region_span
        target_rows = [
            row for row in target_rows if span_top < row["top"] < span_bottom
        ]
    # ponytail: target entry heads = the first two non-bullet rows below the
    # section rule (dates/meta rows carry digits; bullets carry the marker);
    # a dedicated head classifier is the upgrade if this misbinds.
    head_rows = [
        row for row in target_rows
        if not row["text"].startswith("\u2022")
        and not row["text"].startswith("\u2022")
        and any(character.isalpha() for character in row["text"])
    ]
    if len(head_rows) < 2:
        return MeasurementResult(
            request_id=request.request_id,
            status="evidence_missing",
            reason="the region's target content rows could not yield two head anchors",
            method="role_gap_binding/1",
            warnings=["binding resolves the Reviewer's semantic intent against actual PDF rows"],
        )
    return request.model_copy(
        update={
            "from_text": _norm_row(head_rows[0]["text"])[:16],
            "to_text": _norm_row(head_rows[1]["text"])[:16],
            "render_from_text": render_anchors[0],
            "render_to_text": render_anchors[1],
        }
    )




def _execute_template_measurement(
    finding: DefectFinding,
    pdf: Path,
    request_id: str,
    *,
    render_version: str = "",
    region_spans: dict[str, tuple[int, float, float]],
    trace: RunTrace,
    lane_budget: RunBudget,
    target_pdf: Path,
    candidate_name_text: str | None,
    candidate: Any,
    pod: DualSourcePod,
) -> tuple[MeasurementResult, MeasurementRequest]:
    span = region_spans.get(finding.region)
    span_tuple = (span[1], span[2]) if span and span[0] == finding.page else None
    trace.add(
        agent="measure_controller", phase="measure", action="binding",
        input={"render_version": render_version, "finding": finding.finding_id},
    )
    request = finding.requested_measurement.model_copy(update={"request_id": request_id})
    if request.metric == "header_vertical_rule_geometry":
        finding_text = (
            f"{finding.region} {finding.suspected_dimension} {finding.observation} "
            f"{request.intent or ''}"
        ).casefold()
        if not (
            any(word in finding_text for word in ("header", "name"))
            and "vertical" in finding_text
            and any(word in finding_text for word in ("rule", "line", "bar"))
        ):
            result = MeasurementResult(
                request_id=request_id,
                status="not_measurable",
                reason="metric may bind only to an explicit header vertical-rule finding",
                method="header_vertical_rule_geometry/1",
            )
        else:
            lane_budget.spend_tool("measure_header_vertical_rule_geometry")
            result = _measure_header_vertical_rule_geometry(
                request,
                target_pdf=target_pdf,
                current_pdf=pdf,
                current_name_text=candidate_name_text,
            )
        trace.add(
            agent="measure_controller", phase="measure", action="measurement",
            tool="measure_header_vertical_rule_geometry",
            output=result.model_dump(mode="json"), persist_output=True,
        )
        return result, request
    bound = _bind_role_gap_anchors(
        request,
        target_pdf=target_pdf,
        current_pdf=pdf,
        region_span=span_tuple,
        render_leaf_texts=[
            leaf.text or "" for leaf in candidate.leaves
            if leaf.kind in {"work_entry", "education_entry"} and leaf.text
        ][:4],
    )
    if isinstance(bound, MeasurementResult):
        trace.add(
            agent="measure_controller", phase="measure", action="binding_unresolved",
            note=bound.reason, persist_output=True,
        )
        return bound, finding.requested_measurement
    # MeasureController.execute is the SINGLE tool-budget consumer for
    # a deterministic measurement (one compare_pdf_geometry per call,
    # one trace record). Callers never spend or re-trace it.
    result = MeasureController(pod, lane_budget, trace).execute(bound, current_pdf=pdf)
    return result, bound


def _region_spans_from_draft(draft: TargetStructureDraft, page_height: float) -> dict[str, tuple[int, float, float]]:
    """node_id -> (page, top, bottom) from the draft's measured section
    boundary claims (each cites its sidebar label row + rule bbox)."""
    claims = []
    for claim in draft.structure:
        if claim.relation != "section_boundary":
            continue
        rule_top = None
        page_number = None
        for ref in claim.evidence:
            if ref.bbox_pt and len(ref.bbox_pt) == 4 and ref.page_number is not None:
                page_number = ref.page_number
                if ref.bbox_pt[1] > 0 or ref.bbox_pt[3] > ref.bbox_pt[1]:
                    rule_top = float(ref.bbox_pt[1])
        if page_number is not None:
            claims.append((page_number, rule_top, claim.child))
    spans: dict[str, tuple[int, float, float]] = {}
    by_page: dict[int, list[tuple[float, str]]] = {}
    for page_number, rule_top, child in claims:
        by_page.setdefault(page_number, []).append((rule_top, child))
    for page_number, rows in by_page.items():
        rows.sort()
        for index, (rule_top, child) in enumerate(rows):
            bottom = rows[index + 1][0] if index + 1 < len(rows) else page_height
            spans[child] = (page_number, rule_top + 1.0, bottom)
    return spans


def _accepted_regions_hold(
    candidate_pdf: Path,
    *,
    candidate_version_id: str,
    resolved_measurements: dict[str, tuple[MeasurementRequest, MeasurementResult]],
    findings_by_id: dict[str, DefectFinding],
    execute_measurement: Callable[..., tuple[MeasurementResult, MeasurementRequest]],
) -> tuple[bool, list[dict[str, Any]]]:
    """FAIL CLOSED accepted-region recheck: an accepted region is held
    ONLY when the identical re-measurement is CONFIRMED on the
    candidate PDF with all measured values present and the error did
    not grow beyond the prior error + tolerance. Any evidence_missing
    / unmeasurable / vanished anchor / missing value means the
    candidate CANNOT be verified to hold the region -> not held."""
    rechecks: list[dict[str, Any]] = []
    for finding_id, (request, prior) in resolved_measurements.items():
        finding = findings_by_id[finding_id]
        repeat, _ = execute_measurement(
            finding.model_copy(update={"requested_measurement": request}),
            candidate_pdf,
            request.request_id,
            render_version=candidate_version_id,
        )
        values_present = (
            repeat.current_value_pt is not None
            and repeat.target_value_pt is not None
            and prior.current_value_pt is not None
            and prior.target_value_pt is not None
        )
        held = (
            repeat.status == "confirmed"
            and values_present
            and abs(repeat.current_value_pt - repeat.target_value_pt)
            <= abs(prior.current_value_pt - prior.target_value_pt) + E2_IMPROVEMENT_TOLERANCE_PT
        )
        reason = None
        if not held:
            if repeat.status != "confirmed":
                reason = f"re-measurement not confirmed on the candidate PDF (status={repeat.status})"
            elif not values_present:
                reason = "re-measurement left measured values missing (vanished anchor / evidence gap)"
            else:
                reason = "accepted region error grew beyond the prior error + tolerance"
        rechecks.append(
            {
                "finding": finding_id,
                "held": held,
                "status": repeat.status,
                **({"reason": reason} if reason else {}),
            }
        )
        if not held:
            return False, rechecks
    return True, rechecks
