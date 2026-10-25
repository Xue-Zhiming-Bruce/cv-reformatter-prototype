from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
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

from app.main import GENERATED_OUTPUTS_DIR, app  # noqa: E402
from app.template_analysis.commercial import (  # noqa: E402
    NormalizedLayoutEvidence,
    enrich_badges_from_local_pdf,
)
from app.template_analysis.visual_comparator import (  # noqa: E402
    build_side_by_side_comparison,
)


DATASET_DIR = PROJECT_ROOT / "tests" / "local_datasets" / "resume_matrix"
ALL_PAIRS = ["A:B", "A:C", "B:A", "B:C", "C:A", "C:B"]
STEPS = [
    "server",
    "process_source",
    "save_revision",
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


def _run_pair(client: TestClient, source: str, target: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "pair": f"{source}→{target}",
        "artifact_id": None,
        "profile_revision_id": None,
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
    artifact_dir = (PROJECT_ROOT / GENERATED_OUTPUTS_DIR / artifact_id).resolve()
    segmentation_metadata_path = artifact_dir / "candidate_segmentation.json"
    if segmentation_metadata_path.exists():
        result["candidate_segmentation"] = json.loads(
            segmentation_metadata_path.read_text(encoding="utf-8")
        )
    if process.get("original_preview_error"):
        result["observations"].append(
            f"original_preview_error: {process['original_preview_error']}"
        )

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
        "save_revision",
        "POST",
        f"/api/artifacts/{artifact_id}/profiles/revisions",
        json={"profile": profile},
    )
    if approval is None:
        return result
    version_id = approval["profile_revision_id"]
    result["profile_revision_id"] = version_id
    result["initial_profile_revision_id"] = process["profile_revision_id"]

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
            "profile_revision_id": version_id,
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
    result["layout_proof_summary"] = proofs
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
            "reviewer_id": "matrix_harness_synthetic_reviewer",
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
            "profile_revision_id": version_id,
            "design_id": design["design_id"],
        },
    )
    if generated is None:
        return result

    result["preview_identity"] = {key: generated[key] for key in (
        "profile_revision_id", "profile_sha256", "layout_template_version",
        "layout_spec_sha256", "html_sha256", "pdf_sha256", "client_ready")}
    if generated["client_ready"]:
        raise RuntimeError("Matrix preview must not receive final human acceptance")

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


