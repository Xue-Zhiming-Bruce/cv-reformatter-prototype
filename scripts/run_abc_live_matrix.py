from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from statistics import median
from typing import Any

import pdfplumber
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import main as main_api  # noqa: E402
from app.main import app  # noqa: E402
from app.template_analysis.commercial import (  # noqa: E402
    NormalizedLayoutEvidence,
    enrich_badges_from_local_pdf,
)
from app.template_analysis.visual_comparator import (  # noqa: E402
    compare_pdf_layouts,
    build_side_by_side_comparison,
)


DATASET_DIR = PROJECT_ROOT / "tests" / "local_datasets" / "resume_matrix"
ALL_PAIRS = ["A:B", "A:C", "B:A", "B:C", "C:A", "C:B"]
STEPS = [
    "server",
    "process_source",
    "approve_profile",
    "upload_target",
    "request_design",
    "approve_design",
    "layout_proofs",
    "generate",
]
RULE_GEOMETRY_TOLERANCE_PT = 1.0
BODY_SIZE_TOLERANCE_PT = 0.25
STRUCTURE_SIZE_TOLERANCE_PT = 0.25
# Re-baselined for the HTML→Chrome path (ADR 0006, 2026-08-31). Browser font
# resolution produces small per-label width differences from the fonts embedded
# in the targets; the gate still fails closed on genuine layout regressions.
HEADING_WIDTH_TOLERANCE_PT = 3.5


def _measure_dominant_text_size(path: Path) -> float | None:
    weights: Counter[float] = Counter()
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for character in page.chars:
                if str(character.get("text") or "").strip():
                    weights[round(float(character["size"]), 2)] += 1
    return weights.most_common(1)[0][0] if weights else None


def _body_size_failure(
    target_size_pt: float | None,
    generated_size_pt: float | None,
) -> str | None:
    if target_size_pt is None or generated_size_pt is None:
        return (
            None
            if target_size_pt == generated_size_pt
            else "dominant body size is missing"
        )
    difference = abs(target_size_pt - generated_size_pt)
    if difference > BODY_SIZE_TOLERANCE_PT:
        return (
            f"dominant body size drift={difference:.2f}pt exceeds "
            f"{BODY_SIZE_TOLERANCE_PT:.2f}pt"
        )
    return None


def _measure_pdf_badges(path: Path) -> dict[str, Any]:
    evidence = enrich_badges_from_local_pdf(
        NormalizedLayoutEvidence(provider="local_pdf", page_count=0, full_text=""),
        path,
    )
    clusters = evidence.badge_clusters
    return {
        "count": sum(cluster.badge_count for cluster in clusters),
        "fill_colors": sorted({cluster.fill_color_hex for cluster in clusters}),
        "text_colors": sorted({cluster.text_color_hex for cluster in clusters}),
        "items_per_line": [
            count for cluster in clusters for count in cluster.items_per_line
        ],
    }


def _measure_html_badges(path: Path) -> dict[str, Any]:
    class BadgeParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.badges: list[dict[str, str | None]] = []
            self.rows: list[int] = []
            self._row: int | None = None

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            classes = set((values.get("class") or "").split())
            if "chip-row" in classes:
                self._row = 0
            elif "chip-style" in classes:
                style = dict(
                    item.split(":", 1) for item in (values.get("style") or "").split(";") if ":" in item
                )
                self.badges.append({"fill": style.get("background"), "text": style.get("color")})
                if self._row is not None:
                    self._row += 1

        def handle_endtag(self, tag):
            if tag == "div" and self._row is not None:
                self.rows.append(self._row)
                self._row = None

    parser = BadgeParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return {
        "count": len(parser.badges),
        "fill_colors": sorted({item["fill"] for item in parser.badges if item["fill"]}),
        "text_colors": sorted({item["text"] for item in parser.badges if item["text"]}),
        "items_per_line": parser.rows,
    }


