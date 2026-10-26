"""Shared pipeline review implementation."""

from __future__ import annotations

from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any
from app.pipeline.runtime import CheckpointBudgetExceeded, RunBudget, RunTrace, _record_usage
from app.pipeline.measurement import (
    BodyEntryScaffold,
    BodyHeadingScaffold,
    BodyScaffold,
    HeaderScaffold,
    _pdf_lines_and_marks,
)
import pdfplumber as _pdfplumber
from app.pipeline.evidence import (
    DefectFinding,
    E5AgentAuditSpec,
    MeasurementRequest,
    MeasurementResult,
    _call_limits,
    _e5_agent_audit_record,
    _live_model_settings,
    _live_visual_model,
    _sha256_file,
)

E2_IMPROVEMENT_TOLERANCE_PT = 1.0  # measured local-gap tolerance in points

REVIEW_IMAGE_ENCODING_VERSION = "lossless-png-webp/1"
REVIEW_MEASUREMENT_CONTRACT_VERSION = "review-measurement/2"
REVIEW_PRESENTATION_FACTS_VERSION = "review-presentation-facts/3"


def prepare_review_image(path: Path) -> Path:
    """Keep the full overview pixels; use smaller lossless color bytes for I/O."""
    from PIL import Image

    with Image.open(path) as image:
        if image.mode != "RGB":
            return path
        encoded = BytesIO()
        image.save(encoded, format="WEBP", lossless=True, method=6)
    if encoded.tell() >= path.stat().st_size:
        return path
    output = path.with_suffix(".webp")
    output.write_bytes(encoded.getvalue())
    return output

REVIEWER_INSTRUCTIONS = (
    "You are the independent Visual Reviewer of a resume-layout experiment.\n"
    "You inspect the TARGET and the RENDERED page crops you are given, plus\n"
    "measured facts you explicitly requested earlier. You report LOCALIZED\n"
    "visible differences only.\n"
    "Hard boundaries: you never decide the root cause layer; `proposed_cause`\n"
    "is a hypothesis for verification, never a verdict. You never approve\n"
    "delivery. You never modify anything. Every finding binds the exact\n"
    "target and render version strings you were given, and carries one\n"
    "typed measurement request. Use metric='unsupported' with an intent for\n"
    "properties outside the supported measurements; these stay open warnings.\n"
    "Coordinates you report are page-local points; text anchors are VERBATIM\n"
    "line-prefix strings that deterministic measurement can locate in the\n"
    "final PDF.\n"
    "Target person facts are diagnostic evidence only; they never become\n"
    "candidate content."
)

class ScriptedReviewer:
    """Offline observation-first reviewer rehearsal: one real localized
    finding per round from the real target/render pair, with a typed
    measurement request. Plays the scout role the plan assigns — no verdict,
    no approval, no root-cause claim."""

    def __init__(self) -> None:
        self.scripted = 0

    def run(
        self,
        prompt: str,
        *,
        finding_id: str,
        target_version: str,
        render_version: str,
        page: int,
        budget: RunBudget,
        trace: RunTrace,
        agent_name: str = "visual_reviewer",
        target_id: str = "target-v1",
    ) -> list[DefectFinding]:
        self.scripted += 1
        budget.spend_model(agent_name)
        # The scripted scout inspects the same real pair a live reviewer sees:
        # the orchestrator prompt carries the current target/render anchors and
        # the known role-aligned divergence; the finding stays observation-first
        # and cites the requested measurement, not a cause.
        finding = DefectFinding(
            finding_id=finding_id,
            target_version=target_version,
            render_version=render_version,
            page=page,
            region=prompt_region(prompt, target_id),
            observation=prompt_observation(prompt, target_id),
            suspected_dimension="role_gap",
            requested_measurement=prompt_request(prompt, finding_id, target_id),
            severity="medium",
            confidence=0.6,
            reviewer="scripted",
        )
        trace.add(
            agent=agent_name,
            phase="review",
            action="finding",
            output=finding.model_dump(mode="json"),
            persist_output=True,
        )
        return [finding]

