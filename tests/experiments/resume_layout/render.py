"""Deterministic HTML -> PDF rendering: pinned Chrome, local fonts, verification.

Split out of ``a_pipeline``. Owner rulings encoded here:
  * 2026-09-08 — vendor the template's webfonts locally and embed them via
    @font-face, so the PDF carries the real font instead of an approved
    substitution;
  * 2026-09-10 — wait for pending font loads (``--virtual-time-budget``), and
    verify render stability by per-line geometry rather than page statistics.
"""

from __future__ import annotations

from tests.experiments.resume_layout.paths import ROOT
import base64
import os
import pypdfium2 as pdfium
import pdfplumber
import re
import subprocess
from app.generation.chrome_html_to_pdf import HtmlToPdfExportError, _find_chrome
from pathlib import Path
from typing import Any


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


def _render_pages(pdf_path: Path, output_dir: Path, prefix: str) -> list[Path]:
    document = pdfium.PdfDocument(pdf_path)
    paths: list[Path] = []
    for index, page in enumerate(document):
        path = output_dir / f"{prefix}_page_{index + 1}.png"
        page.render(scale=2).to_pil().convert("RGB").save(path)
        paths.append(path)
    return paths


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


def _side_by_side(target: Path, generated: Path, output: Path) -> None:
    from PIL import Image

    with Image.open(target) as left, Image.open(generated) as right:
        height = max(left.height, right.height)
        canvas = Image.new("RGB", (left.width + right.width, height), "white")
        canvas.paste(left, (0, 0))
        canvas.paste(right, (left.width, 0))
        canvas.save(output)
