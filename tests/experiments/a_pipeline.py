"""Run the isolated live A-pipeline experiment on one target/source pair."""

from __future__ import annotations

import argparse
import base64
import csv
import difflib
import hashlib
import html as html_lib
import json
import os
import re
import shutil
import statistics
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Literal

import pdfplumber
import pypdfium2 as pdfium
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.generation.chrome_html_to_pdf import HtmlToPdfExportError, _find_chrome
from app.ingestion.docx_reader import read_docx
from app.ingestion.pdf_reader import read_pdf_text
from app.template_analysis.commercial.adobe import (
    _flatten_text_elements,
    run_adobe_layout,
)
from app.template_analysis.commercial.local_color import (
    enrich_badges_from_local_pdf,
    enrich_colors_from_local_pdf,
    enrich_rules_from_local_pdf,
)


from app.template_analysis.commercial.models import NormalizedLayoutEvidence
from tests.experiments.deepseek import (
    PipelineCallError,
    _chat_completion,
    _chat_content,
    _deepseek_client,
    visual_client,
    _html_call,
    _image_data,
    _structured_chat_call,
    _usage_from,
)
from tests.experiments.fill_plan import (
    _section_semantic,
    analyze_candidate_provenance,
    append_heading_case_rule,
    deduplicate_candidate_html,
    normalize_header_element,
    normalize_section_headings,
    provenance_report,
    source_blocks,
    source_lines,
    validate_candidate_html,
)

from lxml import etree, html as lxml_html
from tests.experiments.refinement import (
    BUILDER_ACTIONS,
    BUILDER_PROPERTIES,
    FILLER_ACTIONS,
    BuilderOperation,
    CandidateState,
    GateFailure,
    HardGateResult,
    PlacementOperation,
    RefinementResult,
    RubricResult,
    VisualComparison,
    VisualIssue,
    VisualRegression,
    apply_builder_operation,
    apply_placement_operation,
    build_measurement_catalog,
    _class_names,
    _consolidate_gate_failures,
    _declared_class_names,
    _style_blocks,
    gate_failure_issue,
    issue_fingerprint,
    resolve_measurements,
    route_issue,
    select_issue,
    update_issue_ledger,
    validate_builder_operation,
    validate_filler_conformance,
    validate_scratch_repair,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET = ROOT / "tests/local_datasets/resume_matrix/resume_A.pdf"
DEFAULT_SOURCE = ROOT / "data/input_samples/synthetic_resume.docx"
RUNS = Path(__file__).with_name("runs")
FORMAT_SUMMARY_VERSION = "a-pipeline-format-summary/3"
TEMPLATE_PROMPT_VERSION = "a-pipeline-template-prompt/3"
ANALYZER_VERSION = "a-pipeline-path-grouper/1"
CHROME_FLAGS = (
    "--headless=new",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--no-pdf-header-footer",
    "--print-to-pdf-no-header",
    "--window-size=1280,1600",
    "--force-device-scale-factor=1",
    "--lang=en-US",
)



def _pinned_chrome_environment(summary: dict[str, Any]) -> dict[str, Any]:
    executable = _find_chrome()
    version = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()
    return {
        "executable": executable,
        "version": version,
        "viewport": {"width": 1280, "height": 1600},
        "device_scale_factor": 1,
        "locale": "en-US",
        "timezone": "UTC",
        "flags": list(CHROME_FLAGS),
        "required_fonts": sorted({row["font_family"] for row in summary.get("style_groups", {}).values() if row.get("font_family")}),
        "capture_renderer": f"pypdfium2 {getattr(pdfium, '__version__', 'unknown')}",
    }



def _export_pinned_html_to_pdf(html_path: Path, output_path: Path, environment: dict[str, Any]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        environment["executable"],
        *environment["flags"],
        # Owner ruling 2026-09-10 (E→F thirteenth freeze): web fonts load
        # asynchronously after the load event — without waiting, date-column
        # widths varied up to ~3.7pt between renders of the same artifact.
        # The virtual-time budget blocks printing until pending work (incl.
        # document.fonts loading) settles, making print geometry deterministic.
        "--virtual-time-budget=10000",
        f"--print-to-pdf={output_path}",
        html_path.resolve().as_uri(),
    ]
    process_environment = {**os.environ, "LANG": "en_US.UTF-8", "TZ": environment["timezone"]}
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, env=process_environment)
    except subprocess.TimeoutExpired as error:
        raise HtmlToPdfExportError("Pinned Chrome PDF render timed out after 120 seconds") from error
    if result.returncode or not output_path.exists() or output_path.stat().st_size == 0:
        raise HtmlToPdfExportError(f"Pinned Chrome PDF render failed: {result.stderr.strip()[-500:]}")
    return output_path



class L0Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    missing_source_content: list[str] = Field(default_factory=list)
    explained_exceptions: list[str] = Field(default_factory=list)
    notes: str = ""



class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts = []
        self.sections = []
        self._section = None
        self._depth = 0
        self._ignored = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"style", "script", "title"}:
            self._ignored = tag
            return
        if self._ignored:
            return
        values = dict(attrs)
        if self._section is not None:
            self._depth += 1
            return
        if values.get("data-section") is not None:
            self._section = {"name": values["data-section"], "parts": []}
            self._depth = 1
            return

    def handle_endtag(self, tag: str) -> None:
        if tag == self._ignored:
            self._ignored = None
            return
        if self._ignored:
            return
        if self._section is None:
            return
        self._depth -= 1
        if self._depth == 0:
            self.sections.append(self._section)
            self._section = None
            return

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        self.parts.append(data)
        if self._section is not None:
            self._section["parts"].append(data)
            return



def _image_sources(document: str) -> set[str]:
    sources = set(re.findall(r"<img\b[^>]*\bsrc=[\"']([^\"']+)", document, re.I))
    sources.update(
        value
        for value in re.findall(r"url\(\s*[\"']?([^\"')]+)", document, re.I)
        if value.startswith("data:image") or not re.search(r"\.(?:woff2?|ttf|otf)(?:$|[?#])", value, re.I)
    )
    return sources



def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)



def _median(values: list[float]) -> float | None:
    return _round(statistics.median(values)) if values else None



def _char_spacing_pt(block: Any, page_width: float) -> float | None:
    bounds = block.char_bounds
    if len(bounds) < 2:
        return None
    gaps = [
        (right.x0 - left.x1) * page_width
        for left, right in zip(bounds, bounds[1:])
        if abs(right.top - left.top) * page_width < 2
    ]
    return _median(gaps)



def _raw_paths(raw: dict[str, Any], evidence: NormalizedLayoutEvidence) -> dict[str, str]:
    """Replay Adobe's exact flatten traversal and retain each emitted Path."""
    structured = raw.get("structured_data") if isinstance(raw.get("structured_data"), dict) else raw
    source = structured.get("elements") or structured.get("Elements") or []
    paths: dict[str, str] = {}
    flattened = _flatten_text_elements(source)
    for order, element in enumerate(flattened):
        page = int(element.get("Page") or element.get("page") or 0) + 1
        element_id = f"adobe.page.{page}.element.{order}"
        paths[element_id] = str(element.get("Path") or element.get("path") or "")
    missing = [block.element_id for block in evidence.text_blocks if block.element_id not in paths]
    if missing:
        raise RuntimeError(f"Adobe flatten traversal did not retain paths for {missing[:5]}")
    return paths



def _path_parts(path: str) -> list[str]:
    return [part for part in path.split("/") if part]



def _path_prefix(parts: list[str], end: int) -> str:
    return "//" + "/".join(parts[: end + 1])



def _sect_paths(path: str) -> list[str]:
    parts = _path_parts(path)
    return [_path_prefix(parts, index) for index, part in enumerate(parts) if part.split("[", 1)[0] == "Sect"]



def _parent_path(path: str) -> str:
    parts = _path_parts(path)
    return "//" + "/".join(parts[:-1]) if len(parts) > 1 else "//Document"



def _bbox_union(rows: list[dict[str, Any]]) -> dict[str, float] | None:
    boxes = [row["bbox_pt"] for row in rows if row.get("bbox_pt")]
    if not boxes:
        return None
    return {
        "x0": _round(min(box["x0"] for box in boxes)),
        "top": _round(min(box["top"] for box in boxes)),
        "x1": _round(max(box["x1"] for box in boxes)),
        "bottom": _round(max(box["bottom"] for box in boxes)),
    }



def _vertical_gap(previous: dict[str, Any], current: dict[str, Any]) -> float | None:
    left, right = previous.get("bbox_pt"), current.get("bbox_pt")
    if not left or not right or previous["page"] != current["page"]:
        return None
    return _round(right["top"] - left["bottom"])



def _nearest_visual_section(row: dict[str, Any], headings: list[dict[str, Any]]) -> str | None:
    box = row.get("bbox_pt")
    if not box:
        return None
    preceding = [
        heading
        for heading in headings
        if heading["page"] == row["page"]
        and heading["bbox_pt"]
        and heading["bbox_pt"]["top"] <= box["top"] + 1
    ]
    return preceding[-1]["section_path"] if preceding else None