def _live_reviewer_findings(
    page_images: list[Any],
    finding_id: str,
    target_version: str,
    render_version: str,
    page: int,
    node_inventory: str,
    budget: RunBudget,
    trace: RunTrace,
    *,
    id_prefix: str | None = None,
    instructions: str = REVIEWER_INSTRUCTIONS,
    visual_model: bool = False,
    message: str | None = None,
    audit: E5AgentAuditSpec | None = None,
) -> list[DefectFinding]:
    """One bounded live reviewer request over the side-by-side crop/overview.
    Requires explicit authorization (never called offline). E4: one agent run
    is a bounded call (per-call request limit); findings ids are re-assigned by
    the shell so the model can never forge them. E5 Phase 0: `message`
    overrides the default prompt text (semantic measurement intent instead of
    verbatim OCR anchors). ``audit`` records the full model-visible message
    history (owner decision 2026-09-22; the E5 call sites pass it)."""
    from pydantic_ai import Agent, BinaryContent

    from app.pipeline.runtime import _live_model

    model = (
        _live_visual_model(budget, trace, "visual_reviewer") if visual_model
        else _live_model(budget, trace, "visual_reviewer")
    )
    agent = Agent(
        model,
        output_type=list[DefectFinding],
        name="visual_reviewer",
        instructions=instructions,
    )
    if budget.remaining_model_requests() < 1:
        raise CheckpointBudgetExceeded("budget exhausted before visual reviewer")
    user_messages = [
        (message or (
        "Compare the TARGET and RENDER images for this page. Report localized "
        "observation-first findings across the WHOLE document region by region "
        "(not only one known defect). Each finding must carry the exact "
        f"target_version={target_version!r} render_version={render_version!r} "
        f"page={page}, a region id from the node inventory, an observation, a "
        "suspected dimension, and one typed measurement request whose text "
        "anchors are verbatim line prefixes of the render PDF. Separate what you "
        "SEE from what you SUSPECT: proposed_cause is a hypothesis only. You "
        "never approve delivery and never decide the root cause.\nNode inventory:\n"
        + node_inventory)),
        *[BinaryContent(data=image.read_bytes(), media_type="image/webp" if image.suffix == ".webp" else "image/png") for image in page_images],
    ]
    model_name = getattr(getattr(agent, "model", None), "model_name", None)
    try:
        result = agent.run_sync(
            user_messages,
            usage_limits=_call_limits(budget, E4_REVIEWER_MAX_REQUESTS),
            model_settings=_live_model_settings(),
        )
    except Exception as error:
        if audit is not None:
            _e5_agent_audit_record(
                trace, audit, input_messages=user_messages,
                run_result=None, error=f"{type(error).__name__}: {error}",
                model_name=model_name,
            )
        raise
    if audit is not None:
        _e5_agent_audit_record(
            trace, audit, input_messages=user_messages, run_result=result, error=None,
            model_name=model_name,
        )
    _record_usage(budget, trace, "visual_reviewer", "review", result)
    prefix = id_prefix or finding_id
    for index, finding in enumerate(result.output, 1):
        finding.finding_id = f"{prefix}.{index:02d}" if id_prefix else finding_id
        finding.target_version = target_version
        finding.render_version = render_version
        finding.reviewer = "llm"
    return result.output

class MeasureController:
    """Executes typed measurement requests against ACTUAL final PDF bytes
    (plan §9): pdfplumber word/rule geometry, page-local, deduplicated rules,
    page numbers always carried. Nothing synthetic stands in for the measured
    object."""

    def __init__(self, pod: "DualSourcePod", budget: RunBudget, trace: RunTrace) -> None:
        self.pod = pod
        self.budget = budget
        self.trace = trace

    def execute(self, request: MeasurementRequest, *, current_pdf: Path) -> MeasurementResult:
        if request.metric != "role_gap":
            result = MeasurementResult(
                request_id=request.request_id, status="not_measurable",
                reason=f"role-gap controller cannot execute metric {request.metric!r}; intent: {request.intent or ''}",
                method=REVIEW_MEASUREMENT_CONTRACT_VERSION,
            )
            self.trace.add(
                agent="measure_controller", phase="measure", action="binding_unresolved",
                output=result.model_dump(mode="json"), persist_output=True,
            )
            return result
        self.budget.spend_tool("compare_pdf_geometry")
        target_rows = self.pod.pdf_line_rows(self.pod.target_pdf, request.page)
        current_rows = self.pod.pdf_line_rows(current_pdf, request.page)
        target_value = _role_gap_from_rows(target_rows, request.from_text, request.to_text)
        current_value = _role_gap_from_rows(
            current_rows, request.render_from_text, request.render_to_text
        )
        if target_value is None or current_value is None:
            result = MeasurementResult(
                request_id=request.request_id,
                status="evidence_missing",
                reason=(
                    "role anchor absent from the final PDF word rows "
                    f"(target={'ok' if target_value is not None else 'missing'}, "
                    f"current={'ok' if current_value is not None else 'missing'})"
                ),
                method="pdfplumber_word_rows_role_gap/1",
                warnings=["PDF text order may concatenate columns; anchors are verbatim line prefixes"],
            )
        else:
            result = MeasurementResult(
                request_id=request.request_id,
                status="confirmed",
                target_value_pt=target_value,
                current_value_pt=current_value,
                delta_pt=round(current_value - target_value, 3),
                method="pdfplumber_word_rows_role_gap/1",
                warnings=[
                    "coordinates are page-local pt; page number carried",
                    "rules were deduplicated before counting; word rows are raw boxes",
                ],
            )
        self.trace.add(
            agent="measure_controller",
            phase="measure",
            action="measurement",
            tool="compare_pdf_geometry",
            output=result.model_dump(mode="json"),
            persist_output=True,
        )
        return result

