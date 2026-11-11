from pathlib import Path

import pytest

from app.ingestion.docx_reader import read_docx


pytestmark = pytest.mark.local_dataset


DOCX_DATASET_DIR = Path("tests/local_datasets/cvparserpro_it_resumes/files")


def test_local_docx_dataset_reads_every_inventoried_document() -> None:
    paths = _local_docx_dataset_files()

    assert {path.name for path in paths} == {
        f"it_cv_{number}.docx" for number in range(1, 51)
    }
    for path in paths:
        if not read_docx(path).plain_text.strip():
            pytest.fail(f"{path} yields empty extracted text")


def _local_docx_dataset_files() -> list[Path]:
    if not DOCX_DATASET_DIR.exists():
        pytest.skip(f"Local DOCX resume dataset is not available at {DOCX_DATASET_DIR}.")
    return sorted(
        path for path in DOCX_DATASET_DIR.glob("*.docx") if not path.name.startswith("~$")
    )