def _build_groups(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build neutral visual groups from Path, correcting impossible placement geometrically."""
    headings = []
    for row in elements:
        leaf = _path_parts(row["path"])[-1].split("[", 1)[0] if _path_parts(row["path"]) else ""
        if row["structural_role"] == "heading_candidate":
            if leaf == "H1":
                row["section_path"] = _parent_path(row["path"])
                headings.append(row)
    headings.sort(key=lambda row: (row["page"], row["bbox_pt"]["top"] if row["bbox_pt"] else 1000000000.0))
    heading_sections = {row["section_path"] for row in headings}

    for row in elements:
        path_sections = [path for path in _sect_paths(row["path"]) if path in heading_sections]
        path_section = path_sections[-1] if path_sections else None
        visual_section = _nearest_visual_section(row, headings)
        heading = next((item for item in headings if item["section_path"] == path_section), None)
        if (
            heading
            and row.get("bbox_pt")
            and heading.get("bbox_pt")
            and row["bbox_pt"]["top"] + 1 < heading["bbox_pt"]["top"]
        ):
            path_section = visual_section
            row["grouping_warning"] = "Path section contradicted measured vertical order; geometry fallback used."
        row["section_path"] = path_section or visual_section or "//Document/header"

    containers: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in elements:
        section = row["section_path"]
        deeper = [path for path in _sect_paths(row["path"]) if path.startswith(section + "/")]
        table_match = re.search(r"^(.*?/Table/TR(?:\[\d+\])?)", row["path"])
        entry = deeper[0] if deeper else (table_match.group(1) if table_match else "")
        container = entry or section
        row["entry_path"] = entry or None
        containers.setdefault((section, container), []).append(row)

    entries = [(key, rows) for key, rows in containers.items() if key[1] != key[0]]

    for row in elements:
        if row["entry_path"] or row["structural_role"] == "heading_candidate" or not row.get("bbox_pt"):
            continue
        candidates = []
        for (section, entry), rows in entries:
            box = _bbox_union(rows)
            if section != row["section_path"] or not box or rows[0]["page"] != row["page"]:
                continue
            distance = (
                0.0
                if box["top"] <= row["bbox_pt"]["bottom"] and row["bbox_pt"]["top"] <= box["bottom"]
                else min(abs(row["bbox_pt"]["top"] - box["bottom"]), abs(box["top"] - row["bbox_pt"]["bottom"]))
            )
            candidates.append((distance, entry, rows))
        if not candidates:
            continue
        if candidates[0 if len(candidates) == 1 else min(range(len(candidates)), key=lambda i: candidates[i][0])][0] <= 3:
            _distance, entry, rows = min(candidates, key=lambda item: item[0])
            containers[(row["section_path"], row["section_path"])].remove(row)
            rows.append(row)
            row["entry_path"] = entry
            row["grouping_warning"] = "Metadata attached to repeated entry by measured vertical alignment."

    groups = []
    for section in dict.fromkeys(row["section_path"] for row in elements):
        section_rows = [row for row in elements if row["section_path"] == section]
        groups.append(
            {
                "id": f"group_{len(groups) + 1}",
                "kind": "section",
                "path": section,
                "parent_id": None,
                "element_ids": [row["id"] for row in section_rows],
                "bbox_pt": _bbox_union(section_rows),
                "spacing_before_pt": None,
            }
        )
        section_id = groups[-1]["id"]
        section_containers = [(key, rows) for key, rows in containers.items() if key[0] == section and rows]
        section_containers.sort(key=lambda item: min((row["bbox_pt"]["top"] for row in item[1] if row.get("bbox_pt")), default=1000000000.0))
        previous = None
        for (owner, container), rows in section_containers:
            ordered = sorted(
                rows,
                key=lambda row: (
                    row["page"],
                    row["bbox_pt"]["top"] if row.get("bbox_pt") else 1000000000.0,
                    row["bbox_pt"]["x0"] if row.get("bbox_pt") else 1000000000.0,
                ),
            )
            kind = "repeated_entry" if container != owner else "body"
            group = {
                "id": f"group_{len(groups) + 1}",
                "kind": kind,
                "path": container,
                "parent_id": section_id,
                "element_ids": [row["id"] for row in ordered],
                "bbox_pt": _bbox_union(ordered),
                "spacing_before_pt": (_vertical_gap(previous, ordered[0]) if previous else None),
            }
            groups.append(group)
            parent_id = group["id"]
            role_rows = []
            role = None
            for row in ordered:
                leaf = _path_parts(row["path"])[-1].split("[", 1)[0] if _path_parts(row["path"]) else ""
                current = (
                    "body"
                    if row["structural_role"] == "list"
                    else "meta"
                    if leaf in {"Aside", "Sub"} or (row.get("bbox_relative") and row["bbox_relative"]["x0"] >= 0.55)
                    else "primary"
                )
                if role_rows and current != role:
                    groups.append(
                        {
                            "id": f"group_{len(groups) + 1}",
                            "kind": role,
                            "path": container,
                            "parent_id": parent_id,
                            "element_ids": [item["id"] for item in role_rows],
                            "bbox_pt": _bbox_union(role_rows),
                            "spacing_before_pt": (
                                _vertical_gap(ordered[ordered.index(role_rows[0]) - 1], role_rows[0])
                                if ordered.index(role_rows[0])
                                else None
                            ),
                        }
                    )
                    role_rows = []
                role, role_rows = current, [*role_rows, row]
            if role_rows:
                first_index = ordered.index(role_rows[0])
                groups.append(
                    {
                        "id": f"group_{len(groups) + 1}",
                        "kind": role,
                        "path": container,
                        "parent_id": parent_id,
                        "element_ids": [item["id"] for item in role_rows],
                        "bbox_pt": _bbox_union(role_rows),
                        "spacing_before_pt": (
                            _vertical_gap(ordered[first_index - 1], role_rows[0]) if first_index else None
                        ),
                    }
                )
            previous = ordered[-1]
    return groups



def _measure_margins(evidence: NormalizedLayoutEvidence, pdf_path: Path | None) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    pages = {page.page_number: page for page in evidence.pages}
    visible_tops: dict[int, float] = {}
    if pdf_path is not None:
        with pdfplumber.open(pdf_path) as pdf:
            for number, page in enumerate(pdf.pages, 1):
                words = page.extract_words()
                if not words:
                    continue
                visible_tops[number] = min(float(word["top"]) for word in words)
    by_page: dict[int, dict[str, float | None]] = {}
    for number, page in pages.items():
        rules = [rule for rule in evidence.rules if rule.page_number == number and rule.bbox.x1 - rule.bbox.x0 >= 0.5]
        blocks = [block for block in evidence.text_blocks if block.page_number == number and block.bbox]
        left = _median([(rule.bbox.x0) * page.width_pt for rule in rules])
        right = _median([(1 - rule.bbox.x1) * page.width_pt for rule in rules])
        top = _round(
            visible_tops.get(number, min((block.bbox.top * page.height_pt for block in blocks), default=None))
        )
        if left is None or right is None:
            warnings.append(f"page {number}: stable horizontal rule edges did not establish both side margins")
        if top is None:
            warnings.append(f"page {number}: visible text did not establish the top boundary")
        bottom = top
        by_page[number] = {"left": left, "top": top, "right": right, "bottom": bottom}
    classes = {
        "single" if len(pages) == 1 else "first" if number == 1 else "last" if number == len(pages) else "continuation": values
        for number, values in by_page.items()
    }
    first = by_page[min(by_page)] if by_page else {key: None for key in ("left", "top", "right", "bottom")}
    return (
        {
            "default": first,
            "page_classes": classes,
            "provenance": {
                "left_right": "local_pdf.long_horizontal_rules",
                "top": "local_pdf.visible_words",
                "bottom": "derived.owner_policy.mirror_measured_top",
            },
        },
        warnings,
    )



def _require_geometry(summary: dict[str, Any]) -> None:
    missing = [f"margin.{key}" for key, value in summary["margins_pt"]["default"].items() if value is None]
    if missing:
        raise RuntimeError(
            "Required geometry is unmeasured; template compilation stopped before Builder: " + ", ".join(missing)
        )



def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()



def _template_cache_key(target: Path, summary: dict[str, Any], provider: str, model: str) -> str:
    payload = {
        "target_sha256": _sha256(target),
        "format_summary_version": FORMAT_SUMMARY_VERSION,
        "template_prompt_version": TEMPLATE_PROMPT_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "provider_version": summary.get("provider_version"),
        "provider": provider,
        "model": model,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()



def build_format_summary(evidence: NormalizedLayoutEvidence, raw: dict[str, Any] | None, pdf_path: Path | None) -> dict[str, Any]:
    """Compress measured evidence into Path-aware neutral visual groups."""
    pages = {page.page_number: page for page in evidence.pages}
    signatures: dict[tuple[Any, ...], str] = {}
    style_rows: dict[str, dict[str, Any]] = {}
    elements: list[dict[str, Any]] = []
    paths = (
        _raw_paths(raw, evidence)
        if raw is not None
        else {block.element_id: "" for block in evidence.text_blocks}
    )
    for block in evidence.text_blocks:
        signature = (
            block.font_family,
            _round(block.font_size_pt),
            block.bold,
            block.italic,
            block.color_hex,
        )
        style_id = signatures.setdefault(signature, f"style_{len(signatures) + 1}")
        page = pages.get(block.page_number)
        row = style_rows.setdefault(
            style_id,
            {
                "font_family": block.font_family,
                "font_size_pt": _round(block.font_size_pt),
                "bold": block.bold,
                "italic": block.italic,
                "color_hex": block.color_hex,
                "line_height_pt": [],
                "char_spacing_pt": [],
                "text_samples": [],
                "provenance": sorted(block.provenance),
            },
        )
        if block.line_height_pt is not None:
            row["line_height_pt"].append(block.line_height_pt)
        if page and (spacing := _char_spacing_pt(block, page.width_pt)) is not None:
            row["char_spacing_pt"].append(spacing)
        if len(row["text_samples"]) < 3:
            row["text_samples"].append(block.text[:120])
        box = block.bbox
        elements.append(
            {
                "id": block.element_id,
                "page": block.page_number,
                "order": block.reading_order,
                "style_id": style_id,
                "structural_role": block.structural_role,
                "text_sample": block.text[:160],
                "bbox_relative": box.model_dump() if box else None,
                "bbox_pt": (
                    {
                        "x0": _round(box.x0 * page.width_pt),
                        "top": _round(box.top * page.height_pt),
                        "x1": _round(box.x1 * page.width_pt),
                        "bottom": _round(box.bottom * page.height_pt),
                    }
                    if box and page
                    else None
                ),
                "spacing_before_pt": _round(block.spacing_before_pt),
                "spacing_after_pt": _round(block.spacing_after_pt),
                "path": paths[block.element_id],
            }
        )

    for row in style_rows.values():
        row["line_height_pt"] = _median(row["line_height_pt"])
        row["char_spacing_pt"] = _median(row["char_spacing_pt"])

    margins, margin_warnings = _measure_margins(evidence, pdf_path)
    groups = _build_groups(elements)
    return {
        "schema_version": FORMAT_SUMMARY_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "provider_version": evidence.provider_version,
        "measurement_policy": "Adobe geometry and typography; local PDF color/rules/badges",
        "pages": [page.model_dump() for page in evidence.pages],
        "margins_pt": margins,
        "style_groups": style_rows,
        "elements": elements,
        "visual_groups": groups,
        "spacing_pt": {
            "line_within_block_by_style": {
                key: value["line_height_pt"] for key, value in style_rows.items()
            },
            "character_by_style": {
                key: value["char_spacing_pt"] for key, value in style_rows.items()
            },
            "per_group": {
                group["id"]: group["spacing_before_pt"] for group in groups
            },
        },
        "rules": [rule.model_dump(mode="json") for rule in evidence.rules],
        "badges": [badge.model_dump(mode="json") for badge in evidence.badge_clusters],
        "warnings": [
            *evidence.warnings,
            *margin_warnings,
            *[row["grouping_warning"] for row in elements if row.get("grouping_warning")],
        ],
    }



VISUAL_RUBRIC = (
    ("header_hierarchy_alignment", "Header hierarchy and alignment"),
    ("contact_value_icon_pairing", "Each visible contact value matches its icon and semantic slot"),
    ("body_typography", "Body typography"),
    ("section_heading_hierarchy", "Section heading hierarchy"),
    ("section_content_mapping", "Candidate content is placed under the correct semantic section"),
    ("entry_structure", "Entry structure"),
    ("vertical_spacing_density", "Vertical spacing density"),
    ("separator_rule_geometry", "Separator-rule geometry"),
    ("margins_columns", "Margins and columns"),
    ("continuation_page_consistency", "Continuation-page consistency"),
    ("design_system_match", "Overall design system matches the target (heading component style, case, rules, list style)"),
    ("section_order", "Section order matches the target's section order"),
)



def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()



def _artifact_ref(path: Path | None) -> dict[str, str] | None:
    return None if path is None else {"path": str(path), "sha256": _sha256(path)}



def build_protected_rubrics(gates: Any, rubric_results: Any) -> list[dict[str, str]]:
    protected: list[dict[str, str]] = []
    if gates and gates.passed:
        protected.append({"rubric_id": "deterministic.hard_gates", "evidence": "retained champion hard gates passed"})
        for group_name, group in gates.independent.items():
            if not isinstance(group, dict):
                continue
            for name, result in group.items():
                if not isinstance(result, dict):
                    continue
                if result.get("passed") is True:
                    protected.append({"rubric_id": f"deterministic.{group_name}.{name}", "evidence": "explicit deterministic pass"})
    protected.extend(
        {"rubric_id": result.rubric_id, "evidence": result.evidence}
        for result in rubric_results
        if result.status == "pass"
    )
    return list({row["rubric_id"]: row for row in protected}.values())



def _builder_operation_call(context: dict[str, Any], model: str, image_paths: list[str]) -> tuple[dict[str, Any], dict[str, int]]:
    content = _chat_content(json.dumps(context, ensure_ascii=False, sort_keys=True), image_paths)
    result, usage = _structured_chat_call(
        model,
        [
            {"role": "system", "content": "Return exactly one bounded CSS operation matching the schema. Do not return HTML or CSS text. Respond with JSON only."},
            {"role": "user", "content": content},
        ],
        BuilderOperation,
    )
    return result.model_dump(mode="json"), usage



def _placement_operation_call(context: dict[str, Any], model: str, image_paths: list[str]) -> tuple[dict[str, Any], dict[str, int]]:
    result, usage = _structured_chat_call(
        model,
        [
            {"role": "system", "content": "Return exactly one bounded source placement operation matching the schema. Do not return HTML or CSS. Respond with JSON only."},
            {"role": "user", "content": _chat_content(json.dumps({**context, "operation_schema": PlacementOperation.model_json_schema()}, ensure_ascii=False, sort_keys=True), image_paths)},
        ],
        PlacementOperation,
    )
    return result.model_dump(mode="json"), usage



def _template_prompt(summary: dict[str, Any]) -> str:
    return f"""Build an EMPTY, self-contained HTML/CSS resume template from the measured format summary below.

Hard rules:
- Use only geometry and typography values present in the summary. Do not estimate or improve them.
- Reproduce page size, margins, columns, rules, colors, five spacing types, and badge/icon-like decorations.
- Target text samples reveal semantic roles only. Never copy target names, employers, dates, contacts, achievements, or other facts.
- Use conspicuous generic placeholders such as [FULL NAME], [CONTACT], [SECTION HEADING], [ENTRY TITLE], [DATE], and [SOURCE TEXT].
- Mark every section container data-section="semantic-name".
- Mark cloneable examples data-repeatable="work-entry|education-entry|skill-group|project-entry|other-entry".
- Fixed slots use data-slot="name|contact|heading|body". Filler may clone repeatable units but must preserve this CSS and page geometry.
- CSS must use pt units for measured geometry, @page size/margins, print-color-adjust, sensible break-inside rules, and flowing content (no target page image/background).
- Apply page margins exactly once through @page. Do not repeat page size, min-height, margins, or padding on body/page wrappers; that creates blank pages.
- Use one continuous flowing body. Do not hard-code or force the target's sample page count. Variable candidate content may naturally add pages.
- Treat text bounding-box width as sample-content evidence, not a fixed slot width. Candidate names and other variable text must wrap or expand without clipping.
- Fixed labels such as section headings must use white-space: nowrap and flex-shrink: 0 so adjacent flowing content cannot wrap or fragment them.
- Include installed fallbacks in every font stack: Lato/Roboto -> Arial; Charter -> Times New Roman. Never end a measured font stack at a generic family alone.
- Contact icons must use the --font-icon stack with Font Awesome 6 Free then Font Awesome 6 Brands; do not declare a generic "Font Awesome" family directly.
- Include placeholder examples for arbitrary extra source sections.

FORMAT SUMMARY:
{json.dumps(summary, ensure_ascii=False, sort_keys=True)}
"""



def build_reviewer_context(
    *,
    target_evidence: dict[str, Any],
    champion_evidence: dict[str, Any] | None,
    challenger_evidence: dict[str, Any],
    format_summary: dict[str, Any],
    hard_gates: Any,
    protected_rubrics: list[dict[str, str]],
    issue_ledger: dict[str, dict[str, Any]],
    repair_outcomes: list[dict[str, Any]],
    focus_issue: Any,
    source_text: str = "",
    unresolved_issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current_unresolved = unresolved_issues or []
    current_fingerprints = {row["issue_fingerprint"] for row in current_unresolved}
    return {
        "target_evidence_board": target_evidence,
        "champion_evidence_board": champion_evidence,
        "challenger_evidence_board": challenger_evidence,
        "format_summary": {
            "margins_pt": format_summary.get("margins_pt"),
            "page_classes": format_summary.get("page_classes"),
            "measurement_catalog": build_measurement_catalog(format_summary),
        },
        "hard_gate_pass_summary": hard_gates.model_dump(mode="json"),
        "visual_rubric": [
            {"rubric_id": rubric_id, "description": description}
            for rubric_id, description in VISUAL_RUBRIC
        ],
        "protected_rubric_ids": protected_rubrics,
        "resolved_issue_history": [
            {"issue_fingerprint": fingerprint, "status": "resolved_by_current_hard_gates"}
            for fingerprint in issue_ledger
            if fingerprint not in current_fingerprints
        ],
        "unresolved_issues": current_unresolved,
        "focus_issue": focus_issue.model_dump(mode="json") if focus_issue else None,
        "recent_repair_outcomes": repair_outcomes[-4:],
        "candidate_source_lines": source_lines(source_text) if source_text else {},
        "candidate_source_blocks": source_blocks(source_text) if source_text else {},
        "measurement_reference_ids": sorted(build_measurement_catalog(format_summary)),
    }



def build_filler_context(
    *,
    source_text: str,
    template_html: str,
    champion_filled_html: str,
    issue: Any,
    action: str,
    resolved_measurements: list[dict[str, Any]],
    target_evidence: dict[str, Any],
    champion_evidence: dict[str, Any] | None,
    protected_rubrics: list[dict[str, str]],
    gate_failures: list[dict[str, Any]],
    same_issue_outcomes: list[dict[str, Any]],
    repair_outcomes: list[dict[str, Any]],
    unresolved_issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "candidate_source_lines": source_lines(source_text),
        "candidate_source_blocks": source_blocks(source_text),
        "run_local_template": template_html,
        "retained_champion_filled_html": champion_filled_html,
        "selected_issue": issue.model_dump(mode="json") if issue else None,
        "selected_action": action,
        "resolved_measurements": resolved_measurements,
        "target_region_evidence": target_evidence,
        "champion_region_evidence": champion_evidence,
        "protected_rubric_ids": protected_rubrics,
        "deterministic_gate_failures": [] if issue else gate_failures,
        "prior_outcomes_for_issue": same_issue_outcomes,
        "recent_repair_summaries": repair_outcomes[-4:],
        "unresolved_issues": unresolved_issues or [],
        "change_history": repair_outcomes[-8:],
        "authority": {
            "allowed": [
                "assign source lines to existing slots",
                "regroup source lines into records",
                "clone or remove repeatable components",
                "select an existing component variant",
                "remove unused placeholders",
            ],
            "forbidden": [
                "modify style blocks or stylesheets",
                "add inline styles",
                "introduce CSS classes",
                "modify typography, spacing, rules, margins, page geometry, or decorations",
            ],
            "provenance": {
                "header_owner": 'data-source-block="document"',
                "section_owner": 'data-source-block="block:Lxxxx" data-source-heading="Lxxxx"',
                "record_owner": 'data-source-record="record:Lxxxx"',
                "rule": 'Use prefixes literally. Annotate source text only on leaves with data-source-line="Lxxxx".',
            },
        },
    }



def build_scratch_repair_context(
    *,
    scratch_draft_html: str,
    issue: Any,
    source_text: str,
    prior_outcomes: list[dict[str, Any]],
    unresolved_issues: list[dict[str, Any]] | None = None,
    change_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    details = json.loads(issue.evidence)
    grouped_rows = details if isinstance(details, list) and details and isinstance(details[0], dict) else None
    lines = source_lines(source_text)
    blocks = source_blocks(source_text)
    rows = grouped_rows or ([details] if isinstance(details, dict) else [])
    line_ids = {row.get("source_line_id") for row in rows} - {None}
    if isinstance(details, list) and all(isinstance(item, str) for item in details):
        missing = {item.casefold() for item in details}
        line_ids.update(line_id for line_id, line in lines.items() if line.casefold() in missing)
    selected_issue = issue.model_dump(mode="json")
    selected_issue.pop("evidence")
    selected_issue["fingerprint"] = issue_fingerprint(issue)
    if grouped_rows:
        issue_details = {
            "grouped": True,
            "rows": [
                {
                    key: value for key, value in row.items()
                    if key not in {"allowed_destination_nodes", "offending_nodes", "relevant_fragments", "code"}
                }
                for row in grouped_rows
            ],
        }
        offending_nodes = [
            node
            for row in grouped_rows
            for node in (row.get("offending_nodes") or row.get("relevant_fragments") or [])[:2]
        ][:12]
        allowed_destination_nodes = [
            node
            for row in grouped_rows
            for node in (row.get("allowed_destination_nodes") or [])[:1]
        ][:8]
    elif isinstance(details, dict):
        issue_details = {
            key: value
            for key, value in details.items()
            if key not in {"expected_block", "actual_block", "expected_record", "actual_record", "offending_nodes", "relevant_fragments", "source_line_id", "allowed_destination_nodes", "source_text", "code"}
        }
        offending_nodes = details.get("offending_nodes", details.get("relevant_fragments", []))
        allowed_destination_nodes = details.get("allowed_destination_nodes", [])
    else:
        issue_details = {"missing_fragments": details}
        offending_nodes = []
        allowed_destination_nodes = []
    return {
        "scratch_draft_html": scratch_draft_html,
        "source_text": source_text,
        "selected_issue": selected_issue,
        "issue_details": issue_details,
        "multi_line_rule": "When the source has several lines for the same slot, render one element per line by cloning the slot element; never drop a line to fit a single-slot template.",
        "relevant_source_lines": {line_id: lines[line_id] for line_id in line_ids if line_id in lines},
        "candidate_source_blocks": {line_id: blocks[line_id] for line_id in line_ids if line_id in blocks},
        "expected_actual_owners": (
            {}
            if grouped_rows or not isinstance(details, dict)
            else {
                key: details.get(key)
                for key in ("expected_block", "actual_block", "expected_record", "actual_record")
                if key in details
            }
        ),
        "offending_nodes": offending_nodes,
        "allowed_destination_nodes": allowed_destination_nodes,
        "literal_provenance_contract": {
            "line": 'data-source-line="Lxxxx"',
            "header": 'data-source-block="document"',
            "section": 'data-source-block="block:Lxxxx" data-source-heading="Lxxxx"',
            "record": 'data-source-record="record:Lxxxx"',
        },
        "prior_outcomes_for_fingerprint": prior_outcomes[-2:],
        "unresolved_issues": unresolved_issues or [],
        "change_history": (change_history or [])[-8:],
    }



def build_builder_context(
    *,
    issue: Any,
    action: str,
    resolved_measurements: list[dict[str, Any]],
    champion: Any,
    target_evidence: dict[str, Any],
    champion_evidence: dict[str, Any] | None,
    protected_rubrics: list[dict[str, str]],
    same_issue_operations: list[dict[str, Any]],
    unresolved_issues: list[dict[str, Any]] | None = None,
    change_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    operation_schema = BuilderOperation.model_json_schema()
    operation_schema["properties"]["action"] = {"const": action, "type": "string"}
    operation_schema["properties"]["property"] = {"enum": sorted(BUILDER_PROPERTIES[action]), "type": "string"}
    operation_schema["properties"]["measurement_ref"] = {
        "enum": [row["measurement_ref"] for row in resolved_measurements],
        "type": "string",
    }
    existing_selectors = sorted(
        {f"[data-section='{name}']" for name in re.findall(r'data-section="([^"]+)"', champion.template_html)}
        | {f"[data-slot='{name}']" for name in re.findall(r'data-slot="([^"]+)"', champion.template_html)}
        | {f"[data-repeatable='{name}']" for name in re.findall(r'data-repeatable="([^"]+)"', champion.template_html)}
        | {f".{name}" for name in _declared_class_names(champion.template_html)}
    )
    return {
        "selected_issue": issue.model_dump(mode="json"),
        "selected_action": action,
        "resolved_measurements": resolved_measurements,
        "champion_template": champion.template_html,
        "champion_filled_html": champion.filled_html,
        "target_region_evidence": target_evidence,
        "champion_region_evidence": champion_evidence,
        "protected_rubric_ids": protected_rubrics,
        "previous_builder_operations_for_issue": same_issue_operations,
        "unresolved_issues": unresolved_issues or [],
        "change_history": (change_history or [])[-8:],
        "operation_schema": operation_schema,
        "action_property_allowlist": sorted(BUILDER_PROPERTIES[action]),
        "existing_selectors": existing_selectors,
        "constraints": [
            "one operation",
            "selector MUST come from existing_selectors verbatim",
            "value derived only from measurement_ref",
            "no content or DOM mutation",
            "protected items must not regress",
        ],
    }



def _write_json(path: Path | None, value: Any) -> None:
    if path is not None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")



def _filler_prompt(context: dict[str, Any]) -> str:
    if "scratch_draft_html" in context:
        return 'Repair the quarantined scratch draft into a hard-gate-valid candidate.\nResolve every unresolved deterministic issue in this call, using selected_issue as the first priority rather than an exclusive scope.\nYou may regroup candidate-content DOM wherever needed. Preserve correct content and provenance. CSS, stylesheets, inline styles, assets, and presentation geometry are frozen.\nReturn one complete HTML document. The full hard-gate suite will verify every change. Use the literal provenance contract exactly.\nROLE CONTEXT:\n' + json.dumps(context, ensure_ascii=False, sort_keys=True)
    return 'Fill or repair the candidate using only the supplied role context.\nReturn one complete HTML document. Preserve every source line exactly once with the supplied provenance IDs.\nEvery rendered source leaf must carry data-source-line; unannotated source text is invalid.\nExample: <h2 data-source-line="L0011">SUMMARY —</h2><p data-source-line="L0011">remaining words</p>.\nUse these literal provenance forms: header data-source-block="document"; section data-source-block="block:L0011" data-source-heading="L0011"; record data-source-record="record:L0012".\nNever use a bare line ID as a block or record ID. Put ownership on the container once, not again on its heading.\nUse the template\'s existing .rule element for decorative separators; never fabricate a line ID. Punctuation-only decorative elements (no letters or digits) need no data-source-line annotation.\nSection mapping (owner policy 2026-09-08): fill the TEMPLATE\'s sections first, in the template\'s own order — map each template section to the source section that matches it by meaning, and move that source section\'s lines there. After ALL template sections, append every source section that matched no template section, in original source order, keeping its source heading and style. Never place an appended section between two template sections.\nNever merge a source section\'s lines into an unrelated template section: summary/highlights bullets are not work-experience content and stay their own section.\nContact slots hold only their matching value (phone number in the phone slot, email in the email slot, GitHub URL in the GitHub slot, LinkedIn URL in the LinkedIn slot). If the source has no value for a contact slot, remove that entire contact item — never fill it with unrelated text.\nRender every source line exactly once — never duplicate a line into two places. The candidate_source_blocks map reflects source reading order; you may reassign lines when another template section is a better semantic home.\nWhen you split one source line across several slots (e.g. company vs role), the parts must not overlap: the leaves\' combined words must equal that line exactly once, and every leaf still carries the same line ID. When several slots look similar (e.g. multiple contact slots), fill the value into exactly one of them.\nWithin repeatable records, do not duplicate shared bullets or attach a source line to more than one record.\nProtected items must not regress. Do not modify CSS, stylesheets, inline styles, page geometry, or introduce classes.\nYou may only assign/regroup source lines, clone/remove existing repeatable components, select an existing variant, and remove unused placeholders.\nRemove every unused placeholder; never emit bracketed placeholder text.\nROLE CONTEXT:\n' + json.dumps(context, ensure_ascii=False, sort_keys=True)



def run_builder_repair_loop(
    template: str,
    summary: dict[str, Any],
    source_text: str,
    generate: Callable[[dict[str, Any]], tuple[str, dict[str, int]]],
    build: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, int]]],
    gate: Callable[[int, CandidateState], HardGateResult],
    compare: Callable[..., tuple[VisualComparison, dict[str, int]]],
    *,
    place: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, int]]] | None = None,
    artifact_root: Path | None = None,
    target_evidence: dict[str, str] | None = None,
    evidence_for_round: Callable[[int], dict[str, str]] | None = None,
) -> RefinementResult:
    """Rev-6 action-routed loop. The cached template string is never mutated."""
    base_hash = _text_sha256(template)
    champion = None
    champion_round = None
    champion_gates = None
    champion_rubrics: list[RubricResult] = []
    ledger: dict[str, dict[str, Any]] = {}
    unresolved: dict[str, VisualIssue] = {}
    repair_outcomes: list[dict[str, Any]] = []
    rejected_builder_changes: list[dict[str, Any]] = []
    pending_issue: VisualIssue | None = None
    scratch_draft_html: str | None = None
    failed_state_visits = Counter()
    pending_gate_failures: list[dict[str, Any]] = []
    consecutive_valid_no_promotion = 0
    filler_attempts = 0
    reviewer_checks = 0
    rounds: list[dict[str, Any]] = []
    filler_usage: Counter[str] = Counter()
    builder_usage: Counter[str] = Counter()
    visual_usage: Counter[str] = Counter()
    protected = []
    stop_reason = "max_filler_attempts"
    target_evidence = target_evidence or {"path": "unavailable", "sha256": "unavailable"}
    catalog = build_measurement_catalog(summary)

    def unresolved_rows() -> list[dict[str, Any]]:
        return [
            {
                "issue_fingerprint": fingerprint,
                **issue.model_dump(mode="json"),
                "attempts": ledger.get(fingerprint, {}).get("outcomes", [])[-8:],
            }
            for fingerprint, issue in unresolved.items()
        ]

    def save_run_state(round_dir: Path | None = None) -> None:
        state = {
            "schema_version": "a-pipeline-run-state/1",
            "stop_reason": stop_reason,
            "champion": {
                "round": champion_round,
                "template_sha256": champion.template_sha256 if champion else None,
                "html_sha256": _text_sha256(champion.filled_html) if champion else None,
            },
            "unresolved_issues": unresolved_rows(),
            "change_history": repair_outcomes,
            "protected_results": protected,
        }
        _write_json(artifact_root / "run_state.json" if artifact_root else None, state)
        _write_json(round_dir / "run_state.json" if round_dir else None, state)

    def html_change(kind: str, before: str, after: str, round_dir: Path | None) -> dict[str, Any]:
        diff = "".join(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before.html",
            tofile="after.html",
        ))
        diff_path = round_dir / "change.diff" if round_dir else None
        if diff_path:
            diff_path.write_text(diff, encoding="utf-8")
        return {
            "kind": kind,
            "before_sha256": _text_sha256(before),
            "after_sha256": _text_sha256(after),
            "diff_path": str(diff_path) if diff_path else None,
            "diff_excerpt": "\n".join(diff.splitlines()[:120]),
        }

    for round_number in range(1, 31):
        round_dir = artifact_root / f"round_{round_number}" if artifact_root else None
        if round_dir:
            round_dir.mkdir(parents=True, exist_ok=True)
        base = champion or CandidateState(template, template, base_hash, [])
        if champion is None and scratch_draft_html is not None:
            routed = ("Filler", "scratch_repair")
        else:
            routed = route_issue(pending_issue, ledger) if pending_issue else ("Filler", "initial_fill")
        if routed is None:
            stop_reason = "same_actor_action_failed_four_times"
            break
        actor, action = routed
        resolved = (
            resolve_measurements(pending_issue, catalog, required=actor == "Builder" and action in BUILDER_ACTIONS)
            if pending_issue
            else []
        )
        fingerprint = issue_fingerprint(pending_issue) if pending_issue else None
        ledger_row = ledger.get(fingerprint, {}) if fingerprint else {}
        champion_evidence = evidence_for_round(champion_round) if evidence_for_round and champion_round else None
        _write_json(
            round_dir / "selected_issue.json" if round_dir else None,
            pending_issue.model_dump(mode="json") if pending_issue else None,
        )
        _write_json(round_dir / "resolved_measurements.json" if round_dir else None, resolved)
        operation_record = None
        change_record = None
        if actor == "Filler":
            if action == "scratch_repair":
                context = build_scratch_repair_context(
                    scratch_draft_html=scratch_draft_html,
                    issue=pending_issue,
                    source_text=source_text,
                    prior_outcomes=ledger_row.get("outcomes", []),
                    unresolved_issues=unresolved_rows(),
                    change_history=repair_outcomes,
                )
            else:
                context = build_filler_context(
                    source_text=source_text,
                    template_html=base.template_html,
                    champion_filled_html=champion.filled_html if champion else "",
                    issue=pending_issue,
                    action=action,
                    resolved_measurements=resolved,
                    target_evidence=target_evidence,
                    champion_evidence=champion_evidence,
                    protected_rubrics=protected,
                    gate_failures=pending_gate_failures,
                    same_issue_outcomes=ledger_row.get("outcomes", []),
                    repair_outcomes=repair_outcomes,
                    unresolved_issues=unresolved_rows(),
                )
            _write_json(round_dir / "filler_context.json" if round_dir else None, context)
            if champion is None:
                filler_attempts += 1
                filled_html, usage = generate(context)
                filler_usage.update(usage)
                conformance_errors = validate_filler_conformance(base.template_html, filled_html)
                if action == "scratch_repair":
                    conformance_errors.extend(validate_scratch_repair(scratch_draft_html, filled_html, pending_issue))
                challenger = CandidateState(base.template_html, filled_html, base.template_sha256, list(base.builder_changes))
                change_record = html_change(
                    "full_html_fill" if action == "initial_fill" else "scratch_html_repair",
                    scratch_draft_html if action == "scratch_repair" else base.filled_html,
                    filled_html,
                    round_dir,
                )
                gate_result = (
                    HardGateResult(passed=False, failures=[GateFailure(code="template_conformance", details=conformance_errors)])
                    if conformance_errors
                    else gate(round_number, challenger)
                )
            else:
                filler_attempts += 1
                filled_html, usage = generate(context)
                filler_usage.update(usage)
                conformance_errors = validate_filler_conformance(champion.template_html, filled_html)
                challenger = CandidateState(champion.template_html, filled_html, champion.template_sha256, list(champion.builder_changes))
                change_record = html_change("candidate_html_repair", champion.filled_html, filled_html, round_dir)
                gate_result = (
                    HardGateResult(passed=False, failures=[GateFailure(code="template_conformance", details=conformance_errors)])
                    if conformance_errors
                    else gate(round_number, challenger)
                )
        else:
            if champion is None:
                raise RuntimeError("Builder repair requires a hard-gate-valid champion")
            context = build_builder_context(
                issue=pending_issue,
                action=action,
                resolved_measurements=resolved,
                champion=champion,
                target_evidence=target_evidence,
                champion_evidence=champion_evidence or target_evidence,
                protected_rubrics=protected,
                same_issue_operations=[row for row in ledger_row.get("outcomes", []) if row.get("actor") == "Builder"],
                unresolved_issues=unresolved_rows(),
                change_history=repair_outcomes,
            )
            _write_json(round_dir / "builder_context.json" if round_dir else None, context)
            if round_dir:
                round_dir.joinpath("template_before.html").write_text(champion.template_html, encoding="utf-8")
            invalid_attempts = []
            for _attempt in range(3):
                payload, usage = build(context)
                builder_usage.update(usage)
                try:
                    operation = validate_builder_operation(payload, champion.template_html, resolved, action)
                    challenger = apply_builder_operation(champion, operation, resolved)
                    operation_record = {
                        **challenger.builder_changes[-1],
                        "issue_fingerprint": fingerprint,
                        "round": round_number,
                        "promotion_result": None,
                    }
                    challenger.builder_changes[-1] = operation_record
                    gate_result = gate(round_number, challenger)
                    break
                except (ValueError, TypeError) as error:
                    invalid_attempts.append({"payload": payload, "validation_error": str(error)})
                    context["invalid_operation_attempts"] = invalid_attempts
            challenger = champion
            last = invalid_attempts[-1]
            operation_record = {**last, "issue_fingerprint": fingerprint, "round": round_number, "promotion_result": False}
            gate_result = HardGateResult(passed=False, failures=[GateFailure(code="invalid_builder_operation", details=last["validation_error"])])
            change_record = {"kind": "builder_operation", "operation": payload, "invalid_attempts": invalid_attempts}
            _write_json(round_dir / "builder_patch.json" if round_dir else None, change_record)
            if round_dir:
                round_dir.joinpath("template_after.html").write_text(challenger.template_html, encoding="utf-8")
        if round_dir:
            round_dir.joinpath("challenger.html").write_text(challenger.filled_html, encoding="utf-8")
        _write_json(round_dir / "hard_gates.json" if round_dir else None, gate_result.model_dump(mode="json"))
        record = {
            "round": round_number,
            "actor": actor,
            "action": action,
            "selected_issue_fingerprint": fingerprint,
            "hard_gates": gate_result.model_dump(mode="json"),
            "promoted": False,
        }
        rounds.append(record)
        if not gate_result.passed:
            pending_gate_failures = [failure.model_dump(mode="json") for failure in gate_result.failures]
            outcome = {
                "round": round_number,
                "actor": actor,
                "action": action,
                "change": change_record,
                "gate_passed": False,
                "promoted": False,
                "summary": "hard gates failed",
            }
            repair_outcomes.append(outcome)
            gate_issues = [gate_failure_issue(failure) for failure in _consolidate_gate_failures(gate_result.failures)]
            if actor == "Builder" and any(
                failure.code == "invalid_builder_operation"
                for failure in gate_result.failures
            ):
                gate_issues = [pending_issue] if pending_issue else gate_issues
            current_gate_fingerprints = {issue_fingerprint(issue) for issue in gate_issues}
            for prior_fingerprint, prior_issue in list(unresolved.items()):
                if prior_issue.region == "deterministic_gate" and prior_fingerprint not in current_gate_fingerprints:
                    unresolved.pop(prior_fingerprint)
            for issue in gate_issues:
                unresolved[issue_fingerprint(issue)] = issue
            if action == "initial_fill":
                for issue in gate_issues:
                    update_issue_ledger(ledger, issue, round_number, resolved_measurements=[])
            else:
                update_issue_ledger(
                    ledger,
                    pending_issue or gate_issues[0],
                    round_number,
                    actor=actor,
                    action=action,
                    resolved_measurements=[],
                    gate_result=False,
                    promoted=False,
                    outcome=outcome["summary"],
                )
            pending_issue = select_issue(gate_issues)
            record["gate_issue_fingerprints"] = [issue_fingerprint(issue) for issue in gate_issues]
            record["selected_next_issue"] = pending_issue.model_dump(mode="json") if pending_issue else None
            if operation_record:
                rejected_builder_changes.append(
                    {**operation_record, "issue_fingerprint": fingerprint, "round": round_number, "promotion_result": False, "rejection_reason": "hard gates failed"}
                )
            _write_json(round_dir / "visual_comparison.json" if round_dir else None, {"skipped": "hard_gates_failed"})
            _write_json(round_dir / "issue_ledger.json" if round_dir else None, ledger)
            save_run_state(round_dir)
            failed_state = json.dumps(
                {
                    "template_sha256": challenger.template_sha256,
                    "html_sha256": _text_sha256(challenger.filled_html),
                    "failures": gate_result.model_dump(mode="json")["failures"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            failed_state_visits[failed_state] += 1
            if failed_state_visits[failed_state] >= 3:
                stop_reason = "repeated_identical_failed_state"
                break
            if champion is None:
                repairable = {
                    "source_block_split", "invented_content", "section_semantic_mismatch", "header_semantic_mismatch", "source_block_assignment", "missing_html_content", "unknown_source_line", "missing_source_content", "invalid_source_block_identity", "duplicated_source_content", "document_has_no_section_heading", "missing_source_heading", "source_record_assignment", "slot_semantic_mismatch", "duplicate_source_block", "layout_render_2", "layout_render_1", "missing_pdf_content", "nested_source_annotations", "source_reading_order",
                }
                repairable_issues = [issue for issue in gate_issues if issue.dimension in repairable and issue.evidence != "null"]
                if repairable_issues:
                    pending_issue = select_issue(repairable_issues)
                    record["selected_next_issue"] = pending_issue.model_dump(mode="json") if pending_issue else None
                    scratch_draft_html = challenger.filled_html
                    continue
                stop_reason = "initial_fill_failed_hard_gates" if action == "initial_fill" else "fatal_scratch_repair_failure"
                break
            continue
        pending_gate_failures = []
        for prior_fingerprint, prior_issue in list(unresolved.items()):
            if re.sub(r"[^a-z]+", "_", prior_issue.region.casefold()).strip("_") == "deterministic_gate":
                unresolved.pop(prior_fingerprint)
        challenger_evidence = evidence_for_round(round_number) if evidence_for_round else target_evidence
        reviewer_context = build_reviewer_context(
            target_evidence=target_evidence,
            champion_evidence=champion_evidence,
            challenger_evidence=challenger_evidence,
            format_summary=summary,
            hard_gates=gate_result,
            protected_rubrics=protected,
            issue_ledger=ledger,
            repair_outcomes=repair_outcomes,
            focus_issue=pending_issue,
            source_text=source_text,
            unresolved_issues=unresolved_rows(),
        )
        reviewer_context["reviewer_attempts_remaining"] = 12 - reviewer_checks
        _write_json(round_dir / "reviewer_context.json" if round_dir else None, reviewer_context)
        try:
            visual, usage = compare(champion_round, round_number, False, [], repair_outcomes, reviewer_context)
        except PipelineCallError as error:
            reviewer_checks += error.attempts
            record["reviewer_failure"] = {"kind": error.kind, "attempts": error.attempts, "message": str(error)}
            stop_reason = f"reviewer_{error.kind}_failure"
            break
        reviewer_checks += int(usage.get("attempts", 1))
        visual_usage.update(usage)
        record["review_number"] = reviewer_checks
        record["visual_comparison"] = visual.model_dump(mode="json")
        expected = "challenger" if (champion is None or visual.preference == "challenger_better") else "champion"
        if visual.retained_champion != expected:
            raise RuntimeError(f"Reviewer retained {visual.retained_champion}; expected {expected}")
        if champion is not None and visual.confidence == "low" and reviewer_checks < 12:
            reviewer_context["reviewer_attempts_remaining"] = 12 - reviewer_checks
            try:
                reversed_visual, usage = compare(champion_round, round_number, True, [], repair_outcomes, reviewer_context)
            except PipelineCallError as error:
                reviewer_checks += error.attempts
                record["reversed_reviewer_failure"] = {"kind": error.kind, "attempts": error.attempts, "message": str(error)}
                stop_reason = f"reviewer_{error.kind}_failure"
                break
            reviewer_checks += int(usage.get("attempts", 1))
            visual_usage.update(usage)
            record["reversed_review_number"] = reviewer_checks
            record["reversed_visual_comparison"] = reversed_visual.model_dump(mode="json")
            if reversed_visual.preference != visual.preference:
                visual = VisualComparison(
                    preference="tie",
                    confidence="low",
                    retained_champion="champion",
                    retained_champion_acceptable=False,
                    retained_champion_issues=reversed_visual.retained_champion_issues,
                    regressions=[*visual.regressions, *reversed_visual.regressions],
                    rubric_results=reversed_visual.rubric_results,
                )
                record["resolved_visual_comparison"] = visual.model_dump(mode="json")
        protected_ids = {row["rubric_id"] for row in protected}
        protected_regression = (
            champion is not None
            or bool(visual.regressions)
            or any(
                result.rubric_id in protected_ids and result.status != "pass"
                for result in visual.rubric_results
            )
        )
        promoted = (champion is not None or visual.preference == "challenger_better") and not protected_regression
        record["protected_regression"] = protected_regression
        if promoted:
            if operation_record:
                operation_record["promotion_result"] = True
            champion, champion_round, champion_gates = challenger, round_number, gate_result
            champion_rubrics = visual.rubric_results
            consecutive_valid_no_promotion = 0
        else:
            if operation_record:
                operation_record["promotion_result"] = False
            consecutive_valid_no_promotion += 1
        record["promoted"] = promoted
        record["retained_champion_round"] = champion_round
        routed_rows = []
        for issue_item in visual.retained_champion_issues:
            routed = route_issue(issue_item, ledger)
            actor_next, action_next = routed if routed else ("stopped", "")
            routed_rows.append(
                {
                    **issue_item.model_dump(mode="json"),
                    "issue_fingerprint": issue_fingerprint(issue_item),
                    "route": actor_next,
                    "selected_action": action_next,
                    "routing_reason": "deterministic suggested-action routing",
                }
            )
        record["feedback_routing"] = routed_rows
        outcome = {
            "round": round_number,
            "actor": actor,
            "action": action,
            "change": change_record,
            "gate_passed": True,
            "promoted": promoted,
            "summary": "promoted" if promoted else "valid challenger not promoted",
        }
        repair_outcomes.append(outcome)
        if pending_issue:
            update_issue_ledger(
                ledger,
                pending_issue,
                round_number,
                actor=actor,
                action=action,
                resolved_measurements=resolved,
                gate_result=True,
                promoted=promoted,
                outcome=outcome["summary"],
            )
        if operation_record and not promoted:
            rejected_builder_changes.append(
                {**operation_record, "issue_fingerprint": fingerprint, "round": round_number, "promotion_result": False, "rejection_reason": "reviewer retained champion"}
            )
        unresolved = {issue_fingerprint(issue_item): issue_item for issue_item in visual.retained_champion_issues}
        protected = build_protected_rubrics(champion_gates, champion_rubrics)
        _write_json(round_dir / "issue_ledger.json" if round_dir else None, ledger)
        save_run_state(round_dir)
        if visual.retained_champion_acceptable and not unresolved:
            stop_reason = "acceptable_champion"
            break
        if consecutive_valid_no_promotion >= 4:
            stop_reason = "four_valid_non_promotions"
            break
        pending_issue = select_issue(list(unresolved.values()))
        if pending_issue is None:
            stop_reason = "no_actionable_issue"
            break
        resolved_next = resolve_measurements(pending_issue, catalog, required=pending_issue.suggested_action in BUILDER_ACTIONS)
        update_issue_ledger(ledger, pending_issue, round_number, resolved_measurements=resolved_next)
        record["selected_next_issue"] = pending_issue.model_dump(mode="json")
        if reviewer_checks >= 12:
            stop_reason = "max_reviewer_checks"
            break
    promoted_changes = champion.builder_changes if champion else []
    changes = {
        "base_template_hash": base_hash,
        "promoted_operations": promoted_changes,
        "rejected_history": rejected_builder_changes,
        "final_candidate_template_hash": champion.template_sha256 if champion else None,
        "persistent_cache_replaced": False,
    }
    if artifact_root:
        _write_json(artifact_root / "builder_changes.json", changes)
        save_run_state()
        if champion:
            artifact_root.joinpath("candidate_template.html").write_text(champion.template_html, encoding="utf-8")
    return RefinementResult(
        champion_round=champion_round,
        champion_html=champion.filled_html if champion else None,
        stop_reason=stop_reason if (champion or stop_reason != "max_filler_attempts") else "no_valid_candidate_within_safety_limit",
        rounds=rounds,
        filler_usage=dict(filler_usage),
        visual_usage=dict(visual_usage),
        filler_attempts=filler_attempts,
        reviewer_checks=reviewer_checks,
        champion_state=champion,
        builder_usage=dict(builder_usage),
        builder_changes=changes,
    )



def _html_text(document: str) -> tuple[str, list[str]]:
    parser = _TextParser()
    parser.feed(document)
    text = html_lib.unescape(" ".join(parser.parts))
    empty = [
        str(section["name"])
        for section in parser.sections
        if not re.sub(r"\s+", "", " ".join(section["parts"]))
    ]
    return re.sub(r"\s+", " ", text).strip(), empty



def _normalized_text(value: str) -> str:
    # Owner rule 2026-09-06: colons are presentation (target style 'Label: items'),
    # not candidate content — source extraction loses them and Filler may add them.
    value = value.replace(":", " ")
    return re.sub(r"\s+", " ", re.sub(r"^[^\w]+", "", value)).strip().casefold()



_FONTAWESOME_ASSETS = ROOT / "tests/experiments/assets/fontawesome"
_FA_FONTS = {
    "Font Awesome 6 Free": ("fa-solid-900.woff2", "font-weight: 900"),
    "Font Awesome 6 Brands": ("fa-brands-400.woff2", "font-weight: 1 900"),
}
_FA_ICONS = {
    "phone": ("Font Awesome 6 Free", "\uf095"),
    "envelope": ("Font Awesome 6 Free", "\uf0e0"),
    "email": ("Font Awesome 6 Free", "\uf0e0"),
    "github": ("Font Awesome 6 Brands", "\uf09b"),
    "linkedin": ("Font Awesome 6 Brands", "\uf08c"),
    "location": ("Font Awesome 6 Free", "\uf3c5"),
}
_FA_CODEPOINTS = {
    "phone": _FA_ICONS["phone"],
    "envelope": _FA_ICONS["envelope"],
    "location-dot": _FA_ICONS["location"],
    "github": _FA_ICONS["github"],
    "linkedin": _FA_ICONS["linkedin"],
    "briefcase": ("Font Awesome 6 Free", "\uf0b1"),
    "globe": ("Font Awesome 6 Free", "\uf0ac"),
}



def _normalize_provenance_annotations(candidate_html: str, source_text: str) -> str:
    """Owner decision 2026-09-08 (E->F postmortem): annotation bookkeeping is
    auto-fixed, never a reason to block the Reviewer. Mechanically safe fixes:
    - a data-source-line id that names no source line is fabricated — strip the
      attribute (the remaining text is still policed by the invented-content
      and coverage gates);
    - a bare line id used as a section block (data-source-block="L0049") gets
      its canonical "block:" prefix back."""
    known_lines = set(source_lines(source_text))
    try:
        tree = lxml_html.document_fromstring(candidate_html)
    except Exception:
        return candidate_html

    changed = False
    for element in tree.xpath("//*[@data-source-line]"):
        line_id = element.get("data-source-line") or ""
        if line_id not in known_lines:
            del element.attrib["data-source-line"]
            changed = True
    for element in tree.xpath("//*[@data-source-block]"):
        block_id = element.get("data-source-block") or ""
        match = re.fullmatch(r"(L\d{4})", block_id)
        if not match:
            continue
        element.set("data-source-block", f"block:{block_id}")
        changed = True
    if not changed:
        return candidate_html
    return etree.tostring(tree, encoding="unicode", method="html")



def _normalize_section_order(candidate_html: str, base_template_html: str, source_text: str) -> str:
    """Owner policy 2026-09-08: the template's sections come first, in the
    template's own order; every other (derived) section is appended after, in
    the original resume's order. Deterministic DOM reordering — the prompt
    asks the model for this order, code guarantees it."""
    template_sections = re.findall(r'data-section="([^"]+)"', base_template_html)
    if not template_sections:
        return candidate_html
    try:
        tree = lxml_html.document_fromstring(candidate_html)
    except (lxml_html.ParseError if hasattr(lxml_html, "ParseError") else ValueError, ValueError):
        return candidate_html

    changed = False
    lines = source_lines(source_text)
    template_semantics = {name: _section_semantic(name) for name in template_sections}
    parents = {element.getparent() for element in tree.xpath('//section[@data-source-block or @data-section]')}
    for parent in parents:
        sections = [el for el in parent if isinstance(el.tag, str) and el.tag == "section"]
        if len(sections) < 2:
            continue

        def source_index(element: Any) -> int:
            ids = [int(value[1:]) for value in element.xpath('.//*[@data-source-line]/@data-source-line') if value[1:].isdigit()]
            return min(ids) if ids else 1000000

        assignments = {}
        used = set()
        for element in sorted(sections, key=source_index):
            name = element.get("data-section")
            heading_id = element.get("data-source-heading")
            semantic = (
                _section_semantic(lines.get(heading_id, ""))
                if heading_id
                else None
            )
            candidates = [
                candidate
                for candidate in template_sections
                if candidate not in used and template_semantics.get(candidate) == semantic
            ]
            assigned = (
                name
                if name in template_sections and semantic is None
                else name if name in candidates
                else candidates[0] if candidates
                else None
            )
            if assigned:
                assignments[element] = assigned
                used.add(assigned)
                if name != assigned:
                    element.set("data-section", assigned)
                    changed = True
            elif name:
                continue
            else:
                derived = re.sub(r"[^a-z0-9]+", "-", lines.get(heading_id, "").casefold()).strip("-")
                element.set("data-section", derived or "derived")
                changed = True

        def order_key(element: Any) -> tuple[int, int]:
            assigned = assignments.get(element)
            if assigned:
                return (0, template_sections.index(assigned))
            return (1, source_index(element))

        ordered = sorted(sections, key=order_key)
        if ordered == sections:
            continue
        index = min(parent.index(element) for element in sections)
        for element in sections:
            parent.remove(element)
        for offset, element in enumerate(ordered):
            parent.insert(index + offset, element)
        changed = True
    if changed:
        return etree.tostring(tree, encoding="unicode", method="html")
    return candidate_html



def _strip_unauthorized_classes(candidate_html: str, base_template_html: str) -> str:
    """Owner decision 2026-09-08: undeclared classes are visually inert (no CSS
    rule, no JS) — strip them instead of spending a repair round. fa-* icon
    semantics follow the template convention and are always allowed."""
    allowed = _class_names(base_template_html) | _declared_class_names(base_template_html)

    def clean(match: re.Match[str]) -> str:
        names = match.group(1).split()
        kept = [name for name in names if name in allowed or name.startswith("fa-")]
        return f'class="{" ".join(kept)}"' if kept else ""

    return re.sub(r'class="([^"]*)"', clean, candidate_html)



def _restore_template_presentation(candidate_html: str, base_template_html: str) -> str:
    """Owner decision 2026-09-08: CSS/asset authority is the frozen template.
    Every presentation deviation (modified style blocks, stylesheet links,
    unauthorized inline styles or classes) is void — code restores the
    template's presentation instead of spending a model round, and the full
    hard-gate suite re-verifies content afterwards."""
    candidate_html = _strip_unauthorized_inline_styles(candidate_html, base_template_html)
    candidate_html = _strip_unauthorized_classes(candidate_html, base_template_html)
    template_styles = _style_blocks(base_template_html)
    candidate_styles = _style_blocks(candidate_html)
    if template_styles and template_styles != candidate_styles:
        candidate_html = re.sub(r"<style\b[^>]*>.*?</style>", "", candidate_html, flags=re.I | re.S)
        joined = "".join(f"<style>{content}</style>" for content in template_styles)
        if re.search(r"</head>", candidate_html, re.I):
            candidate_html = re.sub(r"</head>", lambda m: joined + "\n</head>", candidate_html, count=1, flags=re.I)
        else:
            candidate_html = joined + candidate_html
    if re.findall(r"<link\b[^>]*rel=[\"']?stylesheet[^>]*>", candidate_html, re.I) != re.findall(
        r"<link\b[^>]*rel=[\"']?stylesheet[^>]*>", base_template_html, re.I
    ):
        candidate_links = "".join(re.findall(r"<link\b[^>]*rel=[\"']?stylesheet[^>]*>", base_template_html, re.I))
        candidate_html = re.sub(r"<link\b[^>]*rel=[\"']?stylesheet[^>]*>\s*", "", candidate_html, flags=re.I)
        if re.search(r"</head>", candidate_html, re.I):
            candidate_html = re.sub(r"</head>", lambda m: candidate_links + "\n</head>", candidate_html, count=1, flags=re.I)
        else:
            candidate_html = candidate_links + candidate_html
    return candidate_html



def _strip_unauthorized_inline_styles(candidate_html: str, template_html: str) -> str:
    """Owner rule 2026-09-08: the frozen template is the sole style authority,
    so Filler-authored inline styles are void — code deletes them instead of
    spending a paid repair round. Only inline styles that appear verbatim in
    the template survive."""
    allowed = set(re.findall(r"\sstyle=[\"'][^\"']*[\"']", template_html, re.I))
    return re.sub(
        r"\sstyle=[\"'][^\"']*[\"']",
        lambda match: match.group(0) if match.group(0) in allowed else "",
        candidate_html,
        flags=re.I,
    )



def _inject_icon_fonts(filled_html: str) -> str:
    """Deterministic icon rendering (owner rule 2026-09-06): embed local
    Font Awesome @font-face rules and replace contact icon glyph placeholders
    with the real codepoints. Runs AFTER Filler and BEFORE Chrome rendering; the
    AI never sees the font payload and never picks glyph codepoints.
    Owner fix 2026-09-08: the Builder template now uses <i class="fa-solid
    fa-…"> icon elements instead of .icon spans, so injection also triggers on
    fa-* classes and fills known codepoints there (an unmatched fa-name renders
    nothing)."""
    has_icon_spans = 'class="icon"' in filled_html
    has_fa_elements = bool(re.search(r'class="[^"]*fa-(?!solid\b|brands\b)[a-z0-9-]+', filled_html))
    if not has_icon_spans and not has_fa_elements:
        return filled_html
    faces: list[str] = []
    for family, (filename, weight) in _FA_FONTS.items():
        path = _FONTAWESOME_ASSETS / filename
        if not path.is_file():
            continue
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        faces.append(
            f"@font-face {{ font-family: '{family}'; src: url(data:font/woff2;base64,{data}) format('woff2'); {weight}; font-style: normal; font-display: block; }}"
        )
    if not faces:
        return filled_html
    html = filled_html
    # Extend the --font-icon stack so the real fonts win over fallbacks.
    html = re.sub(
        r"--font-icon:[^;]+;",
        "--font-icon: 'Font Awesome 6 Free', 'Font Awesome 6 Brands', 'Font Awesome 5 Free', 'Font Awesome', sans-serif;",
        html,
        count=1,
    )
    html = re.sub(
        r"font-family:\s*(['\"])Font Awesome\1\s*;",
        "font-family: 'Font Awesome 6 Free', 'Font Awesome 6 Brands', sans-serif;",
        html,
        flags=re.I,
    )
    html = re.sub(r"</style>", "\n" + "\n".join(faces) + "\n</style>", html, count=1)

    def replace_icon(match: re.Match[str]) -> str:
        block = match.group(0)
        item_match = re.search(r'contact-item ([a-zA-Z-]+)', block)
        icon = _FA_ICONS.get(item_match.group(1).casefold()) if item_match else None
        if not icon:
            return block
        family, codepoint = icon
        return re.sub(
            r'(<span class="icon"[^>]*>)[^<]*(</span>)',
            lambda m: f'{m.group(1)}{codepoint}{m.group(2)}',
            block,
            count=1,
        )

    # Replace the whole contact-item block so class context is available.
    html = re.sub(r'<div class="contact-item [^"]*">.*?</div>', replace_icon, html, flags=re.S)

    # <i class="fa-solid fa-…"> elements (Builder convention 2026-09-08): fill
    # the real codepoint for known names; unknown names render nothing, which
    # is harmless — but the fonts must be embedded for the known ones.
    def replace_fa_element(match: re.Match[str]) -> str:
        block = match.group(0)
        name_match = re.search(r'fa-(?!solid\b|brands\b)([a-z0-9-]+)', block)
        icon = _FA_CODEPOINTS.get(name_match.group(1)) if name_match else None
        if not icon:
            return block
        if re.search(rf'\.fa-{re.escape(name_match.group(1))}\s*::?before\s*\{{', html):
            return block
        _family, codepoint = icon
        return re.sub(
            r'(<i\b[^>]*>)[^<]*(</i>)',
            lambda m: f'{m.group(1)}{codepoint}{m.group(2)}',
            block,
            count=1,
        )

    html = re.sub(r"<i\b[^>]*class=[\"'][^\"']*fa-[^\"']*[\"'][^>]*>.*?</i>", replace_fa_element, html, flags=re.S)
    return html



_REDACTION_PATTERNS = (
    re.compile(r"\[redacted[^\]]*\]", re.I),
    re.compile(r"web\s*copy", re.I),
    re.compile(r"\(cid:\d+\)"),
)



def _is_redaction_placeholder(fragment: str) -> bool:
    """True for PDF redaction artifacts (e.g. '[redacted - web copy]', cid
    placeholders) that are NOT candidate content and must not be required in
    generated output (owner ruling 2026-09-06)."""
    if any(pattern.search(fragment) for pattern in _REDACTION_PATTERNS):
        return True
    return not any(character.isalnum() for character in fragment)



def _source_fragments(source_text: str) -> list[str]:
    return [
        fragment.strip()
        for line in source_text.splitlines()
        for fragment in line.split(" | ")  # DOCX table-cell separator added by read_docx.
        if fragment.strip()
        and any(character.isalnum() for character in fragment)
        and not _is_redaction_placeholder(fragment)
    ]



def _missing_occurrences(source_text: str, document_text: str) -> list[str]:
    normalized_document = _normalized_text(document_text)
    fragments = _source_fragments(source_text)
    expected = Counter(_normalized_text(fragment) for fragment in fragments)
    observed = {fragment: normalized_document.count(fragment) for fragment in expected}
    remaining = Counter({fragment: count - observed[fragment] for fragment, count in expected.items() if count > observed[fragment]})
    missing: list[str] = []
    for fragment in fragments:
        normalized = _normalized_text(fragment)
        if remaining[normalized] > 0:
            missing.append(fragment)
            remaining[normalized] -= 1
    return missing



def _missing_source_tokens(source_text: str, document_text: str) -> list[str]:
    expected = Counter(re.findall(r"\w+", " ".join(_source_fragments(source_text)).casefold()))
    observed = Counter(re.findall(r"\w+", document_text.casefold()))
    return sorted((expected - observed).elements())



def _hyphen_free(value: str) -> str:
    """Owner decision 2026-09-09 (F->E matrix postmortem): Chrome breaks words at
    hyphens across lines ("Scikit-" / "learn") and read_pdf_text merges the
    fragments WITHOUT the hyphen ("Scikitlearn"), so hyphen-bearing source lines
    are permanently reported missing from the PDF even though every glyph
    rendered. PDF-side coverage therefore compares hyphen-free text on both
    sides; the HTML-side coverage stays strict (verbatim, hyphens included)."""
    return value.replace("-", "")



def _covered(fragment: str, document_text: str) -> bool:
    normalized = _normalized_text(fragment)
    return bool(normalized) and normalized in _normalized_text(document_text)



def assert_verbatim_source_in_html(document: str, source_text: str) -> None:
    rendered, _ = _html_text(document)
    missing = _missing_occurrences(source_text, rendered)
    if missing:
        raise RuntimeError(f"Filler altered or omitted {len(missing)} source lines: {missing[:5]}")



def l1_check(
    pdf_path: Path,
    filled_html: str,
    source_text: str = "",
    margins: dict[str, float | None] | None = None,
    allow_slot_splitting: bool = False,
) -> dict[str, Any]:
    _, empty_sections = _html_text(filled_html)
    pages: list[dict[str, Any]] = []
    overlaps: list[dict[str, Any]] = []
    margin_violations: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            words = page.extract_words()
            bounds = {
                "left": min((float(word["x0"]) for word in words), default=None),
                "top": min((float(word["top"]) for word in words), default=None),
                "right": float(page.width) - max((float(word["x1"]) for word in words), default=float(page.width)),
                "bottom": float(page.height) - max((float(word["bottom"]) for word in words), default=float(page.height)),
            }
            utilization = 0.0 if not words else (max(float(word["bottom"]) for word in words) - min(float(word["top"]) for word in words)) / float(page.height)
            sparse_extra = page_number > 1 and (len(words) < 20 or utilization < .20)
            pages.append({"page": page_number, "word_count": len(words), "blank": not words, "sparse_extra": sparse_extra, "vertical_utilization": _round(utilization), "visible_bounds_pt": bounds})
            if margins and words:
                for side in ("left", "top", "right"):
                    if margins.get(side) is not None and bounds[side] is not None and bounds[side] + 2 < margins[side]:
                        margin_violations.append({"page": page_number, "side": side, "observed_pt": _round(bounds[side]), "minimum_pt": margins[side]})
            for index, left in enumerate(words):
                for right in words[index + 1 :]:
                    width = min(float(left["x1"]), float(right["x1"])) - max(float(left["x0"]), float(right["x0"]))
                    height = min(float(left["bottom"]), float(right["bottom"])) - max(float(left["top"]), float(right["top"]))
                    if width <= 1 or height <= 1:
                        continue
                    intersection = width * height
                    smaller = min(
                        (float(left["x1"]) - float(left["x0"])) * (float(left["bottom"]) - float(left["top"])),
                        (float(right["x1"]) - float(right["x0"])) * (float(right["bottom"]) - float(right["top"])),
                    )
                    if smaller and intersection / smaller > 0.2:
                        overlaps.append({"page": page_number, "left": left["text"], "right": right["text"]})
    pdf_text = read_pdf_text(pdf_path)
    normalized_pdf = re.sub(r"\s+", " ", pdf_text).casefold()
    pdf_source = _hyphen_free(source_text)
    missing_pdf_fragments = (
        _missing_source_tokens(pdf_source, _hyphen_free(normalized_pdf))
        if allow_slot_splitting else _missing_occurrences(pdf_source, _hyphen_free(normalized_pdf))
    )
    passed = (
        bool(pages)
        and not any(page["blank"] for page in pages)
        and not overlaps
        and not empty_sections
        and not missing_pdf_fragments
        and not margin_violations
    )
    return {
        "passed": passed,
        "pages": pages,
        "overlaps": overlaps,
        "empty_sections": empty_sections,
        "missing_pdf_fragments": missing_pdf_fragments,
        "margin_violations": margin_violations,
        "overflow": "PASS" if not missing_pdf_fragments else "FAIL: source fragments absent after PDF export",
    }



def _font_names(pdf_path: Path) -> list[str]:
    with pdfplumber.open(pdf_path) as pdf:
        return sorted({str(char.get("fontname") or "") for page in pdf.pages for char in page.chars if char.get("fontname")})



def _per_line_edges(pdf: Path) -> list[tuple[int, float, float, float]]:
    """Per-line geometry (page, top, x0, x1) for cross-render comparison."""
    with pdfplumber.open(pdf) as document:
        edges = []
        for page_number, page in enumerate(document.pages, 1):
            rows: dict[int, list[tuple[float, float]]] = {}
            for word in page.extract_words():
                rows.setdefault(round(word["top"], 1), []).append((float(word["x0"]), float(word["x1"])))
            for top, spans in sorted(rows.items()):
                edges.append((page_number, top, min(x0 for x0, _ in spans), max(x1 for _, x1 in spans)))
        return edges


def _line_stability(first: Path, second: Path, tolerance_pt: float = 1.0) -> dict[str, Any]:
    """Owner ruling 2026-09-10 (E→F thirteenth freeze): the double-render
    stability gate upgrades from page-level stats to per-line geometry —
    page-level checks (word counts, utilization) cannot see per-line shifts
    of a few points, which is exactly how the font-timing nondeterminism
    slipped through."""
    first_lines, second_lines = _per_line_edges(first), _per_line_edges(second)
    if len(first_lines) != len(second_lines):
        return {"passed": False, "reason": "line count differs between renders",
                "first": len(first_lines), "second": len(second_lines)}
    worst = 0.0
    for (fp, ftop, fx0, fx1), (sp, stop, sx0, sx1) in zip(first_lines, second_lines):
        if fp != sp or abs(ftop - stop) > 1.5:
            return {"passed": False, "reason": "line layout differs between renders",
                    "first": (fp, ftop), "second": (sp, stop)}
        worst = max(worst, abs(fx0 - sx0), abs(fx1 - sx1))
    return {"passed": worst <= tolerance_pt, "lines_compared": len(first_lines),
            "max_line_delta_pt": round(worst, 3), "tolerance_pt": tolerance_pt}


def independent_checks(source: Path, filled_html: str, generated: Path, l1: dict[str, Any], target: Path | None = None) -> dict[str, Any]:
    generated_fonts = _font_names(generated)
    css_vars: dict[str, str] = {}
    for var_name, var_value in re.findall(r"(--[a-zA-Z0-9_-]+)\s*:\s*([^;}]+)", filled_html):
        css_vars[var_name] = var_value

    def _expand_var(value: str) -> str:
        return re.sub(
            r"var\(\s*(--[a-zA-Z0-9_-]+)\s*\)",
            lambda m: css_vars.get(m.group(1), ""),
            value,
        )

    requested = sorted({
        families[0]
        for declaration in re.findall(r"font-family\s*:\s*([^;}]+)", filled_html, re.I)
        if (families := [
            family.strip().strip("'\"")
            for family in _expand_var(declaration).split(",")
            if family.strip().strip("'\"").casefold()
            not in {"serif", "sans-serif", "monospace", "inherit"}
        ])
    })
    with pdfplumber.open(target or generated) as target_pdf:
        target_font_families = {
            re.sub(r"^.*?\+", "", str(fontname)).rsplit("-", 1)[0].casefold()
            for page in target_pdf.pages
            for fontname in {char["fontname"] for char in page.chars}
        }
    resolved = " ".join(generated_fonts).casefold()
    _APPROVED_SUBSTITUTIONS = {
        "lato": "arial",
        "roboto": "arial",
        "charterbt": "charter",
        "charter": "charter",
        "fontawesome": "arial",
        "fontawesome5brands": "arial",
        "fontawesome5free": "arial",
        "fontawesome6free": "arial",
        "fontawesome6brands": "arial",
        "fontawesome5brandsregular": "arial",
        "segouisymbol": "arial",
        "cmsy10": "times",
        "cmsy9": "times",
        "cmr10": "times",
        "cmmi10": "times",
    }

    def _resolved_or_substituted(family: str) -> bool:
        key = family.casefold().replace(" ", "")
        resolved_flat = resolved.replace(" ", "")
        if key in resolved_flat:
            return True
        if key.startswith("fontawesome") and "fontawesome" in resolved_flat:
            return True
        substitute = _APPROVED_SUBSTITUTIONS.get(key)
        return bool(substitute) and substitute in resolved_flat

    missing_fonts = [
        family for family in requested
        if not _resolved_or_substituted(family) and family.casefold() in target_font_families
    ]
    resolved_flat = resolved.replace(" ", "")
    substituted = [
        family
        for family in requested
        if family.casefold().replace(" ", "") not in resolved_flat
        and bool(_APPROVED_SUBSTITUTIONS.get(family.casefold().replace(" ", "")))
        and _APPROVED_SUBSTITUTIONS[family.casefold().replace(" ", "")] in resolved_flat
    ]
    from tests.experiments.c_pipeline import rendered_duplicate_bullet_lines
    duplicate_lines = rendered_duplicate_bullet_lines(generated)
    duplicate_bullet_risk = bool(duplicate_lines)
    pua = sorted({character for character in filled_html + read_pdf_text(generated) if "\ue000" <= character <= "\uf8ff"})
    fa_embedded = any("fontawesome" in font.casefold() for font in generated_fonts)
    return {
        "fonts": {"passed": not missing_fonts, "requested": requested, "generated_pdf_fonts": generated_fonts, "missing_or_substituted": missing_fonts, "approved_substitutions_used": substituted},
        "duplicate_bullets": {"passed": not duplicate_bullet_risk, "rendered_duplicate_lines": duplicate_lines},
        "icon_fonts": {"passed": not pua or fa_embedded, "private_use_characters": pua, "fontawesome_embedded": fa_embedded},
        "pagination": {"passed": not any(page["blank"] for page in l1["pages"]), "pages": l1["pages"]},
        "margins": {"passed": not l1["margin_violations"], "violations": l1["margin_violations"]},
    }



def _unexpected_content_tokens(
    filled_html: str,
    source_text: str,
    template: str,
    summary: dict[str, Any],
) -> list[str]:
    fixed_template, _ = _html_text(re.sub(r"\[[^]]+\]", "", template))
    target_labels = " ".join(
        str(element.get("text") or "")
        for element in summary.get("elements", [])
        if element.get("structural_role") == "heading_candidate"
    )
    analysis = analyze_candidate_provenance(
        filled_html, source_text, allowed_text=f"{fixed_template} {target_labels}"
    )
    return [
        token for row in analysis["findings"] if row["code"] == "invented_content"
        for token in row["invented_tokens"]
    ]



_LOCAL_FONT_DIR = ROOT / "tests/experiments/assets/fonts"
_FONT_STYLE_FILES = (
    ("regular", "400", "normal"),
    ("bold", "700", "normal"),
    ("regularitalic", "400", "italic"),
    ("bolditalic", "700", "italic"),
)



def _requested_font_families(filled_html: str) -> list[str]:
    """First concrete family of every font-family declaration (CSS vars
    expanded), matching independent_checks' resolution order."""
    css_vars: dict[str, str] = dict(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:\s*([^;}]+)", filled_html))

    def expand(value: str) -> str:
        return re.sub(r"var\(\s*(--[a-zA-Z0-9_-]+)\s*\)", lambda m: css_vars.get(m.group(1), ""), value)

    families: list[str] = []
    for declaration in re.findall(r"font-family\s*:\s*([^;}]+)", filled_html, re.I):
        families_list = [
            family.strip().strip("'\"")
            for family in expand(declaration).split(",")
            if family.strip().strip("'\"").casefold()
            not in {"serif", "sans-serif", "monospace", "inherit", "cursive", "fantasy"}
        ]
        if families_list and families_list[0] not in families:
            families.append(families_list[0])
    return families



def _inject_local_fonts(filled_html: str) -> str:
    """Owner decision 2026-09-08: root-cause fix for the font-substitution
    workaround — vendor the template's webfonts locally (OFL-licensed, e.g.
    assets/fonts/lato-*.ttf) and embed them via @font-face before Chrome
    rendering, so the generated PDF carries the real font and the font gate
    passes on merit instead of an approved-substitution map."""
    faces: list[str] = []
    for family in _requested_font_families(filled_html):
        base = _LOCAL_FONT_DIR / family.casefold().replace(" ", "")
        for suffix, weight, style in _FONT_STYLE_FILES:
            font_file = Path(f"{base}-{suffix}.woff2")
            if not font_file.is_file():
                continue
            data = base64.b64encode(font_file.read_bytes()).decode("ascii")
            faces.append(
                f"@font-face {{ font-family: '{family}'; src: url(data:font/woff2;base64,{data}) format('woff2'); font-weight: {weight}; font-style: {style}; }}"
            )
    if not faces:
        return filled_html
    if not re.search(r"</style>", filled_html, re.I):
        return filled_html
    return re.sub(r"</style>", "\n" + "\n".join(faces) + "\n</style>", filled_html, count=1, flags=re.I)



def _typography_delta(target_summary: dict[str, Any], generated_pdf: Path) -> dict[str, Any]:
    """Owner decision 2026-09-08 (E->F postmortem): typography fidelity is
    measurable — compare the target's measured font sizes against the sizes
    actually present in the generated PDF, so the reviewer verifies numbers
    instead of guessing from pixels."""
    target_sizes = sorted({
        round(float(group["font_size_pt"]), 1)
        for group in (target_summary.get("style_groups") or {}).values()
        if group.get("font_size_pt")
    })
    generated: Counter[int] = Counter()
    with pdfplumber.open(generated_pdf) as pdf:
        for page in pdf.pages:
            for char in page.chars:
                size = char.get("size")
                if size:
                    generated[round(float(size), 1)] += 1
    total = sum(generated.values()) or 1
    by_frequency = sorted(generated.items(), key=lambda item: -item[1])

    def nearest(size: float) -> float | None:
        if not generated:
            return None
        return min(generated, key=lambda candidate: (abs(candidate - size), -generated[candidate]))

    rows = [
        {
            "target_size_pt": size,
            "closest_generated_size_pt": nearest(size),
            "exact_match": size in generated,
        }
        for size in target_sizes
    ]
    return {
        "target_sizes_pt": target_sizes,
        "generated_sizes_by_frequency": [
            {"size_pt": size, "char_share": round(count / total, 3)}
            for size, count in by_frequency[:8]
        ],
        "target_size_rows": rows,
        "generated_sizes_not_in_target": [
            {"size_pt": size, "char_share": round(count / total, 3)}
            for size, count in by_frequency
            if all(abs(size - target) > 0.6 for target in target_sizes)
        ][:6],
    }



def run_hard_gates(
    source_text: str,
    template: str,
    filled_html: str,
    first_pdf: Path,
    second_pdf: Path,
    first_pages: list[Path],
    second_pages: list[Path],
    target: Path,
    summary: dict[str, Any],
    allow_slot_splitting: bool = False,
) -> HardGateResult:
    failures: list[GateFailure] = []
    stable = len(first_pages) == len(second_pages) and all(
        _sha256(left) == _sha256(right) for left, right in zip(first_pages, second_pages)
    )
    if not stable:
        failures.append(GateFailure(code="unstable_render", details={"first_pages": len(first_pages), "second_pages": len(second_pages)}))

    html_text, _ = _html_text(filled_html)
    missing = _missing_source_tokens if allow_slot_splitting else _missing_occurrences
    missing_html = missing(source_text, html_text)
    # PDF-side coverage is hyphen-insensitive (owner decision 2026-09-09, F->E
    # postmortem): Chrome hyphen line-breaks + read_pdf_text merging drop the
    # hyphen, so strict comparison reports rendered content as missing forever.
    pdf_source = _hyphen_free(source_text)
    missing_first = missing(pdf_source, _hyphen_free(read_pdf_text(first_pdf)))
    missing_second = missing(pdf_source, _hyphen_free(read_pdf_text(second_pdf)))
    if missing_html:
        failures.append(GateFailure(code="missing_html_content", details=missing_html))
    if missing_first or missing_second:
        failures.append(GateFailure(code="missing_pdf_content", details={"first": missing_first, "second": missing_second}))

    unexpected = _unexpected_content_tokens(filled_html, source_text, template, summary)
    if unexpected:
        failures.append(GateFailure(code="invented_content", details=unexpected))

    margins = summary["margins_pt"]["default"]
    l1_results = [
        l1_check(first_pdf, filled_html, source_text, margins, allow_slot_splitting),
        l1_check(second_pdf, filled_html, source_text, margins, allow_slot_splitting),
    ]
    for index, result in enumerate(l1_results, 1):
        if not result["passed"]:
            failures.append(GateFailure(code=f"layout_render_{index}", details=result))

    independent = [
        independent_checks(target, filled_html, first_pdf, l1_results[0], target=target),
        independent_checks(target, filled_html, second_pdf, l1_results[1], target=target),
    ]
    required = ("fonts", "duplicate_bullets", "icon_fonts", "pagination", "margins")
    for index, checks in enumerate(independent, 1):
        failed = {name: checks[name] for name in required if not checks[name]["passed"]}
        if failed:
            failures.append(GateFailure(code=f"independent_render_{index}", details=failed))
    return HardGateResult(
        passed=not failures,
        failures=failures,
        l1={"first": l1_results[0], "second": l1_results[1]},
        independent={"first": independent[0], "second": independent[1]},
    )



def _visual_evidence_board(
    pdf_path: Path,
    pages: list[Path],
    summary: dict[str, Any],
    output_dir: Path,
    prefix: str,
) -> Path:
    from PIL import Image, ImageDraw, ImageOps

    tiles: list[tuple[str, Image.Image]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for index, page_path in enumerate(pages):
            page_image = Image.open(page_path).convert("RGB")
            tiles.append((f"page {index + 1}", page_image.copy()))
            masked = page_image.copy()
            draw = ImageDraw.Draw(masked)
            page = pdf.pages[index]
            scale_x, scale_y = masked.width / float(page.width), masked.height / float(page.height)
            for word in page.extract_words():
                draw.rectangle(
                    (
                        float(word["x0"]) * scale_x,
                        float(word["top"]) * scale_y,
                        float(word["x1"]) * scale_x,
                        float(word["bottom"]) * scale_y,
                    ),
                    fill="white",
                )
            structural = output_dir / f"{prefix}_structural_page_{index + 1}.png"
            masked.save(structural)
            tiles.append((f"structural page {index + 1}", masked))
        elements = {element["id"]: element for element in summary.get("elements", [])}
        for index, group in enumerate(
            (group for group in summary.get("visual_groups", []) if group.get("kind") == "section"), 1
        ):
            box = group.get("bbox_pt")
            members = [elements[item] for item in group.get("element_ids", []) if item in elements]
            if not (box and members):
                continue
            page_number = int(members[0]["page"])
            if not (1 <= page_number <= len(pages)):
                continue
            with Image.open(pages[page_number - 1]) as page_image:
                page = pdf.pages[page_number - 1]
                scale_x, scale_y = page_image.width / float(page.width), page_image.height / float(page.height)
                crop = page_image.convert("RGB").crop(
                    (
                        max(0, int(box["x0"] * scale_x)),
                        max(0, int(box["top"] * scale_y)),
                        min(page_image.width, int(box["x1"] * scale_x)),
                        min(page_image.height, int(box["bottom"] * scale_y)),
                    )
                )
            crop_path = output_dir / f"{prefix}_role_{index}.png"
            crop.save(crop_path)
            label = next(
                (str(item.get("text")) for item in members if item.get("structural_role") == "heading_candidate"),
                group["path"],
            )
            tiles.append((f"role: {label[:60]}", crop))
    width = 520
    prepared = []
    for label, tile in tiles:
        resized = ImageOps.contain(tile, (width - 20, 680))
        prepared.append((label, resized))
    height = sum(tile.height + 34 for _, tile in prepared) + 10
    board = Image.new("RGB", (width, max(height, 1)), "white")
    draw = ImageDraw.Draw(board)
    y = 10
    for label, tile in prepared:
        draw.text((10, y), label, fill="black")
        y += 24
        board.paste(tile, (10, y))
        y += tile.height + 10
    path = output_dir / f"{prefix}_evidence_board.png"
    board.save(path)
    return path



def _affected_region_image(board: Path, summary: dict[str, Any], issue: dict[str, Any]) -> Path:
    # RECONSTRUCTION-NOTE: annotations erased under PEP 563; inferred from meta/siblings.
    if not issue or not issue.get("region"):
        return board
    wanted = re.sub("[^a-z0-9]+", " ", str(issue["region"]).casefold()).strip()
    elements = {element["id"]: element for element in summary.get("elements", [])}
    groups = [group for group in summary.get("visual_groups", []) if group.get("kind") == "section"]
    prefix = board.name.removesuffix("_evidence_board.png")
    for index, group in enumerate(groups, 1):
        label = " ".join(
            str(elements[item].get("text") or "")
            for item in group.get("element_ids", [])
            if item in elements and elements[item].get("structural_role") == "heading_candidate"
        )
        normalized = re.sub("[^a-z0-9]+", " ", label.casefold()).strip()
        crop = board.with_name(f"{prefix}_role_{index}.png")
        if crop.exists() and (wanted in normalized or normalized in wanted):
            return crop
    return board



def compare_visual(
    target_board: Path,
    champion_board: Path | None,
    challenger_board: Path,
    summary: dict[str, Any],
    pending_issues: list[dict[str, Any]],
    repair_history: list[str],
    model: str,
    reverse_order: bool,
    reviewer_context: dict[str, Any] | None,
) -> tuple[VisualComparison, dict[str, int]]:
    override = visual_client()
    if override:
        visual_client_instance, override_model = override
        model = override_model
    else:
        visual_client_instance = None
    catalog = build_measurement_catalog(summary)
    response_schema = VisualComparison.model_json_schema()
    response_schema["$defs"]["VisualIssue"]["properties"]["measurement_refs"]["items"] = {
        "enum": sorted(catalog)
    }
    response_schema["$defs"]["RubricResult"]["properties"]["rubric_id"] = {
        "enum": [rubric_id for rubric_id, _description in VISUAL_RUBRIC]
    }
    prompt = f"""Compare candidate presentation against the target presentation using the labeled evidence boards.
Judge typography, spacing, alignment, rules, component structure, continuation-page consistency, and visible semantic placement.
Contact values must match their icons/slots. Source sections must not be silently merged into unrelated sections.
Different page counts are allowed and are never a defect by themselves. Additional source sections and natural overflow are allowed; judge whether they reuse the target's design system, not whether they exist.
Candidate wording and casing must be preserved, so do not flag literal text or capitalization differences from the target sample.
Judge first/continuation/last page classes, not equal pagination or whole-page pixel similarity.
When no CHAMPION image is present, judge the CHALLENGER directly against TARGET: list every visual gap a reviewer would flag (entry layout, spacing density, separator rules, heading sizes, column contents).
When a CHAMPION image is present, judge which of the pair is closer to TARGET.
Return JSON matching this exact shape: {json.dumps(response_schema, ensure_ascii=False)}
When no champion image exists, use preference not_applicable, retained_champion challenger, and put all TARGET-vs-CHALLENGER gaps in retained_champion_issues so the next repair round can fix them.
For a pair, challenger_better retains challenger; champion_better or tie retains champion.
Low confidence means the caller will repeat with image order swapped. Issues must describe only the retained champion.
DESIGN-SYSTEM CHECKLIST — verify each explicitly before returning; report every failing item as its own retained_champion_issue:
1. Heading component style: case (ALL-CAPS vs Title Case), size, and the rule/underline component the target pairs with headings.
2. Section order: the target's section order vs the candidate's visible section order.
3. Typography: the deterministic typography_delta table is authoritative — target sizes with exact_match=false, and generated sizes not in the target with meaningful char_share, are CONFIRMED deviations; flag them unless that region is genuinely absent.
4. Entry structure: left/right alignment pattern, one line per field, bullet style.
5. Density: entry/section spacing vs the target; a sparse trailing page is a defect.
6. Contact component: order and structure of the contact line(s).
The reviewer context distinguishes current unresolved issues from resolved history. Re-report every CURRENT unresolved issue that remains, but never infer a present defect from resolved_issue_history or recent repair outcomes. Current images and current hard gates are authoritative.
Mark acceptable only after reviewing the FULL evidence boards, every prior unresolved issue is visibly resolved, and every rubric is pass. Any fail or unknown rubric means acceptable=false. Identify regressions.
Classify each issue as content_integrity, semantic_placement, structure, or presentation. Content and semantic failures outrank structural failures, which outrank minor presentation refinements.
Choose suggested_action from the schema. Select measurement_refs only from the supplied measurement_reference_ids. Never invent or transcribe numeric target values into an issue.
Every set_style_token, set_rule_geometry, or set_section_spacing issue MUST select at least one compatible supplied measurement reference; if none applies, use unknown instead.
Evaluate every fixed visual rubric item as pass, fail, or unknown. A visual pass is advisory and cannot override deterministic failures.

REVIEWER CONTEXT: {json.dumps(reviewer_context or {"format_summary": summary, "last_issues": pending_issues, "recent_repair_outcomes": repair_history[-4:], "measurement_reference_ids": sorted(build_measurement_catalog(summary))}, ensure_ascii=False, sort_keys=True)}
"""
    labeled = [("TARGET", target_board)]
    pair = [("CHAMPION", champion_board), ("CHALLENGER", challenger_board)]
    if reverse_order:
        pair.reverse()
    labeled.extend((label, path) for label, path in pair if path is not None)
    content = [{"type": "text", "text": prompt}]
    for label, path in labeled:
        content.extend(
            (
                {"type": "text", "text": label},
                {"type": "image_url", "image_url": {"url": _image_data(path)}},
            )
        )

    def validate_visual_result(candidate: BaseModel) -> None:
        assert isinstance(candidate, VisualComparison)
        expected_retained = (
            "challenger" if champion_board is None or candidate.preference == "challenger_better" else "champion"
        )
        if candidate.retained_champion != expected_retained:
            raise ValueError(
                f"retained_champion must be {expected_retained} for preference {candidate.preference}"
            )
        deterministic_dimensions = {
            "missing_source_content", "duplicated_source_content", "invented_content",
            "missing_html_content", "missing_pdf_content", "contact_value_icon_pairing",
        }
        current_gates_passed = bool(
            (reviewer_context or {}).get("hard_gate_pass_summary", {}).get("passed")
        )
        if current_gates_passed:
            # Owner decision 2026-09-08: deterministic gates are the content
            # authority. A reviewer that insists on missing/duplicated/invented
            # content despite passing gates is hallucinating against verified
            # facts — strip those claims (the full gate evidence stays in the
            # round's hard_gates.json) and keep the visual findings. Retrying
            # the same contradiction wastes the entire reviewer budget.
            candidate.retained_champion_issues = [
                issue for issue in candidate.retained_champion_issues
                if issue.dimension not in deterministic_dimensions
                and issue.category != "content_integrity"
                and re.sub(r"[^a-z]+", "_", issue.region.casefold()).strip("_") != "deterministic_gate"
            ]
            if not candidate.retained_champion_issues and all(
                result.status == "pass" for result in candidate.rubric_results
            ):
                candidate.retained_champion_acceptable = True
        for issue in candidate.retained_champion_issues:
            resolve_measurements(issue, catalog, required=issue.suggested_action in BUILDER_ACTIONS)
        rubric_ids = [result.rubric_id for result in candidate.rubric_results]
        expected_ids = [rubric_id for rubric_id, _description in VISUAL_RUBRIC]
        if len(rubric_ids) != len(set(rubric_ids)) or set(rubric_ids) != set(expected_ids):
            raise ValueError("Reviewer must return every fixed rubric_id exactly once")
        if candidate.retained_champion_acceptable and (
            candidate.retained_champion_issues
            or any(result.status != "pass" for result in candidate.rubric_results)
        ):
            raise ValueError("Reviewer cannot mark acceptable with unresolved issues or non-pass rubrics")
        if not candidate.retained_champion_acceptable and (
            not candidate.retained_champion_issues
            and all(result.status == "pass" for result in candidate.rubric_results)
        ):
            raise ValueError("Reviewer must mark acceptable when every rubric passes and no issue remains")

    result, usage = _structured_chat_call(
        model,
        [
            {"role": "system", "content": "You are a precise visual reviewer comparing resume renderings. Return only valid JSON matching the requested schema."},
            {"role": "user", "content": content},
        ],
        VisualComparison,
        allow_correction=(reviewer_context or {}).get("reviewer_attempts_remaining", 2) > 1,
        max_attempts=(reviewer_context or {}).get("reviewer_attempts_remaining"),
        validate=validate_visual_result,
        client=visual_client_instance,
        stream=True,
    )
    assert isinstance(result, VisualComparison)
    return result, usage



def _render_pages(pdf_path: Path, output_dir: Path, prefix: str) -> list[Path]:
    document = pdfium.PdfDocument(pdf_path)
    paths: list[Path] = []
    for index, page in enumerate(document):
        path = output_dir / f"{prefix}_page_{index + 1}.png"
        page.render(scale=2).to_pil().convert("RGB").save(path)
        paths.append(path)
    return paths



def _side_by_side(target: Path, generated: Path, output: Path) -> None:
    from PIL import Image

    with Image.open(target) as left, Image.open(generated) as right:
        height = max(left.height, right.height)
        canvas = Image.new("RGB", (left.width + right.width, height), "white")
        canvas.paste(left, (0, 0))
        canvas.paste(right, (left.width, 0))
        canvas.save(output)



def _estimated_openai_cost(capable_model: str, visual_model: str, usage: dict[str, dict[str, int]]) -> float | None:
    # RECONSTRUCTION-NOTE: parameter/return annotations are erased under PEP 563
    # (no bytecode trace); inferred from call sites and the pre-loss era slice.
    prices = {
        "deepseek-v4-flash": (0.3, 1.0),
        "deepseek-v4-flash-exp": (0.5, 2.0),
    }
    if any(model not in prices for model in (capable_model, visual_model)):
        return None
    total = 0.0
    for lane, model in (("builder", capable_model), ("filler", capable_model), ("visual", visual_model)):
        total += (
            usage[lane].get("input_tokens", 0) * prices[model][0]
            + usage[lane].get("output_tokens", 0) * prices[model][1]
        ) / 1000000
    return total



def _write_readme(
    run_dir: Path,
    target: Path,
    source: Path,
    capable_model: str,
    visual_model: str,
    usage: dict[str, Any],
    l0: L0Result,
    l1: dict[str, Any],
    independent: dict[str, Any],
    elapsed: float,
    cache_key: str,
    cache_hit: bool,
    refinement: RefinementResult,
) -> None:
    passed = l0.passed and not l0.missing_source_content and l1["passed"]
    reviewer_accepted = refinement.stop_reason == "acceptable_champion"
    if reviewer_accepted and passed:
        verdict = "Reviewer accepted the champion (acceptable_champion); L0/L1 passed. L2 remains an owner decision."
    elif passed:
        verdict = f"L0/L1 passed, but the Reviewer did NOT accept the champion (stop reason: `{refinement.stop_reason}`); L2 NOT passed."
    else:
        verdict = "Automatic checks failed; inspect the reports before judging L2."
    estimated_cost = _estimated_openai_cost(capable_model, visual_model, usage)
    cost = (
        f"estimated DeepSeek token cost `${estimated_cost:.4f}`"
        if estimated_cost is not None
        else "token usage recorded; provider billing not guessed"
    )
    run_dir.joinpath("RUN_README.md").write_text(
        "".join([
            "# A-pipeline experiment run\n\nVerdict: **",
            f"{verdict}",
            "**\n\n- Target: `",
            f"{target}",
            "`\n- Source: `",
            f"{source}",
            "`\n- Adobe: live PDF Extract plus local pdfplumber color/rule/badge enrichment\n- Builder / Filler: `",
            f"{capable_model}",
            "` (DeepSeek)\n- Visual comparator: `",
            f"{visual_model}",
            "` (DeepSeek)\n- L0 content coverage: deterministic token comparison\n- Retained champion: round `",
            f"{refinement.champion_round}",
            "`; stop reason `",
            f"{refinement.stop_reason}",
            "`\n- Attempts: `",
            f"{refinement.filler_attempts}",
            "` Filler; `",
            f"{refinement.reviewer_checks}",
            "` actual Reviewer checks\n- Frozen-template cache: `",
            f"{'HIT' if cache_hit else 'MISS'}",
            "` (`",
            f"{cache_key}",
            "`)\n- API usage: `",
            f"{json.dumps(usage, sort_keys=True)}",
            "`\n- Cost: ",
            f"{cost}",
            " (regional uplifts, discounts, and account-specific Adobe pricing excluded).\n- Elapsed: ",
            f"{elapsed:.1f}",
            "s\n\n## Artifacts\n\n- [Measured format summary](format_summary.json)\n- [Pinned render environment](render_environment.json)\n- [Empty HTML template](empty_template.html)\n- [Source provenance](source_provenance.json)\n- [Filled HTML](filled.html)\n- [Generated PDF](generated.pdf)\n- [Target PDF](target.pdf)\n- [L0 content result](l0_result.json)\n- [L1 structure result](l1_result.json)\n- [Independent defect checks](independent_checks.json)\n- [Refinement report](refinement_report.json)\n- [First-page side-by-side](side_by_side.png) — target left, generated right\n- [Raw enriched evidence](enriched_evidence.json)\n\n## Checks\n\n- L0: `",
            f"{'PASS' if l0.passed and not l0.missing_source_content else 'FAIL'}",
            "` — ",
            f"{len(l0.missing_source_content)}",
            " unexplained missing items.\n- L1: `",
            f"{'PASS' if l1['passed'] else 'FAIL'}",
            "` — ",
            f"{len(l1['overlaps'])}",
            " overlaps, ",
            f"{len(l1['empty_sections'])}",
            " empty sections, ",
            f"{sum(1 for page in l1['pages'] if page['blank'])}",
            " blank pages.\n- Independent checks: `",
            f"{json.dumps({name: row['passed'] for name, row in independent.items()}, sort_keys=True)}",
            "`.\n- L2: **OWNER MUST EYEBALL.** Compare `target.pdf`, `generated.pdf`, and `side_by_side.png` for format style only. This run does not claim L2 pass.\n\n## What broke / surprised us\n\n",
            f"{l0.notes or 'No additional L0 judge notes.'}",
            "\n",
        ]),
        encoding="utf-8",
    )



def _analyze_target(
    target: Path,
    run_dir: Path,
    use_persistent_cache: bool = True,
) -> tuple[NormalizedLayoutEvidence, dict[str, Any]]:
    cache = RUNS / "target_cache" / _sha256(target)
    raw_path, evidence_path = cache / "adobe_raw.json", cache / "enriched_evidence.json"
    if use_persistent_cache and raw_path.exists() and evidence_path.exists():
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        evidence = NormalizedLayoutEvidence.model_validate_json(evidence_path.read_text(encoding="utf-8"))
    else:
        evidence, raw, _zip = run_adobe_layout(target)
        evidence = enrich_colors_from_local_pdf(evidence, target)
        evidence = enrich_rules_from_local_pdf(evidence, target)
        evidence = enrich_badges_from_local_pdf(evidence, target)
        if use_persistent_cache:
            cache.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            evidence_path.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
    run_dir.joinpath("adobe_raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    run_dir.joinpath("enriched_evidence.json").write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
    return evidence, raw



def run(
    target: Path,
    source: Path,
    output: Path | None = None,
    run_target_cache: dict[str, dict[str, Any]] | None = None,
    call_ledger: Counter[str] | None = None,
    template_override: Path | None = None,
) -> Path:
    load_dotenv(ROOT / ".env")
    required = ["ADOBE_PDF_SERVICES_CLIENT_ID", "ADOBE_PDF_SERVICES_CLIENT_SECRET"]
    missing = [name for name in required if not os.getenv(name)]
    if not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")):
        missing.append("DEEPSEEK_API_KEY or OPENAI_API_KEY")
    if missing:
        raise RuntimeError("Missing live configuration: " + ", ".join(missing))
    capable_model = os.getenv("A_PIPELINE_MODEL") or "deepseek-v4-flash-vision-exp"
    capable_provider = "deepseek"
    visual_model = os.getenv("A_PIPELINE_VISUAL_MODEL") or os.getenv("A_PIPELINE_VLM_MODEL") or capable_model
    run_dir = output or RUNS / datetime.now(UTC).strftime("a_pipeline_%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    call_ledger = call_ledger if call_ledger is not None else Counter()
    starting_calls = call_ledger.copy()
    log: list[str] = []
    started = time.perf_counter()

    def stage(message: str) -> None:
        print(message, flush=True)
        log.append(f"{datetime.now(UTC).isoformat()} {message}")

    try:
        shutil.copy2(target, run_dir / "target.pdf")
        target_key = _sha256(target)
        cached_target = run_target_cache.get(target_key) if run_target_cache is not None else None
        if cached_target:
            stage("Adobe layout analysis (run-scoped cache hit)")
            evidence, raw, summary = cached_target["evidence"], cached_target["raw"], cached_target["summary"]
            run_dir.joinpath("adobe_raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            run_dir.joinpath("enriched_evidence.json").write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
            adobe_calls = 0
        else:
            stage("Adobe layout analysis (fresh)" if run_target_cache is not None else "Adobe layout analysis")
            call_ledger["adobe"] += 1
            evidence, raw = _analyze_target(target, run_dir, use_persistent_cache=run_target_cache is None)
            stage("Deterministic format summary")
            summary = build_format_summary(evidence, raw, target)
            adobe_calls = 1
        run_dir.joinpath("format_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        _require_geometry(summary)
        chrome_environment = _pinned_chrome_environment(summary)
        run_dir.joinpath("render_environment.json").write_text(
            json.dumps(chrome_environment, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        target_pages = _render_pages(run_dir / "target.pdf", run_dir, "target")
        target_board = _visual_evidence_board(run_dir / "target.pdf", target_pages, summary, run_dir, "target")

        cache_key = _template_cache_key(target, summary, capable_provider, capable_model)
        template_cache = RUNS / "template_cache" / cache_key
        cached_template = template_cache / "empty_template.html"
        cache_hit = template_override is None and (
            cached_target is not None or (run_target_cache is None and cached_template.exists())
        )
        template_source = (
            "candidate override" if template_override else
            "run-scoped cache hit" if cached_target else
            "persistent cache hit" if cache_hit else "fresh"
        )
        stage(f"Builder empty template ({template_source})")
        if template_override:
            template = template_override.read_text(encoding="utf-8")
            usage_1 = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        elif cached_target:
            template = cached_target["template"]
            usage_1 = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        elif cache_hit:
            template = cached_template.read_text(encoding="utf-8")
            usage_1 = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        else:
            call_ledger["builder"] += 1
            initial_context = {
                "format_summary": summary,
                "target_evidence_board": _artifact_ref(target_board),
                "candidate_source_text": None,
                "purpose": "build empty reusable template",
            }
            run_dir.joinpath("initial_builder_context.json").write_text(
                json.dumps(initial_context, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            template, usage_1 = _html_call(_template_prompt(summary), capable_model, [target_board])
            if run_target_cache is None:
                template_cache.mkdir(parents=True, exist_ok=True)
                cached_template.write_text(template, encoding="utf-8")
                template_cache.joinpath("metadata.json").write_text(
                    json.dumps(
                        {
                            "cache_key": cache_key,
                            "target_sha256": target_key,
                            "format_summary_version": FORMAT_SUMMARY_VERSION,
                            "template_prompt_version": TEMPLATE_PROMPT_VERSION,
                            "analyzer_version": ANALYZER_VERSION,
                            "provider_version": summary.get("provider_version"),
                            "provider": "deepseek",
                            "model": capable_model,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            else:
                run_target_cache[target_key] = {"evidence": evidence, "raw": raw, "summary": summary, "template": template}
        template = normalize_header_element(template)
        template = append_heading_case_rule(template)
        run_dir.joinpath("empty_template.html").write_text(template, encoding="utf-8")
        cached_template_hash = _sha256(cached_template) if cached_template.exists() else None

        def save_accounting(usage: dict[str, Any]) -> None:
            run_dir.joinpath("usage.json").write_text(json.dumps(usage, indent=2), encoding="utf-8")
            run_dir.joinpath("cache_result.json").write_text(
                json.dumps(
                    {
                        "key": cache_key,
                        "hit": cache_hit,
                        "scope": "candidate_override" if template_override else "run" if cached_target else "persistent" if cache_hit else "fresh",
                        "adobe_calls": adobe_calls,
                        "builder_calls": 0 if cache_hit or template_override else 1,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            run_dir.joinpath("run_metadata.json").write_text(
                json.dumps(
                    {
                        "provider": "deepseek",
                        "models": {"builder_filler": capable_model, "visual": visual_model},
                        "calls": {
                            name: call_ledger[name] - starting_calls[name]
                            for name in ("adobe", "builder", "filler", "visual")
                        },
                        "estimated_openai_cost_usd": _estimated_openai_cost(capable_model, visual_model, usage),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

        source_text = read_docx(source).plain_text if source.suffix.lower() == ".docx" else read_pdf_text(source)
        run_dir.joinpath("source_text.txt").write_text(source_text, encoding="utf-8")

        gated_source_text = source_text
        run_dir.joinpath("gated_source_text.txt").write_text(gated_source_text, encoding="utf-8")
        artifacts: dict[int, dict[str, Any]] = {}
        candidates: dict[int, str] = {}

        def gate(round_number: int, challenger_html: str, current_template: str | None = None) -> HardGateResult:
            round_dir = run_dir / f"round_{round_number}"
            round_dir.mkdir(exist_ok=True)
            gate_template = current_template or template
            fixed_template_text, _ = _html_text(re.sub(r"\[[^]]+\]", "", gate_template))
            target_labels = " ".join(
                str(element.get("text") or "")
                for element in summary.get("elements", [])
                if element.get("structural_role") == "heading_candidate"
            )
            analysis = analyze_candidate_provenance(
                challenger_html,
                gated_source_text,
                allowed_text=f"{fixed_template_text} {target_labels}",
            )
            provenance_errors, ownership = validate_candidate_html(challenger_html, gated_source_text, analysis=analysis)
            new_images = sorted(_image_sources(challenger_html) - _image_sources(gate_template))
            if new_images:
                provenance_errors.append("candidate HTML introduced image assets not present in the Builder template")
            filler_conformance_errors = validate_filler_conformance(gate_template, challenger_html)
            provenance_errors.extend(filler_conformance_errors)
            conformance = {
                "passed": not provenance_errors,
                "renderer": "candidate_html",
                "candidate_css_allowed": False,
                "source_provenance_errors": provenance_errors,
                "source_provenance_warnings": analysis["warnings"],
                "new_image_assets": new_images,
            }
            round_dir.joinpath("template_conformance.json").write_text(
                json.dumps(conformance, indent=2), encoding="utf-8"
            )
            cheap_failures: list[GateFailure] = []
            if new_images or filler_conformance_errors:
                cheap_failures.append(GateFailure(code="invalid_candidate_html", details=conformance))
            cheap_failures.extend(
                GateFailure(code=finding["code"], details=finding)
                for finding in analysis["findings"]
            )
            if cheap_failures:
                html_path = round_dir / "challenger.html"
                html_path.write_text(challenger_html, encoding="utf-8")
                result = HardGateResult(passed=False, failures=cheap_failures)
                round_dir.joinpath("hard_gates.json").write_text(
                    result.model_dump_json(indent=2), encoding="utf-8"
                )
                round_dir.joinpath("visual_comparison.json").write_text(
                    json.dumps({"skipped": "hard_gates_failed"}, indent=2), encoding="utf-8"
                )
                artifacts[round_number] = {"dir": round_dir, "html": html_path, "gates": result}
                return result
            round_dir.joinpath("source_provenance.json").write_text(
                json.dumps(
                    {
                        **provenance_report(ownership, gated_source_text),
                        "warnings": analysis["warnings"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            challenger_html = _inject_icon_fonts(challenger_html)
            challenger_html = _inject_local_fonts(challenger_html)
            html_path = round_dir / "challenger.html"
            html_path.write_text(challenger_html, encoding="utf-8")
            stage(f"Round {round_number}: double Chrome render and hard gates")
            first_pdf = _export_pinned_html_to_pdf(html_path, round_dir / "render_1.pdf", chrome_environment)
            second_pdf = _export_pinned_html_to_pdf(html_path, round_dir / "render_2.pdf", chrome_environment)
            first_pages = _render_pages(first_pdf, round_dir, "render_1")
            second_pages = _render_pages(second_pdf, round_dir, "render_2")
            first_board = _visual_evidence_board(first_pdf, first_pages, summary, round_dir, "render_1")
            _visual_evidence_board(second_pdf, second_pages, summary, round_dir, "render_2")
            result = run_hard_gates(
                gated_source_text,
                gate_template,
                challenger_html,
                first_pdf,
                second_pdf,
                first_pages,
                second_pages,
                target,
                summary,
                allow_slot_splitting=True,
            )
            round_dir.joinpath("hard_gates.json").write_text(
                result.model_dump_json(indent=2), encoding="utf-8"
            )
            artifacts[round_number] = {
                "dir": round_dir,
                "html": html_path,
                "pdf": first_pdf,
                "pages": first_pages,
                "board": first_board,
                "gates": result,
            }
            if not result.passed:
                round_dir.joinpath("visual_comparison.json").write_text(
                    json.dumps({"skipped": "hard_gates_failed"}, indent=2), encoding="utf-8"
                )
            return result

        def compare(
            champion_round: int | None,
            challenger_round: int,
            reverse_order: bool,
            pending_issues: list[dict[str, Any]],
            repair_history: list[Any],
            reviewer_context: dict[str, Any] | None = None,
        ) -> tuple[VisualComparison, dict[str, int]]:
            stage(
                f"Round {challenger_round}: visual comparison"
                f"{' with swapped order' if reverse_order else ''}"
                f"{' (no champion yet: vs TARGET)' if champion_round is None else ''}"
            )
            if reviewer_context is not None:
                try:
                    reviewer_context["typography_delta"] = _typography_delta(summary, artifacts[challenger_round]["pdf"])
                except Exception:
                    pass
            try:
                result, visual_usage = compare_visual(
                    target_board,
                    artifacts[champion_round]["board"] if champion_round is not None else None,
                    artifacts[challenger_round]["board"],
                    summary,
                    pending_issues,
                    repair_history,
                    visual_model,
                    reverse_order,
                    reviewer_context,
                )
            except PipelineCallError as error:
                call_ledger["visual"] += error.attempts
                raise
            call_ledger["visual"] += visual_usage.get("attempts", 1)
            name = "visual_comparison_reversed.json" if reverse_order else "visual_comparison.json"
            artifacts[challenger_round]["dir"].joinpath(name).write_text(
                result.model_dump_json(indent=2), encoding="utf-8"
            )
            return result, visual_usage

        if target_ref := _artifact_ref(target_board):
            def generate_candidate(context: dict[str, Any]) -> tuple[str, dict[str, int]]:
                issue = context.get("selected_issue")
                paths: list[Path] = []
                champion_ref = context.get("champion_region_evidence")
                if champion_ref and champion_ref.get("path"):
                    paths.append(_affected_region_image(Path(champion_ref["path"]), summary, issue))
                return generate_with_context(context, paths)

            def generate_with_context(context: dict[str, Any], paths: list[Path]) -> tuple[str, dict[str, int]]:
                stage("Filler candidate fill/repair")
                call_ledger["filler"] += 1
                candidate, candidate_usage = _html_call(_filler_prompt(context), capable_model, paths)
                base_template = context.get("run_local_template") or template
                candidate = _restore_template_presentation(candidate, base_template)
                candidate = normalize_header_element(candidate)
                candidate = normalize_section_headings(candidate, base_template, source_text)
                candidate, _removed = deduplicate_candidate_html(candidate, source_text)
                candidate = _normalize_provenance_annotations(candidate, source_text)
                candidate = _normalize_section_order(candidate, base_template, source_text)
                candidates[len(candidates) + 1] = candidate
                return candidate, candidate_usage

            def repair_template(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
                stage("Builder bounded presentation repair")
                call_ledger["builder"] += 1
                issue = context.get("selected_issue")
                return _builder_operation_call(
                    context,
                    capable_model,
                    [
                        _affected_region_image(target_board, summary, issue),
                        _affected_region_image(Path(context["champion_region_evidence"]["path"]), summary, issue),
                    ],
                )

            def repair_placement(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
                issue = context.get("selected_issue")
                paths = [_affected_region_image(target_board, summary, issue)]
                champion_ref = context.get("champion_region_evidence")
                if champion_ref and champion_ref.get("path"):
                    paths.append(_affected_region_image(Path(champion_ref["path"]), summary, issue))
                return _placement_operation_call(context, capable_model, paths)

            refinement = run_builder_repair_loop(
                template,
                summary,
                gated_source_text,
                generate_candidate,
                repair_template,
                lambda number, state: gate(number, state.filled_html, state.template_html),
                compare,
                place=repair_placement,
                artifact_root=run_dir,
                target_evidence=target_ref,
                evidence_for_round=lambda number: _artifact_ref(artifacts[number]["board"]),
            )
        refinement_report = {
            "champion_round": refinement.champion_round,
            "stop_reason": refinement.stop_reason,
            "reviewer_accepted": refinement.stop_reason == "acceptable_champion",
            "filler_attempts": refinement.filler_attempts,
            "reviewer_checks": refinement.reviewer_checks,
            "rounds": refinement.rounds,
            "filler_usage": refinement.filler_usage,
            "visual_usage": refinement.visual_usage,
            "builder_usage": refinement.builder_usage or {},
            "builder_changes": refinement.builder_changes,
        }
        run_dir.joinpath("refinement_report.json").write_text(
            json.dumps(refinement_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        builder_total = dict(Counter(usage_1) + Counter(refinement.builder_usage or {}))
        save_accounting({"builder": builder_total, "filler": refinement.filler_usage, "visual": refinement.visual_usage})
        if refinement.champion_round is None:
            raise RuntimeError("No hard-gate-valid champion before the Filler safety limit; inspect the final hard_gates.json")
        champion = artifacts[refinement.champion_round]
        if cached_template_hash is not None and _sha256(cached_template) != cached_template_hash:
            raise RuntimeError("Persistent template cache changed during refinement")
        shutil.copy2(champion["dir"] / "source_provenance.json", run_dir / "source_provenance.json")
        shutil.copy2(champion["html"], run_dir / "filled.html")
        generated = shutil.copy2(champion["pdf"], run_dir / "generated.pdf")
        filled = refinement.champion_html or champion["html"].read_text(encoding="utf-8")
        generated_text = read_pdf_text(generated)
        run_dir.joinpath("generated_text.txt").write_text(generated_text, encoding="utf-8")

        stage("Deterministic source-to-PDF comparison")
        l0_source = "\n".join(
            line
            for line in gated_source_text.splitlines()
            if not _is_redaction_placeholder(line)
        )
        run_dir.joinpath("l0_source_text.txt").write_text(l0_source, encoding="utf-8")
        missing_content = _missing_source_tokens(_hyphen_free(l0_source), _hyphen_free(generated_text))
        l0 = L0Result(
            passed=not missing_content,
            missing_source_content=missing_content,
            notes="Deterministic source-token coverage; no LLM judge call.",
        )
        run_dir.joinpath("l0_result.json").write_text(l0.model_dump_json(indent=2), encoding="utf-8")
        l1 = champion["gates"].l1["first"]
        run_dir.joinpath("l1_result.json").write_text(json.dumps(l1, indent=2), encoding="utf-8")
        independent = champion["gates"].independent["first"]
        run_dir.joinpath("independent_checks.json").write_text(
            json.dumps(independent, indent=2), encoding="utf-8"
        )
        generated_pages = _render_pages(generated, run_dir, "generated")
        _side_by_side(target_pages[0], generated_pages[0], run_dir / "side_by_side.png")
        usage = {"builder": builder_total, "filler": refinement.filler_usage, "visual": refinement.visual_usage}
        save_accounting(usage)
        _write_readme(
            run_dir,
            target,
            source,
            capable_model,
            visual_model,
            usage,
            l0,
            l1,
            independent,
            time.perf_counter() - started,
            cache_key,
            cache_hit,
            refinement,
        )
        return run_dir
    except Exception as error:
        report = run_dir / "refinement_report.json"
        if not report.exists():
            completed_rounds: list[dict[str, Any]] = []
            completed_reviews = 0
            for round_dir in sorted(run_dir.glob("round_*"), key=lambda path: int(path.name.split("_")[-1])):
                gates_path = round_dir / "hard_gates.json"
                if not gates_path.exists():
                    continue
                gates = json.loads(gates_path.read_text(encoding="utf-8"))
                failures = gates.get("failures", [])
                completed_rounds.append(
                    {
                        "round": int(round_dir.name.split("_")[-1]),
                        "hard_gates": gates,
                        "promoted": False,
                        "feedback_routing": [
                            {
                                **failure,
                                "classification": "contract_violation",
                                "route": "deterministic_validation",
                                "routing_reason": "rejected before Reviewer execution",
                            }
                            for failure in failures
                        ],
                    }
                )
                for review_name in ("visual_comparison.json", "visual_comparison_reversed.json"):
                    review_path = round_dir / review_name
                    if not review_path.exists():
                        continue
                    if "preference" in json.loads(review_path.read_text(encoding="utf-8")):
                        completed_reviews += 1
            report.write_text(
                json.dumps(
                    {
                        "champion_round": None,
                        "stop_reason": "unrecoverable_error",
                        "filler_attempts": call_ledger["filler"] - starting_calls["filler"],
                        "reviewer_checks": completed_reviews,
                        "rounds": completed_rounds,
                        "error": {
                            "type": type(error).__name__,
                            "kind": getattr(error, "kind", "gate" if "hard-gate" in str(error).casefold() else "model"),
                            "message": str(error),
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        raise
    finally:
        run_dir.joinpath("run.log").write_text("\n".join(log) + "\n", encoding="utf-8")



def _comparison_diff(left_path: Path, right_path: Path, output: Path) -> None:
    from PIL import Image, ImageChops
    with Image.open(left_path).convert("RGB") as left, Image.open(right_path).convert("RGB") as right:
        size = (max(left.width, right.width), max(left.height, right.height))
        left_canvas = Image.new("RGB", size, "white")
        right_canvas = Image.new("RGB", size, "white")
        left_canvas.paste(left, (0, 0))
        right_canvas.paste(right, (0, 0))
        ImageChops.difference(left_canvas, right_canvas).save(output)



def _input_record(letter: str) -> dict[str, Any]:
    path = ROOT / f"tests/local_datasets/resume_matrix/resume_{letter}.pdf"
    with pdfplumber.open(path) as pdf:
        pages = len(pdf.pages)
    return {
        "id": f"resume_{letter.casefold()}",
        "filename": path.name,
        "path": str(path.relative_to(ROOT)),
        "sha256": _sha256(path),
        "page_count": pages,
        "byte_size": path.stat().st_size,
    }



def _project_completed_pair(root: Path, pair_dir: Path, result: dict[str, Any]) -> None:
    artifacts = pair_dir / "artifacts"
    generation, comparison = pair_dir / "generation", pair_dir / "comparison"
    generation.mkdir()
    comparison.mkdir()
    shutil.copy2(artifacts / "filled.html", generation / "candidate_profile.html")
    shutil.copy2(artifacts / "generated.pdf", generation / "candidate_profile.pdf")
    refinement = json.loads(artifacts.joinpath("refinement_report.json").read_text())
    comparison.joinpath("visual_comparison.json").write_text(
        json.dumps(
            {"champion_round": refinement["champion_round"], "stop_reason": refinement["stop_reason"], "rounds": refinement["rounds"]},
            indent=2,
        ),
        encoding="utf-8",
    )
    generated_pages = sorted(artifacts.glob("generated_page_*.png"))
    target_pages = sorted(artifacts.glob("target_page_*.png"))
    for index, generated in enumerate(generated_pages, 1):
        target = target_pages[min(index - 1, len(target_pages) - 1)]
        generated_out = comparison / f"generated_page_{index:03d}.png"
        target_out = comparison / f"comparison_target_page_{index:03d}.png"
        shutil.copy2(generated, generated_out)
        shutil.copy2(target, target_out)
        _comparison_diff(target_out, generated_out, comparison / f"comparison_page_{index:03d}_diff.png")
    result["generated_page_count"] = len(generated_pages)
    result["generated_pdf"] = str((generation / "candidate_profile.pdf").relative_to(root))
    result["generated_pdf_sha256"] = _sha256(generation / "candidate_profile.pdf")
    result["first_page_diff"] = str((comparison / "comparison_page_001_diff.png").relative_to(root))



def _write_matrix_reports(
    root: Path,
    started: str,
    inputs: list[dict[str, Any]],
    results: list[dict[str, Any]],
    calls: Counter[str],
) -> None:
    completed_at = datetime.now(UTC).isoformat()
    revision = "Builder repair"
    completed = [result for result in results if result["status"] == "completed"]
    content_rows = [
        {
            "transformation_id": result["transformation_id"],
            "passed": bool(result.get("content_preserved")),
            "l0_passed": result.get("l0_passed"),
            "hard_gate_valid": result.get("hard_gate_valid"),
        }
        for result in results
    ]
    root.joinpath("content_preservation_verification.json").write_text(
        json.dumps(
            {
                "passed": sum(row["passed"] for row in content_rows),
                "total": len(content_rows),
                "results": content_rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest = {
        "execution_schema_version": "a-pipeline-matrix/1",
        "result_format_version": "resume-matrix-review-1.0",
        "experiment": "a_pipeline",
        "dataset_classification": "owner_attested_fake_resume_corpus",
        "state": "completed" if len(completed) == len(results) else "completed_with_failures",
        "started_at": started,
        "completed_at": completed_at,
        "method": f"fresh Adobe and Builder once per target; {revision} gate-and-champion candidate loop",
        "network_calls": dict(calls),
        "llm_calls": sum(calls[name] for name in ("builder", "filler", "visual")),
        "review_policy": "Automated checks do not decide L2; owner must inspect target and champion PDFs.",
        "inputs": inputs,
        "transformations": results,
    }
    root.joinpath("run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    columns = [
        "transformation_id",
        "source_id",
        "target_id",
        "status",
        "average_layout_similarity",
        "baseline_layout_similarity",
        "layout_similarity_delta",
        "generated_page_count",
        "added_section_ids",
        "recovered_candidate_block_ids",
        "generated_pdf",
        "error_message",
    ]
    with root.joinpath("transformation_matrix.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for result in results:
            writer.writerow({name: result.get(name, "") for name in columns})
    input_by_id = {row["id"]: row for row in inputs}
    failures = [result for result in results if result["status"] != "completed"]
    matrix_rows: list[str] = []
    for source in ("resume_d", "resume_e", "resume_f"):
        cells = []
        for target in ("resume_d", "resume_e", "resume_f"):
            result = next(
                (row for row in results if row["source_id"] == source and row["target_id"] == target),
                None,
            )
            if result is None:
                cells.append("—")
            elif result["status"] == "completed":
                cells.append(f"[{result['champion_round']}]({result['generated_pdf']})")
            else:
                cells.append("FAIL")
        matrix_rows.append(f"| {source} | {' | '.join(cells)} |")
    detailed: list[str] = []
    for index, result in enumerate(results, 1):
        if result["status"] == "completed":
            links = (
                f"[PDF]({result['generated_pdf']}) · [diff]({result['first_page_diff']})"
                f" · [result](transformations/{result['transformation_id']}/result.json)"
            )
            status = "complete" if result["automatic_passed"] else "automatic checks failed"
            detailed.append(
                f"| [ ] | {index} | {result['target_id']} | {status}"
                f" | {result['generated_page_count']}/{input_by_id[result['target_id']]['page_count']}"
                f" | {result['champion_round']} | {result['stop_reason']} | {links} |"
            )
        else:
            detailed.append(
                f"| [ ] | {index} | {result['target_id']}"
                f" | failed | — | — | — | [result](transformations/{result['transformation_id']}/result.json) |"
            )
    root.joinpath("RESULTS_INDEX.md").write_text(
        (
            f"# A-pipeline {revision} D/E/F review\n\nAutomated state is recorded below. **Owner must eyeball L2; no visual-quality pass is claimed.**\n\n- Completed: **"
            f"{len(completed)}/{len(results)}**\n- Failed: **{len(failures)}**\n- Content preservation passed: **"
            f"{sum(result.get('content_preserved', False) for result in results)}/{len(results)}"
            f"**\n- Page-count differences: **"
            f"{sum(result.get('generated_page_count') != input_by_id[result['target_id']]['page_count'] for result in completed)}"
            "**\n- Numeric similarity and delta: **not part of this experiment**\n\n## Input key\n\n"
        )
        + "\n".join(f"- `{row['id']}`: `{row['filename']}` ({row['page_count']} pages)" for row in inputs)
        + "\n\n## Champion-round matrix\n\n| Source \\ Target | resume_d | resume_e | resume_f |\n| --- | --- | --- | --- |\n"
        + "\n".join(matrix_rows)
        + "\n\n## Review first\n\n"
        + (
            "\n".join(f"- `{row['transformation_id']}`: {row['error_message']}" for row in failures)
            if failures
            else "- No run failures; inspect every target/champion pair visually."
        )
        + "\n\n## All transformations\n\n| Check | # | Target design | Automated result | Pages | Champion | Stop | Open |\n| --- | ---: | --- | --- | ---: | ---: | --- | --- |\n"
        + "\n".join(detailed)
        + "\n\n[Run summary](RUN_README.md) · [CSV](transformation_matrix.csv) · [Content verification](content_preservation_verification.json) · [Manifest](run_manifest.json)\n",
        encoding="utf-8",
    )
    estimated_costs = [result.get("estimated_openai_cost_usd") for result in results]
    cost = sum(value for value in estimated_costs if value is not None)
    cost_label = (
        "recorded-token lower bound"
        if any(result.get("cost_incomplete") for result in results)
        else "estimated DeepSeek cost"
    )
    pair_lines: list[str] = []
    for result in results:
        if result["status"] != "completed":
            round_lines = [
                f"- Round {row['round']}: gates **{'PASS' if row['hard_gates']['passed'] else 'FAIL'}**; visual `skipped`"
                for row in result.get("rounds", [])
            ]
            pair_lines.append(
                f"### {result['transformation_id']} — FAILED\n\n{result['error_message']}\n\n"
                + ("\n".join(round_lines) + "\n\n" if round_lines else "")
                + f"- Calls: `{json.dumps(result['calls'], sort_keys=True)}`\n"
                + f"- Estimated recorded-token cost: `{result.get('estimated_openai_cost_usd')}` USD"
                + (" (incomplete: some provider usage was not persisted)" if result.get("cost_incomplete") else "")
            )
        else:
            rounds = result["rounds"]
            round_lines = []
            for row in rounds:
                visual = row.get("resolved_visual_comparison") or row.get("visual_comparison") or {}
                round_lines.append(
                    f"- Round {row['round']}: gates **{'PASS' if row['hard_gates']['passed'] else 'FAIL'}**"
                    f"; preference `{visual.get('preference', 'skipped')}`; promoted `{row.get('promoted', False)}`"
                )
            pair_lines.append(
                f"### {result['transformation_id']}\n\nChampion round **{result['champion_round']}"
                f"**; stop `{result['stop_reason']}`; later challenger rejected: **"
                f"{'yes' if result['later_round_rejected'] else 'no'}**.\n\n"
                + "\n".join(round_lines)
                + (
                    f"\n\n- Calls: `{json.dumps(result['calls'], sort_keys=True)}`\n- Estimated DeepSeek cost: `{result.get('estimated_openai_cost_usd')}` USD; Adobe monetary cost is not guessed.\n- Owner L2: [target](transformations/{result['transformation_id']}/artifacts/target.pdf) vs [champion]({result['generated_pdf']})\n- [Per-pair report](transformations/{result['transformation_id']}/artifacts/RUN_README.md)\n"
                )
            )
    root.joinpath("RUN_README.md").write_text(
        (
            f"# A-pipeline {revision} D/E/F live matrix\n\n**Owner must eyeball L2. This run does not declare visual quality passed.**\n\n- Dataset: `owner_attested_fake_resume_corpus`\n- Completed: **"
            f"{len(completed)}/{len(results)}**\n- Actual calls: `{json.dumps(dict(calls), sort_keys=True)}`\n- Total LLM calls: **"
            f"{sum(calls[name] for name in ('builder', 'filler', 'visual'))}**\n- "
            f"{cost_label.capitalize()}: **${cost:.4f}**; Adobe: **{calls['adobe']}** Document Transactions, monetary cost not guessed.\n\n"
        )
        + "\n\n".join(pair_lines)
        + "\n",
        encoding="utf-8",
    )



def validate_matrix_run(root: Path) -> list[str]:
    manifest = json.loads(root.joinpath("run_manifest.json").read_text())
    results = manifest["transformations"]
    with root.joinpath("transformation_matrix.csv").open(encoding="utf-8") as handle:
        csv_rows = list(csv.DictReader(handle))
    checks = [
        (
            len(results) == 6 and len({row["transformation_id"] for row in results}) == 6,
            "manifest has six unique transformations",
        ),
        (len(csv_rows) == len(results), "CSV row count matches manifest"),
        (
            all(
                root.joinpath(row["generated_pdf"]).exists() and root.joinpath(row["first_page_diff"]).exists()
                for row in results
                if row["status"] == "completed"
            ),
            "completed PDF and diff links resolve",
        ),
        (
            all(root.joinpath("transformations", row["transformation_id"], "result.json").exists() for row in results),
            "every transformation has result.json",
        ),
    ]
    markdown = root.joinpath("RESULTS_INDEX.md").read_text()
    links = re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", markdown)
    checks.append((all(root.joinpath(link).exists() for link in links), "RESULTS_INDEX links resolve"))
    failures = [label for passed, label in checks if not passed]
    if failures:
        raise RuntimeError("Matrix validation failed: " + "; ".join(failures))
    return ["PASS: " + label for _passed, label in checks]



def run_def_matrix(output: Path | None = None) -> Path:
    root = output or ROOT / "tests/local_datasets/resume_matrix/runs" / datetime.now(UTC).strftime("matrix_%Y%m%dT%H%M%SZ")
    root.mkdir(parents=True, exist_ok=False)
    started = datetime.now(UTC).isoformat()
    inputs = [_input_record(letter) for letter in "DEF"]
    run_target_cache: dict[str, Any] = {}
    calls: Counter[str] = Counter()
    results: list[dict[str, Any]] = []
    for source, target in ((source, target) for source in "DEF" for target in "DEF" if source != target):
        source_id, target_id = f"resume_{source.casefold()}", f"resume_{target.casefold()}"
        transformation_id = f"{source_id}__to__{target_id}"
        pair_dir = root / "transformations" / transformation_id
        pair_dir.mkdir(parents=True)
        before_calls = calls.copy()
        started_pair = time.perf_counter()
        result = {
            "transformation_id": transformation_id,
            "source_id": source_id,
            "target_id": target_id,
            "status": "failed",
        }
        try:
            artifacts = run(
                ROOT / f"tests/local_datasets/resume_matrix/resume_{target}.pdf",
                ROOT / f"tests/local_datasets/resume_matrix/resume_{source}.pdf",
                pair_dir / "artifacts",
                run_target_cache,
                calls,
            )
            refinement = json.loads(artifacts.joinpath("refinement_report.json").read_text())
            l0 = json.loads(artifacts.joinpath("l0_result.json").read_text())
            l1 = json.loads(artifacts.joinpath("l1_result.json").read_text())
            independent = json.loads(artifacts.joinpath("independent_checks.json").read_text())
            metadata = json.loads(artifacts.joinpath("run_metadata.json").read_text())
            result.update({
                "status": "completed",
                "latency_seconds": round(time.perf_counter() - started_pair, 3),
                "champion_round": refinement["champion_round"],
                "stop_reason": refinement["stop_reason"],
                "rounds": refinement["rounds"],
                "later_round_rejected": any(
                    row["round"] > refinement["champion_round"] and not row.get("promoted", False)
                    for row in refinement["rounds"]
                ),
                "hard_gate_valid": next(
                    row["hard_gates"]["passed"]
                    for row in refinement["rounds"]
                    if row["round"] == refinement["champion_round"]
                ),
                "l0_passed": bool(l0["passed"] and not l0["missing_source_content"]),
                "content_preserved": bool(l0["passed"] and not l0["missing_source_content"]),
                "automatic_passed": bool(
                    l0["passed"]
                    and not l0["missing_source_content"]
                    and l1["passed"]
                    and refinement["stop_reason"] == "acceptable_champion"
                ),
                "reviewer_accepted": refinement["stop_reason"] == "acceptable_champion",
                "calls": metadata["calls"],
                "estimated_openai_cost_usd": metadata["estimated_openai_cost_usd"],
            })
            _project_completed_pair(root, pair_dir, result)
            shared_source = root / "shared/source_analysis" / source_id
            shared_target = root / "shared/target_analysis" / target_id
            shared_source.mkdir(parents=True, exist_ok=True)
            shared_target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(artifacts / "source_text.txt", shared_source / "source_text.txt")
            for name in ("adobe_raw.json", "enriched_evidence.json", "format_summary.json", "empty_template.html"):
                if shared_target.joinpath(name).exists():
                    continue
                shutil.copy2(artifacts / name, shared_target / name)
        except Exception as error:  # noqa: BLE001
            result.update({
                "latency_seconds": round(time.perf_counter() - started_pair, 3),
                "error_type": type(error).__name__,
                "error_kind": getattr(error, "kind", "gate" if "hard-gate" in str(error).casefold() else "model"),
                "error_message": str(error),
                "calls": {name: calls[name] - before_calls[name] for name in ("adobe", "builder", "filler", "visual")},
            })
            metadata_path = pair_dir / "artifacts/run_metadata.json"
            if metadata_path.exists():
                metadata = json.loads(metadata_path.read_text())
                result["estimated_openai_cost_usd"] = metadata["estimated_openai_cost_usd"]
            refinement_path = pair_dir / "artifacts/refinement_report.json"
            if refinement_path.exists():
                refinement = json.loads(refinement_path.read_text())
                result.update({
                    "champion_round": refinement["champion_round"],
                    "stop_reason": refinement["stop_reason"],
                    "rounds": refinement["rounds"],
                    "hard_gate_valid": False,
                    "content_preserved": False,
                })
        pair_dir.joinpath("result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(result)
    _write_matrix_reports(root, started, inputs, results, calls)
    validation = validate_matrix_run(root)
    validation_text = "\n".join(validation) + "\n"
    root.joinpath("validation.txt").write_text(validation_text, encoding="utf-8")
    validation_name = "a_pipeline_matrix_validation.txt"
    validation_log = ROOT / "tests/test_results/pytest" / datetime.now(UTC).strftime(f"%Y%m%dT%H%M%SZ_{validation_name}")
    validation_log.write_text(validation_text, encoding="utf-8")
    return root



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="required acknowledgement for real provider calls")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--matrix-def", action="store_true", help="run all six D/E/F cross-pairs")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required because this experiment makes live provider calls")
    if args.matrix_def:
        result = run_def_matrix(args.output.resolve() if args.output else None)
        print(result)
        rows = json.loads(result.joinpath("run_manifest.json").read_text())["transformations"]
        if any(row["status"] != "completed" or not row.get("automatic_passed", False) for row in rows):
            raise SystemExit(1)
    else:
        print(run(args.target.resolve(), args.source.resolve(), args.output.resolve() if args.output else None))



if __name__ == "__main__":
    main()