def _badge_failures(
    target: dict[str, Any], generated_html: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    for field in ("count", "fill_colors", "text_colors", "items_per_line"):
        if target[field] != generated_html[field]:
            failures.append(
                f"badge {field} target={target[field]!r} generated={generated_html[field]!r}"
            )
    return failures


def _measure_pdf_structure(path: Path) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    rules: list[dict[str, float | int]] = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            grouped: dict[float, list[dict[str, Any]]] = {}
            for word in page.extract_words(extra_attrs=["size", "fontname"]):
                grouped.setdefault(round(float(word["top"]), 1), []).append(word)
            for words in grouped.values():
                ordered = sorted(words, key=lambda word: float(word["x0"]))
                lines.append(
                    {
                        "page_number": page_number,
                        "top_pt": min(float(word["top"]) for word in ordered),
                        "bottom_pt": max(float(word["bottom"]) for word in ordered),
                        "text": " ".join(str(word["text"]) for word in ordered),
                        "size_pt": median(float(word["size"]) for word in ordered),
                        "width_pt": max(float(word["x1"]) for word in ordered)
                        - min(float(word["x0"]) for word in ordered),
                        "bold": any(
                            "bold" in str(word.get("fontname") or "").casefold()
                            for word in ordered
                        ),
                    }
                )
            seen: set[tuple[float | int, ...]] = set()
            for item in [*page.lines, *page.rects]:
                x0, x1 = float(item.get("x0") or 0), float(item.get("x1") or 0)
                top = float(item.get("top") or 0)
                bottom = float(item.get("bottom") or top)
                stroke_width = float(
                    item.get("linewidth") or abs(bottom - top) or 0
                )
                if (
                    abs(x1 - x0) < float(page.width) * 0.5
                    or abs(bottom - top) > 1.5
                    or not 0.25 <= stroke_width <= 6.0
                ):
                    continue
                key = (
                    page_number,
                    round(min(x0, x1), 2),
                    round(max(x0, x1), 2),
                    round(top, 2),
                    round(bottom, 2),
                    round(stroke_width, 2),
                )
                if key not in seen:
                    seen.add(key)
                    rules.append({"page_number": page_number, "top_pt": top})
    lines.sort(key=lambda line: (line["page_number"], line["top_pt"]))
    rules.sort(key=lambda rule: (rule["page_number"], rule["top_pt"]))
    if not rules:
        return {"header_lines": [], "section_labels": [], "entry_title_size_pt": None}

    first_rule = rules[0]
    header_lines = [
        line
        for line in lines
        if line["page_number"] == first_rule["page_number"]
        and line["bottom_pt"] <= first_rule["top_pt"] + 0.1
    ]
    section_labels: list[dict[str, Any]] = []
    for rule in rules[1:]:
        candidates = [
            line
            for line in lines
            if line["page_number"] == rule["page_number"]
            and 0 <= rule["top_pt"] - line["bottom_pt"] <= 15
        ]
        if candidates:
            label = max(candidates, key=lambda line: line["bottom_pt"])
            section_labels.append(
                {
                    "text": label["text"],
                    "size_pt": label["size_pt"],
                    "width_pt": label["width_pt"],
                }
            )
    body_size = _measure_dominant_text_size(path)
    label_texts = {label["text"].casefold() for label in section_labels}
    entry_sizes = [
        float(line["size_pt"])
        for line in lines
        if line["bold"]
        and line["text"].casefold() not in label_texts
        and line not in header_lines
        and body_size is not None
        and float(line["size_pt"]) > body_size
        and float(line["size_pt"])
        < max(float(item["size_pt"]) for item in header_lines)
    ]
    return {
        "header_lines": [
            {"text": line["text"], "size_pt": line["size_pt"]}
            for line in header_lines
        ],
        "section_labels": section_labels,
        "entry_title_size_pt": median(entry_sizes) if entry_sizes else None,
    }


def _structure_failures(
    target: dict[str, Any],
    generated: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    for field in ("header_lines", "section_labels"):
        target_items, generated_items = target[field], generated[field]
        if [item["text"] for item in target_items] != [
            item["text"] for item in generated_items
        ]:
            failures.append(f"{field} text/order differs from target")
            continue
        for index, (target_item, generated_item) in enumerate(
            zip(target_items, generated_items)
        ):
            drift = abs(float(target_item["size_pt"]) - float(generated_item["size_pt"]))
            if drift > STRUCTURE_SIZE_TOLERANCE_PT:
                failures.append(
                    f"{field}[{index}].size drift={drift:.2f}pt exceeds "
                    f"{STRUCTURE_SIZE_TOLERANCE_PT:.2f}pt"
                )
            if field == "section_labels":
                width_drift = abs(
                    float(target_item["width_pt"])
                    - float(generated_item["width_pt"])
                )
                if width_drift > HEADING_WIDTH_TOLERANCE_PT:
                    failures.append(
                        f"{field}[{index}].width drift={width_drift:.2f}pt exceeds "
                        f"{HEADING_WIDTH_TOLERANCE_PT:.2f}pt"
                    )
    target_entry = target["entry_title_size_pt"]
    generated_entry = generated["entry_title_size_pt"]
    if target_entry is None or generated_entry is None:
        if target_entry != generated_entry:
            failures.append("entry title size is missing")
    elif abs(float(target_entry) - float(generated_entry)) > STRUCTURE_SIZE_TOLERANCE_PT:
        failures.append("entry title size differs from target")
    return failures


def _measure_pdf_rules(path: Path) -> dict[str, Any]:
    rules: list[dict[str, float | int]] = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            words = page.extract_words()
            seen: set[tuple[float | int, ...]] = set()
            for item in [*page.lines, *page.rects]:
                x0 = float(item.get("x0") or 0)
                x1 = float(item.get("x1") or 0)
                top = float(item.get("top") or 0)
                bottom = float(item.get("bottom") or top)
                height = abs(bottom - top)
                stroke_width = float(item.get("linewidth") or height or 0)
                if (
                    abs(x1 - x0) < float(page.width) * 0.5
                    or height > 1.5
                    or not 0.25 <= stroke_width <= 6.0
                ):
                    continue
                left, right = min(x0, x1), max(x0, x1)
                key = (
                    page_number,
                    round(left, 2),
                    round(right, 2),
                    round(top, 2),
                    round(bottom, 2),
                    round(stroke_width, 2),
                )
                if key in seen:
                    continue
                seen.add(key)
                overlapping = [
                    word
                    for word in words
                    if float(word["x1"]) >= left and float(word["x0"]) <= right
                ]
                above = [
                    word for word in overlapping if float(word["bottom"]) <= top + 0.1
                ]
                below = [
                    word for word in overlapping if float(word["top"]) >= bottom - 0.1
                ]
                rules.append(
                    {
                        "page_number": page_number,
                        "top_pt": top,
                        "length_pt": right - left,
                        "gap_above_pt": (
                            top - max(float(word["bottom"]) for word in above)
                            if above
                            else 0.0
                        ),
                        "gap_below_pt": (
                            min(float(word["top"]) for word in below) - bottom
                            if below
                            else 0.0
                        ),
                    }
                )
    rules.sort(key=lambda rule: (rule["page_number"], rule["top_pt"]))
    if not rules:
        return {"count": 0, "header": None, "heading": None}
    header, headings = rules[0], rules[1:]
    return {
        "count": len(rules),
        "header": header,
        "heading": (
            {
                "length_pt": median(float(rule["length_pt"]) for rule in headings),
                "gap_above_pt": median(
                    float(rule["gap_above_pt"]) for rule in headings
                ),
                "gap_below_pt": min(
                    float(rule["gap_below_pt"]) for rule in headings
                ),
            }
            if headings
            else None
        ),
    }


def _rule_geometry_failures(
    target: dict[str, Any], generated: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    if target["count"] != generated["count"]:
        failures.append(
            f"rule count target={target['count']} generated={generated['count']}"
        )
    for role in ("header", "heading"):
        target_role, generated_role = target[role], generated[role]
        if target_role is None or generated_role is None:
            if target_role != generated_role:
                failures.append(f"{role} rule geometry is missing")
            continue
        for field in ("length_pt", "gap_above_pt", "gap_below_pt"):
            difference = abs(float(target_role[field]) - float(generated_role[field]))
            if difference > RULE_GEOMETRY_TOLERANCE_PT:
                failures.append(
                    f"{role}.{field} drift={difference:.2f}pt exceeds "
                    f"{RULE_GEOMETRY_TOLERANCE_PT:.1f}pt"
                )
    return failures


def _response_error(response: Any) -> str:
    try:
        return json.dumps(response.json(), ensure_ascii=False, sort_keys=True)
    except ValueError:
        return response.text


def _request(
    client: TestClient,
    result: dict[str, Any],
    step: str,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict[str, Any] | None:
    started = time.monotonic()
    try:
        response = client.request(method, url, **kwargs)
    except Exception:
        result["steps"][step] = {
            "status": "fail",
            "error": traceback.format_exc(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        return None
    elapsed = round(time.monotonic() - started, 3)
    if response.status_code != 200:
        result["steps"][step] = {
            "status": "fail",
            "http_status": response.status_code,
            "error": _response_error(response),
            "elapsed_seconds": elapsed,
        }
        return None
    result["steps"][step] = {
        "status": "pass",
        "http_status": response.status_code,
        "elapsed_seconds": elapsed,
    }
    return response.json()


def _nonempty(path: Path) -> dict[str, Any]:
    exists = path.is_file()
    return {
        "path": str(path.resolve()),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else 0,
        "nonempty": exists and path.stat().st_size > 0,
    }


def _run_pair(client: TestClient, source: str, target: str, *, strategy: str = "legacy", run_dir: Path | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "pair": f"{source}→{target}",
        "artifact_id": None,
        "approved_profile_version_id": None,
        "design_id": None,
        "steps": {step: {"status": "not_run"} for step in STEPS},
        "files": {},
        "observations": [],
    }

    health = _request(client, result, "server", "GET", "/health")
    if health is None or health.get("status") != "ok":
        if health is not None:
            result["steps"]["server"] = {
                "status": "fail",
                "error": f"Unexpected health response: {health!r}",
            }
        return result

    source_path = DATASET_DIR / f"resume_{source}.pdf"
    target_path = DATASET_DIR / f"resume_{target}.pdf"
    process = _request(
        client,
        result,
        "process_source",
        "POST",
        "/api/process",
        files={"file": (source_path.name, source_path.read_bytes(), "application/pdf")},
    )
    if process is None:
        return result
    artifact_id = process["artifact_id"]
    result["artifact_id"] = artifact_id
    artifact_dir = (PROJECT_ROOT / main_api.GENERATED_OUTPUTS_DIR / artifact_id).resolve()
    segmentation_metadata_path = artifact_dir / "candidate_segmentation.json"
    if segmentation_metadata_path.exists():
        result["candidate_segmentation"] = json.loads(
            segmentation_metadata_path.read_text(encoding="utf-8")
        )
    if process.get("original_preview_error"):
        result["observations"].append(
            f"original_preview_error: {process['original_preview_error']}"
        )

    if strategy == "pipeline_e":
        assert run_dir is not None
        return _run_e_pair(client, source, target, process, result, run_dir)

    profile = process["profile"]
    pending_sections = [
        section
        for section in profile.get("additional_sections", [])
        if section.get("review_state") == "pending_review"
    ]
    for section in pending_sections:
        section["review_state"] = "reviewed"
    if pending_sections:
        result["observations"].append(
            "synthetic recruiter reviewed pending source sections before approval: "
            + ", ".join(section["source_heading"] for section in pending_sections)
        )

    approval = _request(
        client,
        result,
        "approve_profile",
        "POST",
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={"profile": profile, "reviewer_note": "ABC live matrix"},
    )
    if approval is None:
        return result
    version_id = approval["profile_version_id"]
    result["approved_profile_version_id"] = version_id

    target_upload = _request(
        client,
        result,
        "upload_target",
        "POST",
        "/api/target-format",
        data={"artifact_id": artifact_id},
        files={"file": (target_path.name, target_path.read_bytes(), "application/pdf")},
    )
    if target_upload is None:
        return result
    target_format = target_upload["target_format"]
    if not target_format.get("used_as_template_source"):
        result["steps"]["upload_target"] = {
            "status": "fail",
            "error": "Target upload returned used_as_template_source=false.",
        }
        return result
    for warning in target_format.get("analysis_warnings", []):
        result["observations"].append(f"target analysis warning: {warning}")

    design = _request(
        client,
        result,
        "request_design",
        "POST",
        "/api/designs/request",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
        },
    )
    if design is None:
        return result
    result["design_id"] = design["design_id"]
    if design.get("state") not in {"ready", "ready_with_review"}:
        result["steps"]["request_design"] = {
            "status": "fail",
            "error": json.dumps(design, ensure_ascii=False, sort_keys=True),
        }
        return result
    for warning in design.get("warnings", []):
        result["observations"].append(f"design warning: {warning}")
    if design.get("needs_review") or design.get("human_review_required"):
        result["observations"].append(
            "design response requires human review before any visual-quality judgment"
        )

    approved_design = _request(
        client,
        result,
        "approve_design",
        "POST",
        f"/api/artifacts/{artifact_id}/designs/{design['design_id']}/approve",
        json={"reviewer_note": "ABC live matrix"},
    )
    if approved_design is None:
        return result
    if approved_design.get("state") != "approved":
        result["steps"]["approve_design"] = {
            "status": "fail",
            "error": f"Unexpected design approval response: {approved_design!r}",
        }
        return result
    result["files"].update(
        {
            "normalized_layout": _nonempty(artifact_dir / "normalized_layout_evidence.json"),
            "target_evidence": _nonempty(artifact_dir / "target_layout_evidence.json"),
            "layout_template_spec": _nonempty(
                artifact_dir
                / "designs"
                / design["design_id"]
                / "layout_template_spec.json"
            ),
        }
    )

    proofs = _request(
        client,
        result,
        "layout_proofs",
        "POST",
        f"/api/artifacts/{artifact_id}/layout-proofs",
        json={"design_id": design["design_id"]},
    )
    if proofs is None:
        return result
    variants = proofs.get("variants", [])
    if {variant.get("variant") for variant in variants} != {"short", "medium", "long"}:
        result["steps"]["layout_proofs"] = {
            "status": "fail",
            "error": f"Expected short/medium/long variants, got: {variants!r}",
        }
        return result
    proof_files: list[dict[str, Any]] = []
    for variant in variants:
        proof_dir = artifact_dir / "layout_proofs" / variant["proof_id"]
        proof_files.extend(
            [_nonempty(proof_dir / variant["html_filename"]), _nonempty(proof_dir / variant["pdf_filename"])]
        )
    result["files"]["proof_variants"] = proof_files
    if not all(item["nonempty"] for item in proof_files):
        result["steps"]["layout_proofs"] = {
            "status": "fail",
            "error": f"One or more proof files are missing or empty: {proof_files!r}",
        }
        return result

    proof_approval = _request(
        client,
        result,
        "layout_proofs",
        "POST",
        f"/api/artifacts/{artifact_id}/layout-proofs/approve",
        json={
            "state": "approved",
            "reviewer_id": "abc-live-matrix",
            "reviewer_note": "Automated pipeline-fact approval; no visual-quality judgment.",
            "design_id": design["design_id"],
        },
    )
    if proof_approval is None:
        return result
    if proof_approval.get("state") != "approved":
        result["steps"]["layout_proofs"] = {
            "status": "fail",
            "error": f"Unexpected proof approval response: {proof_approval!r}",
        }
        return result

    generated = _request(
        client,
        result,
        "generate",
        "POST",
        "/api/generate",
        json={
            "artifact_id": artifact_id,
            "approved_profile_version_id": version_id,
            "design_id": design["design_id"],
        },
    )
    if generated is None:
        return result

    final_pdf_path = artifact_dir / "candidate_profile.pdf"
    review_pdf_path = artifact_dir / f"resume_{source}_to_resume_{target}.pdf"
    if final_pdf_path.exists():
        shutil.copyfile(final_pdf_path, review_pdf_path)

    result["files"].update(
        {
            "final_html": _nonempty(artifact_dir / "candidate_profile.html"),
            "final_pdf": _nonempty(final_pdf_path),
            "review_pdf": _nonempty(review_pdf_path),
            "content_validation": _nonempty(
                artifact_dir / "content_validation.json"
            ),
            "structure_validation": _nonempty(
                artifact_dir / "structure_validation.json"
            ),
            "visual_comparison": _nonempty(
                artifact_dir / "visual_comparison.json"
            ),
        }
    )
    required = [
        result["files"]["normalized_layout"],
        result["files"]["target_evidence"],
        result["files"]["layout_template_spec"],
        result["files"]["final_html"],
        result["files"]["final_pdf"],
        result["files"]["review_pdf"],
        result["files"]["content_validation"],
        result["files"]["structure_validation"],
        result["files"]["visual_comparison"],
        *result["files"]["proof_variants"],
    ]
    if not all(item["nonempty"] for item in required):
        result["steps"]["generate"] = {
            "status": "fail",
            "error": f"Required output is missing or empty: {required!r}",
        }
        return result
    content_validation = json.loads(
        (artifact_dir / "content_validation.json").read_text(encoding="utf-8")
    )
    structure_validation = json.loads(
        (artifact_dir / "structure_validation.json").read_text(encoding="utf-8")
    )
    result["content_validation"] = content_validation
    result["structure_validation"] = structure_validation
    if not content_validation.get("passed"):
        result["steps"]["generate"] = {
            "status": "fail",
            "error": "content_validation.json did not pass",
        }
        return result
    if not structure_validation.get("passed"):
        result["steps"]["generate"] = {
            "status": "fail",
            "error": "structure_validation.json did not pass",
        }
        return result
    side_by_side = build_side_by_side_comparison(
        target_path,
        final_pdf_path,
        artifact_dir,
    )
    result["files"]["side_by_side"] = [
        _nonempty(artifact_dir / filename) for filename in side_by_side
    ]
    if not all(item["nonempty"] for item in result["files"]["side_by_side"]):
        result["steps"]["generate"] = {
            "status": "fail",
            "error": "One or more side-by-side comparison pages are missing.",
        }
        return result
    target_rule_geometry = _measure_pdf_rules(target_path)
    generated_rule_geometry = _measure_pdf_rules(final_pdf_path)
    result["rule_geometry"] = {
        "tolerance_pt": RULE_GEOMETRY_TOLERANCE_PT,
        "target": target_rule_geometry,
        "generated": generated_rule_geometry,
    }
    failures = _rule_geometry_failures(
        target_rule_geometry,
        generated_rule_geometry,
    )
    target_body_size = _measure_dominant_text_size(target_path)
    generated_body_size = _measure_dominant_text_size(final_pdf_path)
    result["typography"] = {
        "body_size_tolerance_pt": BODY_SIZE_TOLERANCE_PT,
        "target_dominant_by_volume_size_pt": target_body_size,
        "generated_dominant_by_volume_size_pt": generated_body_size,
    }
    if body_size_failure := _body_size_failure(target_body_size, generated_body_size):
        failures.append(body_size_failure)
    target_structure = _measure_pdf_structure(target_path)
    generated_structure = _measure_pdf_structure(final_pdf_path)
    result["structure"] = {
        "font_size_tolerance_pt": STRUCTURE_SIZE_TOLERANCE_PT,
        "target": target_structure,
        "generated": generated_structure,
    }
    failures.extend(_structure_failures(target_structure, generated_structure))
    target_badges = _measure_pdf_badges(target_path)
    generated_badges = _measure_html_badges(artifact_dir / "candidate_profile.html")
    result["badges"] = {
        "target": target_badges,
        "generated_html": generated_badges,
    }
    failures.extend(_badge_failures(target_badges, generated_badges))
    if failures:
        result["steps"]["generate"] = {
            "status": "fail",
            "error": "; ".join(failures),
        }
    return result



def _run_e_pair(client: TestClient, source: str, target: str, process: dict[str, Any], result: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    directory = main_api.GENERATED_OUTPUTS_DIR / process["artifact_id"]
    source_dir = run_dir / "shared/source_analysis" / source
    source_dir.mkdir(parents=True, exist_ok=True)
    for name in ("candidate_profile.json", "raw_extracted_text.txt", "segmentation_audit.json", "candidate_segmentation.json", "candidate_field_evidence.json"):
        shutil.copy2(directory / name, source_dir / name)
    audit = json.loads((directory / "segmentation_audit.json").read_text())
    if audit["status"] != "passed":
        result["steps"]["process_source"] = {"status": "fail", "error_code": "segmentation_coverage_failed", "error": "Source-line audit failed; no approval or conversion attempted."}
        return result
    target_path = DATASET_DIR / f"resume_{target}.pdf"
    upload = _request(client, result, "upload_target", "POST", "/api/target-format", data={"artifact_id": process["artifact_id"]}, files={"file": (target_path.name, target_path.read_bytes(), "application/pdf")})
    if upload is None:
        return result
    result["observations"].append("Preview uses the persisted extraction revision. No profile, layout or final-file approval is automated.")
    started = time.monotonic()
    response = client.post(f"/api/artifacts/{process['artifact_id']}/preview-jobs", json={"profile_revision_id": process["profile_revision_id"], "blind_profile": False})
    if response.status_code != 202:
        result["steps"]["generate"] = {"status": "fail", "error": _response_error(response)}
        return result
    job = response.json()
    while job["state"] in {"queued", "running"}:
        time.sleep(2)
        response = client.get(f"/api/artifacts/{process['artifact_id']}/preview-jobs/{job['job_id']}")
        response.raise_for_status()
        job = response.json()
    result["preview_job"] = job
    result["steps"]["generate"] = {"status": "pass" if job["state"] == "succeeded" else "fail", "elapsed_seconds": round(time.monotonic() - started, 3)}
    if job["state"] != "succeeded":
        result["steps"]["generate"].update(job["error"] or {"error": job["state"]})
        return result
    generated = job["result"]
    assert not generated["client_ready"]
    output = run_dir / "transformations" / f"{source}__to__{target}" / "generation"
    output.mkdir(parents=True, exist_ok=True)
    for name in ("candidate_profile.html", "candidate_profile.pdf", "client_render_context.json", "generated_profile.json", "layout_template_spec.json", "quality_gates.json", "generation_metadata.json"):
        shutil.copy2(directory / name, output / ("effective_layout_template_spec.json" if name == "layout_template_spec.json" else name))
    template_dir = directory / "pipeline_e_template" / json.loads((directory / "generation_metadata.json").read_text())["target_sha256"]
    target_dir = run_dir / "shared/target_analysis" / target
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in ("layout_template_spec.json", "renderer_artifact.json"):
        shutil.copy2(template_dir / name, target_dir / name)
    shutil.copy2(template_dir / "renderer_artifact.json", output / "renderer_artifact.json")
    comparison_dir = output.parent / "comparison"
    comparison = compare_pdf_layouts(target_path, output / "candidate_profile.pdf", comparison_dir)
    result["visual_comparison"] = comparison.model_dump(mode="json")
    result["files"]["side_by_side"] = [str(comparison_dir / name) for name in build_side_by_side_comparison(target_path, output / "candidate_profile.pdf", comparison_dir)]
    gates = json.loads((output / "quality_gates.json").read_text())
    result["content_validation"] = gates["content"]
    result["quality_gates"] = gates
    result["generated"] = generated
    result["output_paths"] = {"pdf": str(output / "candidate_profile.pdf"), "diff": str(comparison_dir / "comparison_page_001_diff.png"), "renderer": str(output / "renderer_artifact.json")}
    result["output_checksums"] = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in [("pdf", output / "candidate_profile.pdf"), ("html", output / "candidate_profile.html"), ("renderer", output / "renderer_artifact.json")]}
    return result


def _write_e_review(run_dir: Path, report: dict[str, Any]) -> None:
    rows = []
    for pair in report["pairs_requested"]:
        source, target = pair.split(":")
        result = next((item for item in report["pairs"] if item["pair"] == pair.replace(":", "→")), None)
        failed = next((value for value in result["steps"].values() if value["status"] == "fail"), None) if result else {"error": "Not run"}
        status = "completed" if result and result.get("generated") and not failed else "failed"
        comparison = result.get("visual_comparison", {}) if result else {}
        paths = {key: str(Path(value).relative_to(run_dir)) for key, value in result.get("output_paths", {}).items()} if result else {}
        row = {"transformation_id": source + "__to__" + target, "source_id": source, "target_id": target, "status": status, "latency_seconds": sum(step.get("elapsed_seconds", 0) for step in result["steps"].values()) if result else 0, "average_layout_similarity": comparison.get("average_layout_similarity"), "baseline_layout_similarity": None, "layout_similarity_delta": None, "generated_page_count": comparison.get("generated_page_count"), "target_page_count": comparison.get("target_page_count"), "added_section_ids": [], "recovered_candidate_block_ids": [], "generated_pdf": paths.get("pdf", ""), "output_paths": paths, "checksums": result.get("output_checksums", {}) if result else {}, "warnings": comparison.get("warnings", []), "error_message": (failed.get("error") or failed.get("message") or str(failed)) if failed else "", "error": failed}
        rows.append(row)
        folder = run_dir / "transformations" / row["transformation_id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "result.json").write_text(json.dumps({**row, "api_evidence": result}, indent=2, ensure_ascii=False))
    inputs = []
    for identifier in sorted({name for pair in report["pairs_requested"] for name in pair.split(":")}):
        path = DATASET_DIR / f"resume_{identifier}.pdf"
        with pdfplumber.open(path) as pdf:
            pages = len(pdf.pages)
        inputs.append({"id": identifier, "filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "page_count": pages, "size_bytes": path.stat().st_size})
    manifest = {"execution_schema_version": "pipeline-e-api-matrix/1", "result_format_version": "resume-matrix-review-1.0", "dataset_classification": "owner_attested_fake_resume_corpus", "state": "running" if not report.get("completed_at") else "completed" if all(row["status"] == "completed" for row in rows) else "completed_with_failures", "started_at": report["started_at"], "completed_at": report.get("completed_at"), "method": report.get("method", "live extraction -> audited CandidateProfile revision -> fresh E template construction -> real Chrome -> checked immutable preview"), "network_calls": report.get("network_calls", "Real configured extraction, Adobe and E builder/reviewer; details in local API/construction records."), "llm_calls": {"extraction_provider": report["llm_provider"], "pipeline_model": os.getenv("PIPELINE_MODEL", "deepseek-flash")}, "review_policy": "Unreviewed. No final acceptance, no visual winner, no automatic source-section review.", "inputs": inputs, "transformations": rows, "layout_proofs": {"evidence_version": "pipeline-e-content-shape-probes/1", "approval_state": "not_approved", "note": "E construction uses its canonical content-shape probes. Legacy template proof approvals are not applicable and are not fabricated."}}
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    columns = "transformation_id source_id target_id status average_layout_similarity baseline_layout_similarity layout_similarity_delta generated_page_count added_section_ids recovered_candidate_block_ids generated_pdf error_message".split()
    with (run_dir / "transformation_matrix.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, columns, extrasaction="ignore"); writer.writeheader(); writer.writerows(rows)
    completed = [row for row in rows if row["status"] == "completed"]
    preservation = {"passed": len(completed), "total": len(rows), "checks": "Only PDFs admitted by actual E content, leaf-identity, privacy, font and material geometry checks are counted. Failed/not-run cases are not passes.", "transformations": [{"id": row["transformation_id"], "passed": row["status"] == "completed"} for row in rows]}
    (run_dir / "content_preservation_verification.json").write_text(json.dumps(preservation, indent=2))
    counts = f"Completed {len(completed)} / {len(rows)}; failed {len(rows)-len(completed)}; content checks {len(completed)}/{len(rows)}; negative delta unmeasured (no baseline); page-count differences {sum(row['generated_page_count'] != row['target_page_count'] for row in completed)}."
    text = ["# Pipeline E live matrix review", "", "Automated run: " + manifest["state"] + ". Manual review: all pending. No final acceptance.", "", counts, "", "Layout similarity is diagnostic, not a quality gate. Delta is unmeasured without a comparable baseline.", "", "## Input key", ""]
    text += [f"- {item['id']}: {item['filename']} ({item['page_count']} pages)" for item in inputs]
    text += ["", "## Score matrix", "", "| Source → Target | Diagnostic score / PDF |", "| --- | --- |"]
    text += [f"| {row['source_id']} → {row['target_id']} | " + (f"[{row['average_layout_similarity']:.4f}]({row['generated_pdf']})" if row['status']=='completed' else "Failed") + " |" for row in rows]
    text += ["", "## Review first", ""]
    text += [f"- [{row['transformation_id']}](transformations/{row['transformation_id']}/result.json): {row['error_message']}" for row in rows if row['status']=='failed'] or ["- Inspect contact layout, typography, spacing and page flow in every output."]
    for source in sorted({row['source_id'] for row in rows}):
        text += ["", "## Source " + source, "", "| Check | # | Target design | Automated result | Pages | Score | Delta | Recovered | Added | Open |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for number, row in enumerate(rows, 1):
            if row['source_id'] != source: continue
            links = f"[failure](transformations/{row['transformation_id']}/result.json)"
            if row['status']=='completed':
                links = " · ".join(f"[{key}]({path})" for key,path in row['output_paths'].items())
            score = f"{row['average_layout_similarity']:.4f}" if row['status']=='completed' else "—"
            flag = " · pages" if row['status']=='completed' and row['generated_page_count']!=row['target_page_count'] else ""
            text.append(f"| ☐ | {number} | {row['target_id']} / resume_{row['target_id']}.pdf | {row['status']}{flag} | {row['generated_page_count'] or '—'} / {row['target_page_count'] or '—'} | {score} | — | unmeasured | unmeasured | {links} |")
    text += ["", "## Construction probes", "", "Canonical E content-shape probes and model usage are in api_artifacts/*/pipeline_e_template/*/construction_*/. No legacy proof approval or owner acceptance is asserted.", "", "[Run summary](RUN_README.md) · [CSV](transformation_matrix.csv) · [Content checks](content_preservation_verification.json) · [Manifest](run_manifest.json)"]
    (run_dir / "RESULTS_INDEX.md").write_text("\n".join(text)+"\n")
    (run_dir / "RUN_README.md").write_text("# Live E API matrix\n\n" + counts + "\n\nSource and target are the authorized public/fake PDF corpus. Only candidate profile facts enter output. Raw provider evidence stays local. Outputs are pending human review. No final acceptance is recorded.\n")
    assert len(rows) == len(set(row['transformation_id'] for row in rows)) == len(report['pairs_requested'])
    assert sum(1 for _ in csv.DictReader((run_dir / 'transformation_matrix.csv').open())) == len(rows)
    for row in completed:
        assert all((run_dir / path).is_file() for path in row['output_paths'].values())
    import re
    for link in re.findall(r"\]\(([^)]+)\)", (run_dir / 'RESULTS_INDEX.md').read_text()):
        assert (run_dir / link).exists(), link
    print("Matrix report validated:", counts, flush=True)

def main() -> int:
    parser = argparse.ArgumentParser(description="Run the authorized ABC live API matrix.")
    parser.add_argument(
        "--pairs",
        nargs="+",
        default=["A:B"],
        help="Pairs such as A:B A:C, or 'all'. Defaults to the required first gate A:B.",
    )
    parser.add_argument("--strategy", choices=["legacy", "pipeline_e"], default="legacy")
    parser.add_argument("--run-dir", type=Path, help="E matrix review directory under the canonical resume-matrix runs path.")
    parser.add_argument("--report", type=Path, help="Optional JSON report path.")
    args = parser.parse_args()
    pairs = ALL_PAIRS if args.pairs == ["all"] else args.pairs
    allowed_pairs = ALL_PAIRS if args.strategy == "legacy" else [f"{a}:{b}" for a in "ABCDFEI" for b in "ABCDFEI" if a != b and (DATASET_DIR / f"resume_{a}.pdf").exists() and (DATASET_DIR / f"resume_{b}.pdf").exists()]
    if len(set(pairs)) != len(pairs) or any(pair not in allowed_pairs for pair in pairs):
        parser.error(f"pairs must come from {', '.join(ALL_PAIRS)}")

    provider = (os.getenv("API_LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "openai").lower()
    if provider == "mock":
        parser.error("Live matrix refuses API_LLM_PROVIDER/LLM_PROVIDER=mock.")

    if args.strategy == "pipeline_e":
        if not args.run_dir or not args.run_dir.resolve().is_relative_to((DATASET_DIR / "runs").resolve()):
            parser.error("E requires --run-dir under tests/local_datasets/resume_matrix/runs/")
        if os.getenv("PIPELINE_E_LIVE_ENABLED", "").lower() not in {"1", "true", "yes"}:
            parser.error("E live matrix requires PIPELINE_E_LIVE_ENABLED=1.")
        args.run_dir = args.run_dir.resolve()
        args.run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        main_api.GENERATED_OUTPUTS_DIR = args.run_dir / "api_artifacts"
        main_api.LOCAL_DATABASE_PATH = args.run_dir / "metadata.sqlite3"
        os.environ["TEMPLATE_DESIGN_STRATEGY"] = "pipeline_e"

    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "llm_provider": provider,
        "pairs_requested": pairs,
        "expected_fail_observations": [
            {
                "pair": pair.replace(":", "→"),
                "status": "expected_fail_not_run",
                "reason": "Target A lacks the measured structure required by the generation gate.",
            }
            for pair in ("B:A", "C:A")
            if pair not in pairs
        ],
        "pairs": [],
    }
    with TestClient(app) as client:
        for pair in pairs:
            source, target = pair.split(":")
            print(f"RUN {source}→{target}", flush=True)
            result = _run_pair(client, source, target, strategy=args.strategy, run_dir=args.run_dir)
            report["pairs"].append(result)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True) if args.strategy == "legacy" else f"{pair}: generate={result['steps']['generate']['status']}", flush=True)
            if args.strategy == "pipeline_e":
                _write_e_review(args.run_dir, report)
            if args.strategy == "legacy" and any(step["status"] == "fail" for step in result["steps"].values()):
                break
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    if args.strategy == "pipeline_e":
        _write_e_review(args.run_dir, report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return int(len(report["pairs"]) != len(pairs) or any(
        step["status"] == "fail"
        for result in report["pairs"]
        for step in result["steps"].values()
    ))


if __name__ == "__main__":
    raise SystemExit(main())
