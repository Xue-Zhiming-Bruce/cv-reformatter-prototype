"""Targeted unit tests for the shared headless-Chrome HTML-to-PDF contract.

Stubs Chrome discovery and subprocess execution so pytest never launches a
browser here; asserts the one export contract (page truth via @page CSS, no
header/footer, pinned locale/timezone/font readiness, staged output), the
recorded browser/font environment, and failure/empty/stale-output handling.
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
        self.environments: list[dict[str, str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        self.environments.append(kwargs.get("env") or {})
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
    monkeypatch.setattr(mod, "_find_chrome", lambda: mod.ChromeBinary("/fake/chrome", "override"))
    monkeypatch.setattr(mod, "_binary_version", lambda _path: "FakeChrome 1.0")
    monkeypatch.setattr(mod.subprocess, "run", fake)
    return fake


def _write_html(tmp_path: Path) -> Path:
    html = tmp_path / "in.html"
    html.write_text("<html><body>x</body></html>", encoding="utf-8")
    return html


def _leftover_staging_files(tmp_path: Path) -> list[Path]:
    return list(tmp_path.glob(".*.tmp.pdf"))


def test_shared_export_contract_and_success(tmp_path: Path, fake_chrome: _FakeRun) -> None:
    html = _write_html(tmp_path)
    out = tmp_path / "out.pdf"
    result = mod.export_html_to_pdf(html, out)
    assert result == out
    assert out.read_bytes().startswith(b"%PDF-1.4")
    command = fake_chrome.commands[-1]
    assert command[0] == "/fake/chrome"
    for flag in (
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        "--window-size=1280,1600",
        "--force-device-scale-factor=1",
        "--lang=en-US",
        f"--virtual-time-budget={mod.PROFILE.virtual_time_budget_ms}",
    ):
        assert flag in command
    # Page size/margins stay with @page: no paper, margin or scale override.
    assert not any(arg.startswith(("--print-to-pdf-page-size", "--print-to-pdf-margin")) for arg in command)
    assert command[-1] == html.resolve().as_uri()
    # Chrome writes a staging file; only a complete PDF becomes the output.
    printed = next(arg for arg in command if arg.startswith("--print-to-pdf=")).split("=", 1)[1]
    assert printed != str(out) and printed.endswith(".tmp.pdf")
    process_environment = fake_chrome.environments[-1]
    assert process_environment["LANG"] == "en_US.UTF-8"
    assert process_environment["TZ"] == mod.PROFILE.timezone
    assert _leftover_staging_files(tmp_path) == []


def test_environment_records_browser_resolution_and_font_assets(
    fake_chrome: _FakeRun,
) -> None:
    environment = mod.chrome_environment()
    assert environment["contract"] == mod.CONTRACT_VERSION
    assert environment["profile"] == mod.PROFILE.name
    assert environment["resolution"] == "override"
    assert environment["executable"] == "/fake/chrome"
    assert environment["version"] == "FakeChrome 1.0"
    assert environment["locale"] == "en-US" and environment["timezone"] == "UTC"
    assert environment["font_readiness"] == mod.PROFILE.font_readiness
    fonts = environment["fonts"]
    assert "lato-regular.woff2" in fonts["files"] and "lato.css" in fonts["files"]
    assert len(fonts["sha256"]) == 64
    assert mod.chrome_environment()["digest"] == environment["digest"]


def test_changed_browser_version_changes_the_validity_digest(
    fake_chrome: _FakeRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = mod.chrome_environment()["digest"]
    monkeypatch.setattr(mod, "_binary_version", lambda _path: "FakeChrome 2.0")
    after = mod.chrome_environment()
    assert after["version"] == "FakeChrome 2.0"
    assert after["digest"] != before


def test_missing_chrome_records_unresolved_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def missing() -> mod.ChromeBinary:
        raise mod.HtmlToPdfExportError("No Chrome/Chromium binary found")

    monkeypatch.setattr(mod, "_find_chrome", missing)
    environment = mod.chrome_environment()
    assert environment["resolution"] == "unresolved"
    assert environment["version"] is None
    with pytest.raises(mod.HtmlToPdfExportError, match="No Chrome"):
        mod.export_html_to_pdf(_write_html(tmp_path), tmp_path / "out.pdf")


def test_fake_output_is_text_bearing(tmp_path: Path, fake_chrome: _FakeRun) -> None:
    """The stubbed Chrome output is a real text-bearing PDF with the marker text."""
    out = tmp_path / "out.pdf"
    mod.export_html_to_pdf(_write_html(tmp_path), out)
    with pdfplumber.open(out) as pdf:
        extracted = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert FAKE_PDF_TEXT in extracted


def test_missing_html_raises(tmp_path: Path) -> None:
    with pytest.raises(mod.HtmlToPdfExportError, match="does not exist"):
        mod.export_html_to_pdf(tmp_path / "missing.html", tmp_path / "out.pdf")


def test_nonzero_exit_raises_and_preserves_prior_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = tmp_path / "out.pdf"
    previous.write_bytes(b"PRIOR EXPORT")
    monkeypatch.setattr(mod, "_find_chrome", lambda: mod.ChromeBinary("/fake/chrome", "override"))
    monkeypatch.setattr(mod, "_binary_version", lambda _path: "FakeChrome 1.0")
    monkeypatch.setattr(mod.subprocess, "run", _FakeRun(returncode=1, stderr="boom"))
    with pytest.raises(mod.HtmlToPdfExportError, match="exit 1"):
        mod.export_html_to_pdf(_write_html(tmp_path), previous)
    assert previous.read_bytes() == b"PRIOR EXPORT"
    assert _leftover_staging_files(tmp_path) == []


def test_timeout_raises_and_preserves_prior_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = tmp_path / "out.pdf"
    previous.write_bytes(b"PRIOR EXPORT")
    monkeypatch.setattr(mod, "_find_chrome", lambda: mod.ChromeBinary("/fake/chrome", "override"))
    monkeypatch.setattr(mod, "_binary_version", lambda _path: "FakeChrome 1.0")

    def boom(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 120)

    monkeypatch.setattr(mod.subprocess, "run", boom)
    with pytest.raises(mod.HtmlToPdfExportError, match="timed out"):
        mod.export_html_to_pdf(_write_html(tmp_path), previous)
    assert previous.read_bytes() == b"PRIOR EXPORT"
    assert _leftover_staging_files(tmp_path) == []


def test_stale_pre_existing_file_is_not_a_successful_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = tmp_path / "out.pdf"
    stale.write_bytes(b"%PDF-1.4 STALE EXPORT")
    monkeypatch.setattr(mod, "_find_chrome", lambda: mod.ChromeBinary("/fake/chrome", "override"))
    monkeypatch.setattr(mod, "_binary_version", lambda _path: "FakeChrome 1.0")
    monkeypatch.setattr(mod.subprocess, "run", _FakeRun(returncode=0, write_pdf=False))
    with pytest.raises(mod.HtmlToPdfExportError, match="produced no PDF"):
        mod.export_html_to_pdf(_write_html(tmp_path), stale)
    assert stale.read_bytes() == b"%PDF-1.4 STALE EXPORT"


def test_chrome_bin_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHROME_BIN", "/opt/custom/chrome")
    assert mod._find_chrome() == mod.ChromeBinary("/opt/custom/chrome", "override")


def test_no_chrome_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHROME_BIN", raising=False)
    monkeypatch.delenv("CHROME_EXECUTABLE", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(mod.Path, "exists", lambda self: False)
    with pytest.raises(mod.HtmlToPdfExportError, match="No Chrome"):
        mod._find_chrome()