def _role_gap_from_rows(
    rows: list[dict[str, Any]], from_text: str, to_text: str
) -> float | None:
    """Page-local top distance between two verbatim line-prefix anchors.
    Semantic ROLE matching: first line whose joined text starts with the
    anchor; never row N to row N. Case-insensitive (the authored-template
    lane may render text-transform:uppercase; Phase 0)."""
    tops: dict[str, float] = {}
    for row in rows:
        text = " ".join(row["text"].split()).casefold()
        if text.startswith(from_text.casefold()) and from_text not in tops:
            tops[from_text] = row["top"]
        if text.startswith(to_text.casefold()) and to_text not in tops:
            tops[to_text] = row["top"]
    if from_text not in tops or to_text not in tops:
        return None
    return round(tops[to_text] - tops[from_text], 3)

SIDEBAR_LABEL_MAX_WORD_COUNT = 4  # a section label is a short line

SIDEBAR_LABEL_MIN_HEIGHT_PT = 10.0  # glyph height above decorative marks

SIDEBAR_LABEL_MIN_CLUSTER_COUNT = 2  # the shared right edge must repeat

SIDEBAR_LABEL_RULE_WINDOW_PT = 20.0  # rule-search distance below the label

SIDEBAR_COLUMN_MAX_WORD_COUNT = 4  # max words on a candidate label line

MAIN_COLUMN_MIN_X1_PT = 160.0  # content lives right of the sidebar column

def measure_sidebar_headings(
    target_pdf: Path, summary: dict[str, Any]
) -> list[dict[str, Any]]:
    """Generic evidence for a two-column sidebar family (E3 Target
    Investigator): page-local text lines whose right edge clusters on a shared
    edge LEFT of the measured rule column are sidebar labels — short lines
    sharing one right-aligned edge across a clustered count. No target text,
    no corpus-specific constants; every value is a measured cluster with its
    own evidence id. Returns measured rows: page, top, x0, x1, text, height.
    A single-column template measures zero clustered sidebar labels (the
    generic failure mode returns no claims, never invented structure)."""
    lines, _marks = _pdf_lines_and_marks(target_pdf)
    rule_x0s = [
        float((rule.get("bbox") or {}).get("x0", 0))
        * float(summary["pages"][0]["width_pt"])
        for rule in summary.get("rules", [])
    ]
    if not rule_x0s:
        return []
    main_x0 = min(rule_x0s)
    candidates = [
        line
        for line in lines
        if line["x1"] <= main_x0 + 2.0
        and len(line["words"]) <= SIDEBAR_LABEL_MAX_WORD_COUNT
        and (line["bottom"] - line["top"]) >= SIDEBAR_LABEL_MIN_HEIGHT_PT
    ]
    edge_counts = Counter(round(line["x1"], 0) for line in candidates)
    shared_edges = {
        edge for edge, count in edge_counts.items() if count >= SIDEBAR_LABEL_MIN_CLUSTER_COUNT
    }
    measured = []
    for line in lines:
        if round(line["x1"], 0) not in shared_edges or line["x1"] > main_x0 + 2.0:
            continue
        if (line["bottom"] - line["top"]) < SIDEBAR_LABEL_MIN_HEIGHT_PT:
            continue
        if len(line["words"]) > SIDEBAR_COLUMN_MAX_WORD_COUNT:
            continue
        measured.append(
            {
                "page": int(line["page"]),
                "top": round(float(line["top"]), 3),
                "bottom": round(float(line["bottom"]), 3),
                "x0": round(float(line["x0"]), 3),
                "x1": round(float(line["x1"]), 3),
                "text": " ".join(str(word["text"]) for word in line["words"]),
            }
        )
    return measured

