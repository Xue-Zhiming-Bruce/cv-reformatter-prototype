from pathlib import Path
from zipfile import ZipFile

import pytest
from pypdf import PdfWriter

from app.ingestion.pdf_reader import (
    CorruptedPdfError,
    EmptyPdfTextError,
    UnsupportedPdfTypeError,
    read_pdf_text,
)


TESTS_DIR = Path(__file__).resolve().parents[1]
PDF_RESUME_DATASETS = [
    TESTS_DIR / "local_datasets" / "resume_archive" / "source.zip",
    TESTS_DIR / "archive.zip",
]


@pytest.mark.parametrize("font_size", [8, 12, 24])
def test_read_pdf_text_preserves_positioned_word_gaps(tmp_path: Path, font_size: int) -> None:
    from tests.helpers.synthetic_pdf import _write_single_page_pdf

    pdf = _write_single_page_pdf(tmp_path / "positioned.pdf", [
        f"BT /F1 {font_size} Tf 72 720 Td [(Software) -230 (Engineer) -230 (II)] TJ ET",
        f"BT /F1 {font_size} Tf {font_size * 0.08} Tc 72 680 Td (quality-of-service) Tj ET",
    ])

    assert read_pdf_text(pdf).splitlines() == ["Software Engineer II", "quality-of-service"]


def test_read_pdf_text_extracts_text_from_dataset_sample(tmp_path: Path) -> None:
    dataset_path = _require_pdf_resume_dataset()
    pdf_path = _copy_zip_member(
        dataset_path,
        "data/data/ACCOUNTANT/10554236.pdf",
        tmp_path,
    )

    text = read_pdf_text(pdf_path)

    assert "ACCOUNTANT" in text
    assert "Summary" in text
    assert len(text) > 1_000


def test_pdf_resume_dataset_samples_are_readable(tmp_path: Path) -> None:
    dataset_path = _require_pdf_resume_dataset()
    members = _dataset_pdf_members(dataset_path)

    for member in members[:5]:
        pdf_path = _copy_zip_member(dataset_path, member, tmp_path)

        text = read_pdf_text(pdf_path)

        assert len(text) > 500


def test_read_pdf_text_rejects_unsupported_extension(tmp_path: Path) -> None:
    path = tmp_path / "resume.docx"
    path.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(UnsupportedPdfTypeError, match="Only .pdf"):
        read_pdf_text(path)


def test_read_pdf_text_rejects_corrupted_pdf(tmp_path: Path) -> None:
    path = tmp_path / "resume.pdf"
    path.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(CorruptedPdfError, match="corrupted"):
        read_pdf_text(path)


def test_read_pdf_text_rejects_pdf_without_extractable_text(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as output:
        writer.write(output)

    with pytest.raises(EmptyPdfTextError, match="No extractable text"):
        read_pdf_text(path)


def _require_pdf_resume_dataset() -> Path:
    for dataset_path in PDF_RESUME_DATASETS:
        if dataset_path.exists():
            return dataset_path
    pytest.skip(
        "Dataset fixture is not available in any expected location: "
        + ", ".join(str(path) for path in PDF_RESUME_DATASETS)
    )


def _dataset_pdf_members(dataset_path: Path) -> list[str]:
    with ZipFile(dataset_path) as archive:
        return [
            member.filename
            for member in archive.infolist()
            if not member.is_dir() and member.filename.lower().endswith(".pdf")
        ]


def _copy_zip_member(dataset_path: Path, member: str, target_dir: Path) -> Path:
    target = target_dir / Path(member).name
    with ZipFile(dataset_path) as archive:
        target.write_bytes(archive.read(member))
    return target
