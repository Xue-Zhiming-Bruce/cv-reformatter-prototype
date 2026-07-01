from pathlib import Path
import subprocess

import pytest

from app.generation import pdf_exporter
from app.generation.pdf_exporter import LibreOfficeNotFoundError, PdfExportFailedError, export_docx_to_pdf


def test_export_docx_to_pdf_runs_soffice_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    docx_path = tmp_path / "candidate_profile.docx"
    output_dir = tmp_path / "out"
    docx_path.write_bytes(b"placeholder docx")
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        (output_dir / "candidate_profile.pdf").write_bytes(b"%PDF-1.4")
        return subprocess.CompletedProcess(command, 0, stdout="converted", stderr="")

    monkeypatch.setattr(pdf_exporter.shutil, "which", lambda name: "/usr/local/bin/soffice" if name == "soffice" else None)
    monkeypatch.setattr(pdf_exporter.subprocess, "run", fake_run)

    profile_dir = tmp_path / "lo-profile"
    pdf_path = export_docx_to_pdf(
        docx_path,
        output_dir=output_dir,
        timeout_seconds=12,
        user_installation_dir=profile_dir,
    )

    assert pdf_path == output_dir / "candidate_profile.pdf"
    assert captured["command"] == [
        "/usr/local/bin/soffice",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(docx_path),
    ]
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 12


def test_export_docx_to_pdf_reports_missing_libreoffice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    docx_path = tmp_path / "candidate_profile.docx"
    docx_path.write_bytes(b"placeholder docx")
    monkeypatch.setattr(pdf_exporter.shutil, "which", lambda _name: None)

    with pytest.raises(LibreOfficeNotFoundError, match="LibreOffice"):
        export_docx_to_pdf(docx_path)


def test_export_docx_to_pdf_reports_failed_conversion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    docx_path = tmp_path / "candidate_profile.docx"
    docx_path.write_bytes(b"placeholder docx")

    def fake_run(
        command: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="conversion failed")

    monkeypatch.setattr(pdf_exporter.shutil, "which", lambda _name: "/usr/local/bin/soffice")
    monkeypatch.setattr(pdf_exporter.subprocess, "run", fake_run)

    with pytest.raises(PdfExportFailedError, match="conversion failed"):
        export_docx_to_pdf(docx_path)