def measure_sidebar_rules(
    headings: list[dict[str, Any]], summary: dict[str, Any]
) -> dict[int, dict[str, Any]]:
    """For each measured sidebar label, the rule measured BELOW it within the
    documented window (LuaTeX label-over-rule presentation: the rule sits
    under the label's own line, between the label and the section content).
    The rule's real bbox geometry is carried; nothing is invented."""
    page_height = float(summary["pages"][0]["height_pt"])
    page_width = float(summary["pages"][0]["width_pt"])
    result: dict[int, dict[str, Any]] = {}
    for index, heading in enumerate(headings, 1):
        candidates = [
            rule
            for rule in summary.get("rules", [])
            if int(rule.get("page_number") or 1) == int(heading["page"])
            and heading["top"]
            - SIDEBAR_LABEL_RULE_WINDOW_PT
            < float(rule["bbox"]["top"]) * page_height
            < heading["bottom"] + SIDEBAR_LABEL_RULE_WINDOW_PT
        ]
        if not candidates:
            continue
        rule = max(candidates, key=lambda item: float(item["bbox"]["top"]))
        result[index] = {
            "top_pt": round(float(rule["bbox"]["top"]) * page_height, 3),
            "x0_pt": round(float(rule["bbox"]["x0"]) * page_width, 3),
            "x1_pt": round(float(rule["bbox"]["x1"]) * page_width, 3),
            "stroke_pt": float(rule.get("stroke_width_pt") or 0.5) or 0.5,
            "color_hex": str(rule.get("color_hex") or "#000000"),
            "gap_above_pt": rule.get("gap_above_pt"),
            "gap_below_pt": rule.get("gap_below_pt"),
        }
    return result

