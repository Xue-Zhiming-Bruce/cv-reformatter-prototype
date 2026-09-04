"""Headless-Chrome adapter for the product HTML-to-PDF export.

Chrome renders declared point sizes and CSS borders directly. Page size and
margins come from the renderer's ``@page`` CSS (the renderer is the single
source of page truth); Chrome is invoked with no header/footer or forced
margins.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class HtmlToPdfExportError(RuntimeError):
    pass


_CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
    "chrome-headless-shell",
)
_MACOS_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _find_chrome() -> str:
    override = os.getenv("CHROME_BIN") or os.getenv("CHROME_EXECUTABLE")
    if override:
        return override
    if Path(_MACOS_CHROME).exists():
        return _MACOS_CHROME
    for name in _CHROME_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    raise HtmlToPdfExportError(
        "No Chrome/Chromium binary found for HTML-to-PDF export. Install "
        "Chrome/Chromium or set CHROME_BIN."
    )


def export_html_to_pdf(html_path: str | Path, output_path: str | Path) -> Path:
    html_path, output_path = Path(html_path), Path(output_path)
    if not html_path.exists():
        raise HtmlToPdfExportError(f"HTML file does not exist: {html_path}")
    chrome = _find_chrome()
    timeout = max(float(os.getenv("CHROME_EXPORT_TIMEOUT_SECONDS", "120")), 1.0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",  # new headless (Chrome >= 112)
        "--print-to-pdf-no-header",  # legacy flag; ignored when unknown
        f"--print-to-pdf={output_path}",
        html_path.resolve().as_uri(),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise HtmlToPdfExportError(
            f"Chrome print-to-pdf timed out after {timeout} seconds."
        ) from exc
    if result.returncode != 0:
        raise HtmlToPdfExportError(
            f"Chrome print-to-pdf failed (exit {result.returncode}): "
            f"{result.stderr.strip()[-500:]}"
        )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise HtmlToPdfExportError("Chrome print-to-pdf produced no PDF.")
    return output_path
