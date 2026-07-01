from contextlib import nullcontext
from pathlib import Path
import shutil
import subprocess
import tempfile


class PdfExportError(RuntimeError):
    """Raised when DOCX to PDF export fails."""


class LibreOfficeNotFoundError(PdfExportError):
    """Raised when LibreOffice or soffice is not installed or not on PATH."""


class PdfExportFailedError(PdfExportError):
    """Raised when LibreOffice runs but does not produce the expected PDF."""


def export_docx_to_pdf(
    docx_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    soffice_path: str | None = None,
    timeout_seconds: int = 60,
    user_installation_dir: str | Path | None = None,
) -> Path:
    source_path = Path(docx_path)
    _validate_docx_source(source_path)

    resolved_output_dir = Path(output_dir) if output_dir is not None else source_path.parent
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    executable = soffice_path or _find_soffice()
    profile_context = (
        tempfile.TemporaryDirectory(prefix="cv-reformatter-libreoffice-")
        if user_installation_dir is None
        else nullcontext(str(user_installation_dir))
    )

    with profile_context as profile_dir:
        user_installation_uri = Path(profile_dir).resolve().as_uri()
        command = [
            executable,
            f"-env:UserInstallation={user_installation_uri}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(resolved_output_dir),
            str(source_path),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise PdfExportFailedError(
                f"LibreOffice PDF export timed out after {timeout_seconds} seconds."
            ) from exc
        except OSError as exc:
            raise PdfExportFailedError(f"LibreOffice PDF export could not start: {exc}") from exc

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "No details returned.").strip()
        raise PdfExportFailedError(f"LibreOffice PDF export failed: {details}")

    pdf_path = resolved_output_dir / f"{source_path.stem}.pdf"
    if not pdf_path.exists():
        details = (result.stderr or result.stdout or "No details returned.").strip()
        raise PdfExportFailedError(
            f"LibreOffice completed but did not create expected PDF: {pdf_path}. {details}"
        )
    return pdf_path


def _validate_docx_source(source_path: Path) -> None:
    if not source_path.exists():
        raise FileNotFoundError(f"DOCX file not found: {source_path}")
    if not source_path.is_file():
        raise PdfExportError(f"Expected a DOCX file, got: {source_path}")
    if source_path.suffix.lower() != ".docx":
        raise PdfExportError("PDF export requires a .docx source file.")


def _find_soffice() -> str:
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if not executable:
        raise LibreOfficeNotFoundError(
            "LibreOffice/soffice is required for DOCX to PDF export. "
            "Install LibreOffice and ensure 'soffice' is available on PATH."
        )
    return executable
