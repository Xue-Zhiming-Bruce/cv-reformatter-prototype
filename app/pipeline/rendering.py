"""Shared pipeline rendering implementation."""

from __future__ import annotations

import base64
import hashlib
import html as html_lib
import os
import re
import statistics
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
import pdfplumber
import pypdfium2 as pdfium
from app.generation.chrome_html_to_pdf import HtmlToPdfExportError, _find_chrome
from app.template_analysis.commercial.adobe import _flatten_text_elements
from app.template_analysis.commercial.models import NormalizedLayoutEvidence

ROOT = Path(__file__).resolve().parents[2]

RUNS = Path("data/generated_outputs/pipeline")

FORMAT_SUMMARY_VERSION = "a-pipeline-format-summary/3"

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

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

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

def _hyphen_free(value: str) -> str:
    """Owner decision 2026-09-09 (F->E matrix postmortem): Chrome breaks words at
    hyphens across lines ("Scikit-" / "learn") and read_pdf_text merges the
    fragments WITHOUT the hyphen ("Scikitlearn"), so hyphen-bearing source lines
    are permanently reported missing from the PDF even though every glyph
    rendered. PDF-side coverage therefore compares hyphen-free text on both
    sides; the HTML-side coverage stays strict (verbatim, hyphens included)."""
    return value.replace("-", "")

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

_LOCAL_FONT_DIR = Path(__file__).with_name("assets") / "fonts"

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

def _render_pages(pdf_path: Path, output_dir: Path, prefix: str) -> list[Path]:
    document = pdfium.PdfDocument(pdf_path)
    paths: list[Path] = []
    for index, page in enumerate(document):
        path = output_dir / f"{prefix}_page_{index + 1}.png"
        page.render(scale=2).to_pil().convert("RGB").save(path)
        paths.append(path)
    return paths
