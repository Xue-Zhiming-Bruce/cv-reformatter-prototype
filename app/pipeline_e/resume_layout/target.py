"""Target-document measurement: Adobe evidence -> neutral visual format summary.

Split out of ``a_pipeline`` (and ``b_pipeline._page_text_lines``). This is the
target side of the library: it consumes provider-neutralized Adobe evidence plus
local pdfplumber measurements and produces the ``format_summary`` that the
layout-state compiler binds against. It never sees candidate content.
"""

from __future__ import annotations

from app.pipeline_e.resume_layout.paths import RUNS
from app.pipeline_e.resume_layout.text import (
    _median,
    _round,
    _sha256,
)
import json
import pdfplumber
import re
from app.template_analysis.commercial.adobe import _flatten_text_elements, run_adobe_layout
from app.template_analysis.commercial.local_color import enrich_badges_from_local_pdf, enrich_colors_from_local_pdf, enrich_rules_from_local_pdf
from app.template_analysis.commercial.models import NormalizedLayoutEvidence
from pathlib import Path
from typing import Any


ANALYZER_VERSION = "a-pipeline-path-grouper/1"


FORMAT_SUMMARY_VERSION = "a-pipeline-format-summary/3"


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


def _vertical_gap(previous: dict[str, Any], current: dict[str, Any]) -> float | None:
    left, right = previous.get("bbox_pt"), current.get("bbox_pt")
    if not left or not right or previous["page"] != current["page"]:
        return None
    return _round(right["top"] - left["bottom"])


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


def _parent_path(path: str) -> str:
    parts = _path_parts(path)
    return "//" + "/".join(parts[:-1]) if len(parts) > 1 else "//Document"


def _sect_paths(path: str) -> list[str]:
    parts = _path_parts(path)
    return [_path_prefix(parts, index) for index, part in enumerate(parts) if part.split("[", 1)[0] == "Sect"]


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