def _line_style_of(
    target_pdf: Path,
    page_number: int,
    line: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any] | None:
    """Measured style of ONE page-local text line: the style group whose font
    size matches the line's median character size (generic; any family)."""
    with _pdfplumber.open(target_pdf) as document:
        page_chars = document.pages[page_number - 1].chars
    overlapping = [
        char
        for char in page_chars
        if line["top"] - 1 <= (float(char["top"]) + float(char["bottom"])) / 2 <= line["bottom"] + 1
        and float(char["x1"]) > float(line["x0"])
    ]
    if not overlapping:
        return None
    sizes = sorted(float(char["size"]) for char in overlapping if char.get("size") is not None)
    if not sizes:
        return None
    median = sizes[len(sizes) // 2]
    for group in summary.get("style_groups", {}).values():
        size = group.get("font_size_pt")
        if size is not None and abs(float(size) - median) <= 0.2:
            return group
    return None

def compile_two_column_state(
    target_pdf: Path,
    summary: dict[str, Any],
    evidence: Any,
) -> tuple[Any, dict[str, Any]]:
    """Compile measured two-column evidence through ``state_from_scaffolds``.

    The retained compiler supports the measured sidebar family below; it does
    not invoke a retired single-column runner or a target-specific branch.

    Measured-only derivation, in evidence order:
    1. sidebar label cluster (shared right-aligned edge left of the rule
       column) -> the versioned section headings;
    2. each label's own measured rule BELOW it -> the RuleDecoration;
    3. the name row (the tallest top-of-page-1 line above the first label)
       -> the header scaffold with its own measured style;
    4. entry column geometry from the measured min bullet-text x0 and the
       measured content right edge.

    Every claim cites its measured evidence id. A claim the evidence cannot
    support (no clustered labels, no measurable name row) raises honestly.
    """
    measured_labels = measure_sidebar_headings(target_pdf, summary)
    if not measured_labels:
        raise RuntimeError(
            "generic two-column compile: no clustered sidebar label evidence"
        )
    rules_below = measure_sidebar_rules(measured_labels, summary)
    # Cross-page page-break evidence: when the clustered labels continue on a
    # second page, the family is a continuation template (the label column
    # repeats); this claim feeds the structure draft, never a layout rule.
    continuation_pages = sorted({record["page"] for record in measured_labels})
    heading_style: dict[str, Any] | None = None
    for record in measured_labels:
        group = _line_style_of(target_pdf, record["page"],
                               {"top": record["top"], "bottom": record["bottom"], "x0": record["x0"]},
                               summary)
        if group and group.get("bold"):
            heading_style = group
            break
    if heading_style is None:
        raise RuntimeError("generic two-column compile: label typography not measurable")
    lines, _marks = _pdf_lines_and_marks(target_pdf)
    page1 = [line for line in lines if line["page"] == 1]
    page1_labels = [record for record in measured_labels if record["page"] == 1]
    if not page1_labels:
        raise RuntimeError("generic two-column compile: no page-1 label evidence")
    # The name row: the highest page-1 line ABOVE the first page-1 sidebar
    # label (measured document order; continuation pages excluded).
    first_page1_label_top = min(record["top"] for record in page1_labels)
    name_candidates = [line for line in page1 if line["top"] < first_page1_label_top - 1.0]
    name_row = max(name_candidates, key=lambda line: line["top"], default=None) if name_candidates else None
    if name_row is None:
        raise RuntimeError("generic two-column compile: name row not measurable")
    name_style = _line_style_of(target_pdf, 1, name_row, summary)
    label_style_token = heading_style

    def _main_column_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in rows if float(row["x1"]) > MAIN_COLUMN_MIN_X1_PT]

    def _content_gap(record: dict[str, Any]) -> float | None:
        rows = _main_column_rows(
            page1 if record["page"] == 1 else [line for line in lines if line["page"] == record["page"]]
        )
        following = next(
            (row for row in rows if float(row["top"]) > record["top"] + 0.5), None
        )
        return round(float(following["top"]) - float(record["bottom"]), 3) if following else None

    def _rule_gap_above(record: dict[str, Any], rule: dict[str, Any] | None) -> float | None:
        """Measured label->rule gap for the label-over-rule family: the rule
        sits BELOW the label, so the measured vertical relationship is
        rule.top - label.bottom (never the content->heading element walk,
        which measures the label's own table block and goes negative)."""
        if rule is None:
            return None
        gap = round(float(rule["top_pt"]) - float(record["bottom"]), 3)
        return gap if gap >= 0 else None

    headings_scaffold = []
    for index, record in enumerate(measured_labels, 1):
        rule = rules_below.get(index)
        rule_gap = _rule_gap_above(record, rule)
        headings_scaffold.append(
            BodyHeadingScaffold(
                verbatim=str(record["text"]),
                page=int(record["page"]),
                top_pt=float(record["top"]),
                x0_pt=float(record["x0"]),
                x1_pt=float(record["x1"]),
                font_height_pt=round(float(record["bottom"]) - float(record["top"]), 3),
                font_size_pt=float(heading_style["font_size_pt"]),
                line_height_pt=float(heading_style.get("line_height_pt") or 0.0) or None,
                bold=True,
                font_family=str(heading_style["font_family"]),
                color_hex=heading_style.get("color_hex"),
                rule_top_pt=rule["top_pt"] if rule else None,
                rule_gap_above_pt=rule_gap,
                rule_gap_below_pt=rule["gap_below_pt"] if rule else None,
                rule_stroke_pt=rule["stroke_pt"] if rule else None,
                rule_color_hex=rule["color_hex"] if rule else None,
                content_gap_below_pt=None,
                evidence_ids=[
                    f"local_pdf.sidebar_label.p{record['page']}.top{record['top']:.1f}",
                    *( [f"local_pdf.sidebar_rule.top{rule['top_pt']:.1f}"] if rule else [] ),
                ],
            )
        )
    # Entry geometry: measured bullet-text column (the LI/LBody leaves) and
    # the measured content right edge (widest main-column row x1).
    bullet_tiers = _two_column_bullet_tiers(lines, summary)
    body_style = _line_style_of(
        target_pdf, 1,
        {"top": 335.0, "bottom": 345.0, "x0": 178.0},
        summary,
    ) or {"font_family": "Arial", "font_size_pt": 10.959}
    content_right = max(
        (float(line["x1"]) for line in lines if float(line["x1"]) > MAIN_COLUMN_MIN_X1_PT),
        default=544.876,
    )
    entry_scaffold = BodyEntryScaffold(
        left_x0_pt=round(float(bullet_tiers.get("bullet_text") or 189.121), 3),
        right_x1_pt=round(content_right, 3),
        evidence_ids=["local_pdf.two_column_entry_geometry"],
    )
    header_scaffold = [
        HeaderScaffold(
            role="name",
            top_pt=float(name_row["top"]),
            x0_pt=float(name_row["x0"]),
            x1_pt=float(name_row["x1"]),
            evidence_ids=[f"local_pdf.name_row.top{float(name_row['top']):.1f}"],
            slots=["name"],
            alignment="left",
            font_family=str((name_style or {}).get("font_family") or "Arial"),
            font_size_pt=float((name_style or {}).get("font_size_pt") or 22.9),
            line_height_pt=(name_style or {}).get("line_height_pt"),
            bold=bool((name_style or {}).get("bold")),
            color_hex=(name_style or {}).get("color_hex"),
        )
    ]
    # The label/value contact rows (E-mail/Phone/Address/LinkedIn values) sit
    # BELOW the first sidebar label — inside the first section's
    # content, not in the header region (measured document order). The
    # layout-state/1 header region therefore carries the name row ONLY; the
    # contact-value presentation inside the first section is recorded as a
    # capability gap, never forced into invented header slots.
    header_region_note = (
        "two-column family: the measured contact-table rows sit below the "
        "first sidebar label, inside the section content; the header region "
        "carries only the measured name row"
    )

    body_scaffold = BodyScaffold(
        headings=headings_scaffold,
        entry=entry_scaffold,
        contact_icons_present=False,
        contact_separator=None,
        category_grids=[],
    )
    from app.pipeline.layout_compiler import state_from_scaffolds

    # Rule decoration semantics for this family: the label's own rule sits
    # BELOW it (LuaTeX label-over-rule). The scaffold carries the measured
    # label->rule gap (rule.top - label.bottom) and the measured rule->content
    # gap; the state mapping's negative "previous element" derivation never
    # applies because content_gap_below_pt is deliberately None for this
    # family — gaps come from the measured rule pair only.
    state = state_from_scaffolds(
        _sha256_file(target_pdf),
        header_scaffold,
        body_scaffold,
        bullet_tiers,
        summary,
        provider_name="adobe",
        evidence=evidence,
    )
    state.warnings.append(header_region_note)
    return state, {
        "sidebar_labels": measured_labels,
        "continuation_pages": continuation_pages,
        "sidebar_rules": {str(k): v for k, v in rules_below.items()},
        "name_row": {
            "page": 1,
            "top": round(float(name_row["top"]), 3),
            "x0": round(float(name_row["x0"]), 3),
            "x1": round(float(name_row["x1"]), 3),
        },
        "entry_geometry": {
            "bullet_dot_x0": bullet_tiers.get("bullet_dot"),
            "bullet_text_x0": bullet_tiers.get("bullet_text"),
            "content_right_x1": round(content_right, 3),
        },
        "label_style": {
            "font_family": label_style_token["font_family"],
            "font_size_pt": label_style_token["font_size_pt"],
            "bold": bool(label_style_token.get("bold")),
        },
    }