def _write_review(run_dir: Path, report: dict[str, Any]) -> None:
    """Project the canonical matrix manifest into portable owner-review files."""
    def write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+"\n")

    def identity(path: Path) -> dict[str, Any]:
        with pdfplumber.open(path) as pdf:
            pages = len(pdf.pages)
        return {"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "page_count": pages, "size_bytes": path.stat().st_size}

    def link(path: Path, label: str) -> str:
        return f"[{label}]({path.relative_to(run_dir).as_posix()})"

    rows = []
    proofs = {}
    for pair in report["pairs"]:
        source, target = pair["pair"].split("→")
        source_id, target_id = f"resume_{source.lower()}", f"resume_{target.lower()}"
        pair_id = f"{source_id}__to__{target_id}"
        directory = run_dir/"transformations"/pair_id
        directory.mkdir(parents=True, exist_ok=True)
        artifact = Path(pair["files"].get("final_pdf", {}).get("path", directory/"absent")).parent
        if pair.get("artifact_id"):
            artifact = directory/"artifacts"/pair["artifact_id"]
        errors = [{"step": step, **data} for step, data in pair["steps"].items() if data["status"] == "fail"]
        row = {"transformation_id": pair_id, "source_id": source_id, "target_id": target_id,
               "status": "failed" if errors else "completed", "errors": errors,
               "average_layout_similarity": None, "baseline_layout_similarity": None,
               "layout_similarity_delta": None, "generated_page_count": None,
               "added_section_ids": [], "recovered_candidate_block_ids": [],
               "generated_pdf": None, "error_message": "; ".join(str(e["error"]) for e in errors),
               "latency_seconds": sum(step.get("elapsed_seconds", 0) for step in pair["steps"].values()),
               "warnings": pair["observations"], "api_evidence": pair, "outputs": {}}
        for role, names, input_id in (
            ("source", ["candidate_profile.json", "raw_extracted_text.txt", "normalized_candidate_document.json",
                        "candidate_field_evidence.json", "candidate_segmentation.json", "segmentation_audit.json"], source_id),
            ("target", ["target_layout_evidence.json", "normalized_layout_evidence.json", "adobe_raw_response.json",
                        "layout_template_spec.json"], target_id),
        ):
            snapshot = run_dir/"shared"/f"{role}_analysis"/input_id
            snapshot.mkdir(parents=True, exist_ok=True)
            for name in names:
                if (artifact/name).is_file():
                    shutil.copy2(artifact/name, snapshot/name)
            shutil.copy2(DATASET_DIR/f"resume_{source if role == 'source' else target}.pdf", snapshot/"input.pdf")
        proof_dir = artifact/"layout_proofs"
        if proof_dir.exists():
            snapshot = run_dir/"shared"/"layout_proofs"/target_id
            snapshot.mkdir(parents=True, exist_ok=True)
            shutil.copytree(proof_dir, snapshot/"layout_proofs", dirs_exist_ok=True)
            if (artifact/"layout_template_spec.json").is_file():
                shutil.copy2(artifact/"layout_template_spec.json", snapshot/"layout_template_spec.json")
            write_json(snapshot/"layout_proof_summary.json", pair.get("layout_proof_summary", {"state": "incomplete"}))
            proofs[target_id] = {"path": snapshot.relative_to(run_dir).as_posix(), "human_accepted": False,
                                 "negative_cases": {"state": "not_run", "reason": "Not exercised by this live matrix; canonical offline regressions remain separate evidence."}}
        if (artifact/"candidate_profile.pdf").is_file():
            generation, comparison = directory/"generation", directory/"comparison"
            generation.mkdir(exist_ok=True)
            comparison.mkdir(exist_ok=True)
            for path in artifact.iterdir():
                if path.is_file() and (path.name.startswith(("generated_page_", "comparison_", "side_by_side_")) or path.name == "visual_comparison.json"):
                    shutil.copy2(path, comparison/path.name)
            spec = Path(pair["files"]["layout_template_spec"]["path"])
            for src, dest in ((artifact/"candidate_profile.html", "candidate_profile.html"),
                              (artifact/"candidate_profile.pdf", "candidate_profile.pdf"),
                              (artifact/"client_render_context.json", "client_render_context.json"),
                              (artifact/"production_render_plan.json", "production_render_plan.json"),
                              (spec, "effective_layout_template_spec.json")):
                shutil.copy2(src, generation/dest)
            from app.extraction.candidate_document_analyzer import NormalizedCandidateDocument, build_candidate_block_binding_plan
            from app.template_analysis.schemas import LayoutTemplateSpec
            document = NormalizedCandidateDocument.model_validate_json((artifact/"normalized_candidate_document.json").read_text())
            layout = LayoutTemplateSpec.model_validate_json(spec.read_text())
            write_json(generation/"candidate_block_binding_plan.json", build_candidate_block_binding_plan(document, layout).model_dump(mode="json"))
            visual = json.loads((artifact/"visual_comparison.json").read_text())
            row.update(average_layout_similarity=visual["average_layout_similarity"],
                       generated_page_count=visual["generated_page_count"],
                       generated_pdf=(generation/"candidate_profile.pdf").relative_to(run_dir).as_posix())
            context = json.loads((artifact/"client_render_context.json").read_text())
            row["recovered_candidate_block_ids"] = [s["source_block_id"] for s in context.get("additional_sections", []) if s.get("source_block_id")]
            row["outputs"] = {str(p.relative_to(run_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in generation.iterdir() if p.is_file()}
        states = [json.loads(p.read_text()) for p in artifact.glob("**/pipeline/e5_state.json")]
        row["agentic_usage"] = [state["budget_state"] for state in states]
        row["human_review_state"] = "pending"
        write_json(directory/"result.json", row)
        rows.append(row)
    report.update(execution_schema_version="abc-live-api/2", result_format_version="resume-matrix-review-1.0",
        state="failed" if any(r["status"] == "failed" for r in rows) else "awaiting_owner_review",
        dataset_classification="owner_attested_fake_resume_corpus",
        method="canonical API matrix; existing bounded A runner; default whole-page Reviewer",
        review_policy={"final_output_acceptance": "human_pending", "synthetic_proof_approval_only": True},
        network_calls={"agentic_http_reservations": sum(b["model_request_count"] for r in rows for b in r["agentic_usage"]),
                       "adobe_http_count": None, "note": "Adobe HTTP count is not instrumented; API step timing is recorded."},
        llm_calls={"extraction": [r["api_evidence"]["candidate_segmentation"] for r in rows if "candidate_segmentation" in r["api_evidence"]],
                   "agentic_usage": [b for r in rows for b in r["agentic_usage"]], "billed_cost": None},
        inputs={key: identity(DATASET_DIR/f"resume_{key}.pdf") for key in sorted({v for p in report["pairs_requested"] for v in p.split(":")})},
        transformations=rows, layout_proofs={"evidence_version": "layout-proof-matrix-1.0",
            "reviewer_id": "matrix_harness_synthetic_reviewer", "targets": proofs})
    write_json(run_dir/"run_manifest.json", report)
    columns = ["transformation_id", "source_id", "target_id", "status", "average_layout_similarity",
               "baseline_layout_similarity", "layout_similarity_delta", "generated_page_count", "added_section_ids",
               "recovered_candidate_block_ids", "generated_pdf", "error_message"]
    with (run_dir/"transformation_matrix.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    verification = {"passed": sum(r["api_evidence"].get("content_validation", {}).get("passed", False) for r in rows),
                    "total": len(rows), "not_run": sum("content_validation" not in r["api_evidence"] for r in rows),
                    "manual_review": "pending"}
    write_json(run_dir/"content_preservation_verification.json", verification)
    completed = sum(r["status"] == "completed" for r in rows)
    index = ["# Agentic workflow review", "", "Automated: "+report["state"]+". Human review: pending. No final acceptance.",
             f"Completed {completed}; failed {len(rows)-completed}; content {verification['passed']}/{len(rows)}; negative deltas 0 (baseline not measured); page-count differences "+str(sum(r["generated_page_count"] is not None and r["generated_page_count"] != report["inputs"][r["target_id"][-1].upper()]["page_count"] for r in rows))+".",
             "", "Layout similarity is diagnostic, not a pass/fail gate. Baseline/delta are unmeasured.", "", "## Input key", ""]
    for key, value in report["inputs"].items():
        role = "source" if any(r["source_id"] == f"resume_{key.lower()}" for r in rows) else "target"
        path = run_dir/"shared"/f"{role}_analysis"/f"resume_{key.lower()}"/"input.pdf"
        index.append(f"- {key}: "+link(path, value["filename"])+f", {value['page_count']} pages.")
    index += ["", "## Source-by-target scores", "", "| Source | Target | Score / PDF |", "| --- | --- | --- |"]
    index += [f"| {r['source_id']} | {r['target_id']} | "+(link(run_dir/r["generated_pdf"], f"{r['average_layout_similarity']:.4f}") if r["generated_pdf"] else "failed")+" |" for r in rows]
    index += ["", "## Review first", ""]
    index += [f"- {r['transformation_id']}: {r['error_message']} — "+link(run_dir/"transformations"/r["transformation_id"]/"result.json", "failure evidence") for r in rows if r["status"] == "failed"]
    if report.get("offline_preflight_diagnosis"):
        index.append("- "+link(run_dir/report["offline_preflight_diagnosis"], "Offline diagnosis (no additional provider calls)"))
    index += ["", "## Results", "", "| Check | # | Target design | Automated result | Pages | Score | Delta | Recovered | Added | Open |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for number, row in enumerate(rows, 1):
        directory = run_dir/"transformations"/row["transformation_id"]
        opens = [link(directory/"result.json", "result")]
        for path, label in ((directory/"generation/candidate_profile.pdf", "PDF"), (directory/"comparison/comparison_page_001_diff.png", "diff"), (directory/"generation/production_render_plan.json", "plan")):
            if path.is_file():
                opens.append(link(path, label))
        target_pages = report["inputs"][row["target_id"][-1].upper()]["page_count"]
        flags = " pages" if row["generated_page_count"] is not None and row["generated_page_count"] != target_pages else ""
        score = "n/a" if row["average_layout_similarity"] is None else f"{row['average_layout_similarity']:.4f}"
        index.append(f"| [ ] | {number} | {row['target_id']} | {row['status']}{flags} | {row['generated_page_count'] or 'n/a'}/{target_pages} | {score} | n/a | {len(row['recovered_candidate_block_ids'])} | {len(row['added_section_ids'])} | {' · '.join(opens)} |")
    index += ["", "## Layout proofs (synthetic, evaluation-only)", ""]
    index += [f"- {target}: "+link(run_dir/data["path"]/"layout_proof_summary.json", "proof evidence")+"; no human fidelity acceptance." for target, data in proofs.items()]
    index += ["", " · ".join(link(run_dir/name, name) for name in ("RUN_README.md", "transformation_matrix.csv", "content_preservation_verification.json", "run_manifest.json")), ""]
    (run_dir/"RESULTS_INDEX.md").write_text("\n".join(index))
    (run_dir/"RUN_README.md").write_text("# Run scope\n\nExisting authorized A–C PDF corpus; candidate facts and target presentation stay separate.\nSource/configuration, API steps, usage and failures are in run_manifest.json.\nNo final acceptance call is made. Inspect actual outputs before accepting them.\n")
    assert len(rows) == len(report["pairs_requested"]) == len({r["transformation_id"] for r in rows})
    with (run_dir/"transformation_matrix.csv").open() as output:
        assert len(list(csv.DictReader(output))) == len(rows)
    for row in rows:
        if row["status"] == "completed":
            assert row["generated_pdf"] and (run_dir/row["generated_pdf"]).is_file()
            assert (run_dir/"transformations"/row["transformation_id"]/"comparison/comparison_page_001_diff.png").is_file()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the authorized ABC live API matrix.")
    parser.add_argument(
        "--pairs",
        nargs="+",
        default=["A:B"],
        help="Pairs such as A:B A:C, or 'all'. Defaults to the required first gate A:B.",
    )
    parser.add_argument("--report", type=Path, help="Optional extra copy of the run manifest.")
    parser.add_argument("--out", type=Path, help="New matrix run directory under the canonical local runs directory.")
    args = parser.parse_args()
    pairs = ALL_PAIRS if args.pairs == ["all"] else args.pairs
    if len(pairs) != len(set(pairs)):
        parser.error("duplicate pairs are not allowed")
    if any(pair not in ALL_PAIRS for pair in pairs):
        parser.error(f"pairs must come from {', '.join(ALL_PAIRS)}")

    provider = (os.getenv("API_LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "openai").lower()
    if provider == "mock":
        parser.error("Live matrix refuses API_LLM_PROVIDER/LLM_PROVIDER=mock.")

    run_dir = args.out or DATASET_DIR / "runs" / datetime.now(timezone.utc).strftime("matrix_%Y%m%dT%H%M%SZ_agentic_workflow")
    if not run_dir.resolve().is_relative_to((DATASET_DIR/"runs").resolve()):
        parser.error("--out must be under tests/local_datasets/resume_matrix/runs")
    run_dir.mkdir(parents=True, exist_ok=False)
    from app import main as api
    global GENERATED_OUTPUTS_DIR
    api.LOCAL_DATABASE_PATH = run_dir / "local.sqlite3"
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "generation_strategy": os.getenv("GENERATION_STRATEGY", "standard"),
        "execution_settings": {key: os.getenv(key) for key in ("AGENTIC_MAX_MODEL_REQUESTS", "AGENTIC_MAX_TOOL_CALLS", "AGENTIC_MAX_REPAIR_ROUNDS")},
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
            if report["pairs"] and any(step["status"] == "fail" for step in report["pairs"][-1]["steps"].values()):
                report["pairs"].append({"pair": pair.replace(":", "→"), "artifact_id": None, "files": {},
                    "observations": [], "steps": {"server": {"status": "fail", "error": "not_run_after_prior_failure"}}})
                continue
            source, target = pair.split(":")
            GENERATED_OUTPUTS_DIR = run_dir / "transformations" / f"resume_{source.lower()}__to__resume_{target.lower()}" / "artifacts"
            api.GENERATED_OUTPUTS_DIR = GENERATED_OUTPUTS_DIR
            print(f"RUN {source}→{target}", flush=True)
            result = _run_pair(client, source, target)
            report["pairs"].append(result)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)

    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_review(run_dir, report)
    print(f"REVIEW {run_dir / 'RESULTS_INDEX.md'}", flush=True)
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
