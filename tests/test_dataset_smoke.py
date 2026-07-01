from pathlib import Path
import shutil

import pytest

from app.extraction.llm_extractor import MockLLMClient, extract_candidate_profile
from app.generation.docx_renderer import render_docx
from app.generation.pdf_exporter import export_docx_to_pdf
from app.generation.template_mapper import build_client_render_context
from app.ingestion.docx_reader import read_docx


DOCX_DATASET_DIR = Path("tests/local_datasets/cvparserpro_it_resumes/files")
LEGACY_DOCX_DATASET_DIR = Path("tests/IT_Resume_Dataset_CVParserPro")


def test_local_docx_dataset_runs_through_backend_render_chain(tmp_path: Path) -> None:
    paths = _local_docx_dataset_files()

    client = MockLLMClient()
    failures: list[dict[str, str]] = []

    for path in paths:
        try:
            extracted_text = read_docx(path).plain_text
            if not extracted_text.strip():
                raise AssertionError("empty extracted text")
            profile = extract_candidate_profile(extracted_text, client)
            context = build_client_render_context(profile, blind_profile=True)
            output_path = render_docx(context, tmp_path / f"{path.stem}.docx")
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise AssertionError("generated DOCX missing or empty")
        except Exception as exc:
            failures.append(
                {
                    "file": path.name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    assert failures == []


def test_local_docx_dataset_pdf_export_smoke(tmp_path: Path) -> None:
    if not (shutil.which("soffice") or shutil.which("libreoffice")):
        pytest.skip("LibreOffice/soffice is not available for local PDF export.")

    paths = _local_docx_dataset_files()

    extracted_text = read_docx(paths[0]).plain_text
    profile = extract_candidate_profile(extracted_text, MockLLMClient())
    context = build_client_render_context(profile, blind_profile=True)
    docx_path = render_docx(context, tmp_path / "candidate_profile.docx")

    pdf_path = export_docx_to_pdf(docx_path, output_dir=tmp_path)

    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 0


def _local_docx_dataset_files() -> list[Path]:
    for dataset_dir in [DOCX_DATASET_DIR, LEGACY_DOCX_DATASET_DIR]:
        if dataset_dir.exists():
            paths = sorted(path for path in dataset_dir.glob("*.docx") if not path.name.startswith("~$"))
            if paths:
                return paths
    pytest.skip("Local DOCX resume dataset is not available.")
