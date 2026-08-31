"""Targeted unit tests for the headless-Chrome HTML-to-PDF adapter.

Stubs Chrome discovery and subprocess execution so pytest never launches a
browser; asserts the exact print-to-pdf command, the page/margin contract
(via @page CSS, honored by --no-pdf-header-footer / no forced margins), and
failure/empty-output handling.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pdfplumber
import pytest

from app.generation import chrome_html_to_pdf as mod
from tests.helpers.synthetic_pdf import build_text_pdf_pages


FAKE_PDF_TEXT = "CHROME FAKE PDF TEXT"


class _FakeRun:
    """Records commands; writes a text-bearing fake PDF on success."""

    def __init__(self, returncode: int = 0, stderr: str = "", write_pdf: bool = True):
        self.returncode = returncode
        self.stderr = stderr
        self.write_pdf = write_pdf
        self.commands: list[list[str]] = []

    def __call__(self, command, **_kwargs):
        self.commands.append(command)
        if self.returncode == 0 and self.write_pdf:
            for arg in command:
                if arg.startswith("--print-to-pdf="):
                    build_text_pdf_pages(
                        Path(arg.split("=", 1)[1]),
                        [FAKE_PDF_TEXT],
                        width_pt=612.0,
                        height_pt=792.0,
                        margin_top_pt=36.0,
                        margin_left_pt=36.0,
                        margin_bottom_pt=36.0,
                        margin_right_pt=36.0,
                    )
        return subprocess.CompletedProcess(
            command, self.returncode, stdout="", stderr=self.stderr
        )


@pytest.fixture()
def fake_chrome(monkeypatch: pytest.MonkeyPatch) -> _FakeRun:
    fake = _FakeRun()
    monkeypatch.setattr(mod, "_find_chrome", lambda: "/fake/chrome")
    monkeypatch.setattr(mod.subprocess, "run", fake)
    return fake


def _write_html(tmp_path: Path) -> Path:
    html = tmp_path / "in.html"
    html.write_text("<html><body>x</body></html>", encoding="utf-8")
    return html


def test_command_flags_and_success(tmp_path: Path, fake_chrome: _FakeRun) -> None:
    html = _write_html(tmp_path)
    out = tmp_path / "out.pdf"
    result = mod.export_html_to_pdf(html, out)
    assert result == out
    assert out.read_bytes().startswith(b"%PDF-1.4")
    cmd = fake_chrome.commands[0]
    assert cmd[0] == "/fake/chrome"
    assert "--headless=new" in cmd
    assert "--disable-gpu" in cmd
    assert "--no-pdf-header-footer" in cmd  # margin/page truth comes from @page CSS
    assert "--print-to-pdf-no-header" in cmd
    assert f"--print-to-pdf={out}" in cmd
    assert cmd[-1] == html.resolve().as_uri()  # file:// URL input


def test_fake_output_is_text_bearing(tmp_path: Path, fake_chrome: _FakeRun) -> None:
    """The stubbed Chrome output is a real text-bearing PDF with the marker text."""
    html = _write_html(tmp_path)
    out = tmp_path / "out.pdf"
    mod.export_html_to_pdf(html, out)
    with pdfplumber.open(out) as pdf:
        extracted = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert FAKE_PDF_TEXT in extracted


def test_missing_html_raises(tmp_path: Path) -> None:
    with pytest.raises(mod.HtmlToPdfExportError, match="does not exist"):
        mod.export_html_to_pdf(tmp_path / "missing.html", tmp_path / "out.pdf")


def test_nonzero_exit_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_find_chrome", lambda: "/fake/chrome")
    monkeypatch.setattr(mod.subprocess, "run", _FakeRun(returncode=1, stderr="boom"))
    with pytest.raises(mod.HtmlToPdfExportError, match="exit 1"):
        mod.export_html_to_pdf(_write_html(tmp_path), tmp_path / "out.pdf")


def test_timeout_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_find_chrome", lambda: "/fake/chrome")

    def boom(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 120)

    monkeypatch.setattr(mod.subprocess, "run", boom)
    with pytest.raises(mod.HtmlToPdfExportError, match="timed out"):
        mod.export_html_to_pdf(_write_html(tmp_path), tmp_path / "out.pdf")


def test_empty_output_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_find_chrome", lambda: "/fake/chrome")
    monkeypatch.setattr(
        mod.subprocess, "run", _FakeRun(returncode=0, write_pdf=False)
    )
    with pytest.raises(mod.HtmlToPdfExportError, match="produced no PDF"):
        mod.export_html_to_pdf(_write_html(tmp_path), tmp_path / "out.pdf")


def test_chrome_bin_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHROME_BIN", "/opt/custom/chrome")
    assert mod._find_chrome() == "/opt/custom/chrome"


def test_no_chrome_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHROME_BIN", raising=False)
    monkeypatch.delenv("CHROME_EXECUTABLE", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(mod.Path, "exists", lambda self: False)
    with pytest.raises(mod.HtmlToPdfExportError, match="No Chrome"):
        mod._find_chrome()
