from pathlib import Path
import base64
import io

import pytest
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Inches

from app.ingestion.docx_reader import ExtractedTable, read_docx
from app.ingestion.file_validator import CorruptedFileError, UnsupportedFileTypeError, validate_docx_file


def test_read_docx_extracts_paragraphs_and_tables(tmp_path: Path) -> None:
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    document.add_paragraph("Skills: Python, SQL")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Company"
    table.rows[0].cells[1].text = "Role"
    table.rows[1].cells[0].text = "Acme"
    table.rows[1].cells[1].text = "Engineer"
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Jane Candidate", "Skills: Python, SQL"]
    assert content.tables[0].rows[1] == ["Acme", "Engineer"]
    assert "Acme | Engineer" in content.plain_text


def test_read_docx_keeps_authored_paragraph_table_paragraph_order(tmp_path: Path) -> None:
    """Issue #49: a table between two paragraphs stays between them, not last."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Before table")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Acme"
    table.rows[0].cells[1].text = "Engineer"
    table.rows[1].cells[0].text = "Led the migration"
    table.rows[1].cells[1].text = "2024"
    document.add_paragraph("After table")
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Before table",
        "Acme | Engineer",
        "Led the migration | 2024",
        "After table",
    ]
    assert [isinstance(block, ExtractedTable) for block in content.blocks] == [
        False,
        True,
        False,
    ]


def test_read_docx_attributes_header_and_footer_text_to_their_section(tmp_path: Path) -> None:
    """Header/footer text is read with provenance, ahead of and after the body."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.sections[0].header.paragraphs[0].text = "Synthetic Candidate Contact"
    document.sections[0].footer.paragraphs[0].text = "+1 555 0100"
    document.add_paragraph("Before table")
    table = document.add_table(rows=1, cols=1)
    table.rows[0].cells[0].text = "Table entry"
    document.add_paragraph("After table")
    document.save(path)

    content = read_docx(path)

    assert [
        (item.location, item.section_index, item.text) for item in content.header_footer
    ] == [
        ("header", 1, "Synthetic Candidate Contact"),
        ("footer", 1, "+1 555 0100"),
    ]
    assert content.plain_text.splitlines() == [
        "Synthetic Candidate Contact",
        "Before table",
        "Table entry",
        "After table",
        "+1 555 0100",
    ]


def test_read_docx_reads_a_linked_header_once_at_its_defining_section(
    tmp_path: Path,
) -> None:
    """A linked header repeats a definition instead of restating a candidate fact."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.sections[0].header.paragraphs[0].text = "Synthetic Candidate Contact"
    document.add_paragraph("First body paragraph")
    document.add_section()
    document.add_paragraph("Second body paragraph")
    document.save(path)

    content = read_docx(path)

    assert [item.section_index for item in content.header_footer] == [1]
    assert content.plain_text.count("Synthetic Candidate Contact") == 1


def test_read_docx_reports_a_nested_table_instead_of_dropping_its_cell(tmp_path: Path) -> None:
    """python-docx returns no text for a cell that holds a table; say so."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Before table")
    outer = document.add_table(rows=1, cols=1)
    nested = outer.rows[0].cells[0].add_table(rows=1, cols=1)
    nested.rows[0].cells[0].text = "Nested entry"
    document.add_paragraph("After table")
    document.save(path)

    content = read_docx(path)

    assert "Nested entry" not in content.plain_text
    assert [(item.kind, item.location, item.count) for item in content.limitations] == [
        ("nested_table", "body", 1)
    ]
    assert "not read" in content.limitations[0].message
    assert "Review the source document" in content.limitations[0].message


def test_read_docx_reports_text_boxes_and_tracked_changes(tmp_path: Path) -> None:
    """Visible content outside the supported shapes is attributed, never silent."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    boxed = document.add_paragraph()
    boxed._p.append(
        parse_xml(
            f'<w:r {nsdecls("w")}>'
            '<w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml">'
            "<v:textbox><w:txbxContent><w:p><w:r><w:t>Boxed contact</w:t></w:r></w:p>"
            "</w:txbxContent></v:textbox></v:shape></w:pict></w:r>"
        )
    )
    tracked = document.add_paragraph("Skills: ")
    tracked._p.append(
        parse_xml(
            f'<w:ins {nsdecls("w")} w:id="1" w:author="recruiter">'
            "<w:r><w:t>Python</w:t></w:r></w:ins>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert "Boxed contact" not in content.plain_text
    assert "Python" not in content.plain_text
    assert sorted(item.kind for item in content.limitations) == ["text_box", "tracked_change"]


def test_read_docx_with_embedded_image_preserves_text_extraction(tmp_path: Path) -> None:
    path = tmp_path / "resume-with-image.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    document.add_picture(io.BytesIO(_one_pixel_png()), width=Inches(0.25))
    document.add_paragraph("Skills: Python, SQL")
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Jane Candidate", "Skills: Python, SQL"]
    assert content.plain_text == "Jane Candidate\nSkills: Python, SQL"


def test_validate_docx_rejects_unsupported_extension(tmp_path: Path) -> None:
    path = tmp_path / "resume.txt"
    path.write_text("not a docx", encoding="utf-8")

    with pytest.raises(UnsupportedFileTypeError, match="Only .docx"):
        validate_docx_file(path)


def test_validate_docx_rejects_corrupted_file(tmp_path: Path) -> None:
    path = tmp_path / "resume.docx"
    path.write_text("not a zip", encoding="utf-8")

    with pytest.raises(CorruptedFileError, match="corrupted"):
        validate_docx_file(path)


def _one_pixel_png() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X3mNvwAAAABJRU5ErkJggg=="
    )
