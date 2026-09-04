from pathlib import Path
import pytest

from app.extraction.llm_extractor import MockLLMClient, extract_candidate_profile
from app.generation.html_renderer import render_html
from app.generation.template_mapper import build_client_render_context
from app.ingestion.docx_reader import read_docx
from app.template_analysis.schemas import built_in_layout_template_spec


pytestmark = pytest.mark.local_dataset


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
            output_path = render_html(context, built_in_layout_template_spec(), tmp_path / f"{path.stem}.html")
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise AssertionError("generated HTML missing or empty")
        except Exception as exc:
            failures.append(
                {
                    "file": path.name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    assert failures == []


def _local_docx_dataset_files() -> list[Path]:
    for dataset_dir in [DOCX_DATASET_DIR, LEGACY_DOCX_DATASET_DIR]:
        if dataset_dir.exists():
            paths = sorted(path for path in dataset_dir.glob("*.docx") if not path.name.startswith("~$"))
            if paths:
                return paths
    pytest.skip("Local DOCX resume dataset is not available.")