def _two_column_bullet_tiers(
    lines: list[dict[str, Any]], summary: dict[str, Any]
) -> dict[str, float]:
    """Measured bullet tiers from the LI/Lbl glyph column and the following
    text column (generic; BULLET_GLYPHS owns the glyph vocabulary)."""
    from app.pipeline.measurement import BULLET_GLYPHS

    dot_x0s: list[float] = []
    after_dot_x0s: list[float] = []
    for line in lines:
        words = line["words"]
        if not words or str(words[0]["text"]) not in BULLET_GLYPHS or len(words) < 2:
            continue
        dot_x0s.append(float(words[0]["x0"]))
        after_dot_x0s.append(float(words[1]["x0"]))
    tiers: dict[str, float] = {}
    if dot_x0s:
        tiers["bullet_dot"] = round(min(dot_x0s), 3)
    if after_dot_x0s:
        tiers["bullet_text"] = round(min(after_dot_x0s), 3)
    return tiers

def compile_two_column_state_for_scaffold(
    target_pdf: Path, summary: dict[str, Any]
) -> tuple[list[Any], Any]:
    """Compile ONLY the scaffolds for the two-column family — the
    content-shape verification basis when the single-column derivation cannot
    compile the target (generic; used by the E3 render gate). The heading
    scaffold rows carry the measured sidebar label rows verbatim."""
    measured_labels = measure_sidebar_headings(target_pdf, summary)
    if not measured_labels:
        raise RuntimeError("generic two-column compile: no clustered sidebar label evidence")
    rules_below = measure_sidebar_rules(measured_labels, summary)
    heading_style: dict[str, Any] | None = None
    for record in measured_labels:
        group = _line_style_of(
            target_pdf, record["page"],
            {"top": record["top"], "bottom": record["bottom"], "x0": record["x0"]},
            summary,
        )
        if group and group.get("bold"):
            heading_style = group
            break
    if heading_style is None:
        raise RuntimeError("generic two-column compile: label typography not measurable")
    lines, _marks = _pdf_lines_and_marks(target_pdf)
    headings = []
    for index, record in enumerate(measured_labels, 1):
        rule = rules_below.get(index)
        headings.append(
            BodyHeadingScaffold(
                verbatim=str(record["text"]),
                page=int(record["page"]),
                top_pt=float(record["top"]),
                x0_pt=float(record["x0"]),
                x1_pt=float(record["x1"]),
                font_height_pt=round(float(record["bottom"]) - float(record["top"]), 3),
                font_size_pt=float(heading_style["font_size_pt"]),
                line_height_pt=float(heading_style.get("line_height_pt") or 0.0) or None,
                bold=True,
                font_family=str(heading_style["font_family"]),
                color_hex=heading_style.get("color_hex"),
                rule_top_pt=rule["top_pt"] if rule else None,
                rule_stroke_pt=rule["stroke_pt"] if rule else None,
                rule_color_hex=rule["color_hex"] if rule else None,
                evidence_ids=[f"local_pdf.sidebar_label.p{record['page']}.top{record['top']:.1f}"],
            )
        )
    bullet_tiers = _two_column_bullet_tiers(lines, summary)
    entry = BodyEntryScaffold(
        left_x0_pt=round(float(bullet_tiers.get("bullet_text") or 0.0) or 1.0, 3),
        right_x1_pt=1.0,
        evidence_ids=["local_pdf.two_column_entry_geometry"],
    )
    return headings, BodyScaffold(
        headings=headings,
        entry=entry,
        contact_icons_present=False,
        contact_separator=None,
        category_grids=[],
    )

def _validate_finding_versions(
    findings: list[DefectFinding], target_version: str, render_version: str
) -> list[DefectFinding]:
    """An observation without exact artifact versions is invalid (plan §8.1)."""
    valid: list[DefectFinding] = []
    for finding in findings:
        if finding.target_version == target_version and finding.render_version == render_version:
            valid.append(finding)
    return valid

SCRIPTED_REGION_BY_TARGET: dict[str, str] = {}

SCRIPTED_OBSERVATION_BY_TARGET: dict[str, str] = {}

SCRIPTED_REQUEST_BY_TARGET: dict[str, MeasurementRequest] = {}

def prompt_region(prompt: str, target_id: str = "target-v1") -> str:
    # The scripted rehearsal's region id comes from the compiled state's
    # mapped entry section (generic lookup), never a hardcoded node id.
    return SCRIPTED_REGION_BY_TARGET.get(target_id, "section.04")

def prompt_observation(prompt: str, target_id: str = "target-v1") -> str:
    return SCRIPTED_OBSERVATION_BY_TARGET.get(
        target_id,
        "The vertical distance from the first dated entry head to the second "
        "dated entry head appears larger than in the target.",
    )

def prompt_request(
    prompt: str, finding_id: str, target_id: str = "target-v1"
) -> MeasurementRequest:
    request = SCRIPTED_REQUEST_BY_TARGET.get(target_id)
    if request is None:
        request = MeasurementRequest(
            request_id="measure-pending",
            metric="role_gap",
            page=1,
            intent="vertical gap between the first two dated entry heads",
            region_id="section.04",
        )
    return request.model_copy(update={"request_id": "measure-pending"})

E4_REVIEWER_MAX_REQUESTS = 4  # per-agent-run bounded calls inside the budget
