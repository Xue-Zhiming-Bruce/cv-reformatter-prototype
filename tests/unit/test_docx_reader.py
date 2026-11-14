from pathlib import Path
import base64
import io

import pytest
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches

from app.extraction.coverage_audit import audit_segmentation_lines
from app.ingestion.docx_reader import (
    ExtractedTable,
    read_docx,
    unread_content_messages,
)
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
        (item.location, item.variant, item.section_index, item.text)
        for item in content.header_footer
    ] == [
        ("header", "default", 1, "Synthetic Candidate Contact"),
        ("footer", "default", 1, "+1 555 0100"),
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


def test_read_docx_reads_a_block_content_control_in_place(tmp_path: Path) -> None:
    """A body-level control keeps its paragraphs and tables where the author put it."""
    path = tmp_path / "resume.docx"
    document = Document()
    before = document.add_paragraph("Before control")
    before._p.addnext(
        _content_control(
            '<w:p><w:r><w:t>Inside control</w:t></w:r></w:p>'
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell entry</w:t></w:r></w:p>"
            "</w:tc></w:tr></w:tbl>"
        )
    )
    document.add_paragraph("After control")
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Before control",
        "Inside control",
        "Cell entry",
        "After control",
    ]
    assert content.limitations == []


def test_read_docx_reports_inline_content_controls(tmp_path: Path) -> None:
    """A control wrapping runs, or sitting in a cell, keeps text the reader never sees."""
    path = tmp_path / "resume.docx"
    document = Document()
    phone = document.add_paragraph("Phone: ")
    phone._p.append(_content_control("<w:r><w:t>555 0100</w:t></w:r>"))
    table = document.add_table(rows=1, cols=1)
    table.rows[0].cells[0].paragraphs[0]._p.append(
        _content_control("<w:r><w:t>In-cell value</w:t></w:r>")
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Phone:"]
    assert [(item.kind, item.location, item.count) for item in content.limitations] == [
        ("content_control", "body", 2)
    ]
    assert (
        "content inside a content control this reader cannot read in place"
        in content.limitations[0].message
    )
    assert "Review the source document" in content.limitations[0].message
    # The coverage audit measures only the text the reader supplied, so it reports
    # `incomplete` here instead of passing over the missing control text.
    audit = audit_segmentation_lines(
        content.plain_text,
        content.plain_text.splitlines(),
        *unread_content_messages(content.limitations),
    )
    assert audit.status == "incomplete"


def test_read_docx_reports_a_field_result(tmp_path: Path) -> None:
    """A simple field's cached result is visible text outside the read set."""
    path = tmp_path / "resume.docx"
    document = Document()
    reference = document.add_paragraph("Ref: ")
    reference._p.append(
        parse_xml(
            f'<w:fldSimple {nsdecls("w")} w:instr=" REF candidate_name ">'
            "<w:r><w:t>Field result</w:t></w:r></w:fldSimple>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Ref:"]
    assert [(item.kind, item.location, item.count) for item in content.limitations] == [
        ("field", "body", 1)
    ]
    assert "cached visible result is not read" in content.limitations[0].message
    audit = audit_segmentation_lines(
        content.plain_text,
        content.plain_text.splitlines(),
        *unread_content_messages(content.limitations),
    )
    assert audit.status == "incomplete"


def test_read_docx_reads_only_enabled_header_variants(tmp_path: Path) -> None:
    """Word hides a disabled variant, so it supplies no facts and no limitation."""
    hidden = tmp_path / "hidden.docx"
    document = Document()
    document.sections[0].first_page_header.paragraphs[0].text = "Hidden first page"
    document.add_paragraph("Body line")
    document.save(hidden)

    hidden_content = read_docx(hidden)

    assert hidden_content.plain_text.splitlines() == ["Body line"]
    assert hidden_content.header_footer == []
    assert hidden_content.limitations == []

    shown = tmp_path / "shown.docx"
    document = Document()
    section = document.sections[0]
    section.first_page_header.paragraphs[0].text = "First page contact"
    section.first_page_footer.paragraphs[0].text = "First page footer"
    section.different_first_page_header_footer = True
    document.settings.odd_and_even_pages_header_footer = True
    section.even_page_header.paragraphs[0].text = "Even page contact"
    document.add_paragraph("Body line")
    document.save(shown)

    shown_content = read_docx(shown)

    assert [
        (item.location, item.variant, item.text) for item in shown_content.header_footer
    ] == [
        ("header", "first_page", "First page contact"),
        ("header", "even_page", "Even page contact"),
        ("footer", "first_page", "First page footer"),
    ]
    assert shown_content.plain_text.splitlines() == [
        "First page contact",
        "Even page contact",
        "Body line",
        "First page footer",
    ]


def test_read_docx_keeps_header_paragraph_table_paragraph_order(tmp_path: Path) -> None:
    """A header table keeps its place and its attribution, not paragraph-first order."""
    path = tmp_path / "resume.docx"
    document = Document()
    header = document.sections[0].header
    header.paragraphs[0].text = "Header first"
    table = header.add_table(rows=1, cols=2, width=Inches(6))
    table.rows[0].cells[0].text = "Contact"
    table.rows[0].cells[1].text = "Phone"
    header.add_paragraph("Header last")
    document.add_paragraph("Body line")
    document.save(path)

    content = read_docx(path)

    assert content.header_footer[0].text == "Header first\nContact | Phone\nHeader last"
    assert content.plain_text.splitlines() == [
        "Header first",
        "Contact | Phone",
        "Header last",
        "Body line",
    ]


def test_read_docx_reads_a_wrapper_of_paragraphs_and_tables(tmp_path: Path) -> None:
    """A block wrapper around only block shapes is read where it sits."""
    path = tmp_path / "resume.docx"
    document = Document()
    before = document.add_paragraph("Before wrapper")
    before._p.addnext(
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="resume">'
            "<w:p><w:r><w:t>Inside wrapper</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell entry</w:t></w:r></w:p>"
            "</w:tc></w:tr></w:tbl>"
            "</w:customXml>"
        )
    )
    document.add_paragraph("After wrapper")
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Before wrapper",
        "Inside wrapper",
        "Cell entry",
        "After wrapper",
    ]
    assert content.limitations == []


def test_read_docx_reads_a_wrapped_paragraph_inside_a_table_cell(tmp_path: Path) -> None:
    """A cell's block content is read through the body walk, not its direct paragraphs."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0]._tc.append(
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="field">'
            "<w:p><w:r><w:t>CELL_WRAPPED</w:t></w:r></w:p></w:customXml>"
        )
    )
    table.rows[0].cells[1]._tc.append(
        _content_control("<w:p><w:r><w:t>CELL_CONTROL</w:t></w:r></w:p>")
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Jane Candidate",
        "CELL_WRAPPED | CELL_CONTROL",
    ]
    assert content.limitations == []


def test_read_docx_reads_rows_and_cells_a_content_control_wraps(tmp_path: Path) -> None:
    """A control around a whole row or cell holds table text python-docx skips."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Acme"
    table.rows[0].cells[1].text = "Engineer"
    table.rows[1].cells[0].text = "Led the migration"
    table.rows[1].cells[1].text = "2024"
    # ``Table.rows`` and ``Row.cells`` return only direct members, so a control
    # around one of them took its text out of the reader's reach.
    first_row, second_row = table._tbl.findall(qn("w:tr"))
    first_row.replace(
        first_row.findall(qn("w:tc"))[0],
        _content_control(
            "<w:tc><w:p><w:r><w:t>Cell wrapped</w:t></w:r></w:p></w:tc>"
        ),
    )
    table._tbl.replace(
        second_row,
        _content_control(
            "<w:tr><w:tc><w:p><w:r><w:t>Row wrapped</w:t></w:r></w:p></w:tc></w:tr>"
        ),
    )
    document.save(path)

    content = read_docx(path)

    assert content.tables[0].rows == [
        ["Cell wrapped", "Engineer"],
        ["Row wrapped"],
    ]
    assert content.limitations == []


def test_read_docx_reads_a_header_table_row_a_content_control_wraps(
    tmp_path: Path,
) -> None:
    """The header walk reaches a wrapped table row the same way the body walk does."""
    path = tmp_path / "resume.docx"
    document = Document()
    header = document.sections[0].header
    header.paragraphs[0].text = "Jane Candidate"
    table = header.add_table(rows=1, cols=1, width=Inches(6))
    table.rows[0].cells[0].text = "Contact row"
    table._tbl.replace(
        table._tbl.findall(qn("w:tr"))[0],
        _content_control(
            "<w:tr><w:tc><w:p><w:r><w:t>Header wrapped</w:t></w:r></w:p></w:tc></w:tr>"
        ),
    )
    document.save(path)

    content = read_docx(path)

    assert "Header wrapped" in content.plain_text
    assert content.limitations == []


def test_read_docx_reads_a_content_control_around_a_whole_table(tmp_path: Path) -> None:
    """A control holding a table is a block control, read in the position it occupies."""
    path = tmp_path / "resume.docx"
    document = Document()
    before = document.add_paragraph("Before table")
    before._p.addnext(
        _content_control(
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Controlled entry</w:t></w:r></w:p>"
            "</w:tc></w:tr></w:tbl>"
        )
    )
    document.add_paragraph("After table")
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Before table",
        "Controlled entry",
        "After table",
    ]
    assert content.limitations == []


def test_read_docx_names_a_table_wrapper_it_cannot_descend(tmp_path: Path) -> None:
    """A table child holding text that is not a row or known wrapper is named."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    table = document.add_table(rows=1, cols=1)
    table.rows[0].cells[0].text = "Readable cell"
    table._tbl.append(
        parse_xml(
            f'<w:unknownWrapper {nsdecls("w")}>'
            "<w:tr><w:tc><w:p><w:r><w:t>Unreachable row</w:t></w:r></w:p></w:tc>"
            "</w:tr></w:unknownWrapper>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Jane Candidate", "Readable cell"]
    (limitation,) = content.limitations
    assert (limitation.kind, limitation.element) == ("unknown_block", "unknownWrapper")
    assert limitation.location.startswith("body table child ")
    assert limitation.blocks_source_review is True


def test_read_docx_reads_one_alternate_content_branch(tmp_path: Path) -> None:
    """An mc:AlternateContent holds one content twice; only one branch is read."""
    path = tmp_path / "resume.docx"
    document = Document()
    # ``wps`` is declared on the document root python-docx writes; ``wpx`` is not.
    for requires in ("wps", "wpx"):
        paragraph = document.add_paragraph("Alternative: ")
        paragraph._p.append(
            parse_xml(
                "<mc:AlternateContent "
                'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
                f'{nsdecls("w")}>'
                f'<mc:Choice Requires="{requires}">'
                "<w:r><w:t>CHOICE</w:t></w:r></mc:Choice>"
                "<mc:Fallback><w:r><w:t>FALLBACK</w:t></w:r></mc:Fallback>"
                "</mc:AlternateContent>"
            )
        )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Alternative: CHOICE",
        "Alternative: FALLBACK",
    ]
    assert content.limitations == []


def test_read_docx_reports_an_unrecognized_inline_wrapper(tmp_path: Path) -> None:
    """An inline wrapper outside the pass-through set is named, never unwrapped."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Jane Candidate")
    paragraph._p.append(
        parse_xml(f'<w:foo {nsdecls("w")}><w:r><w:t>Unknown run</w:t></w:r></w:foo>')
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Jane Candidate"]
    assert [(item.kind, item.location, item.count) for item in content.limitations] == [
        ("unknown_inline", "body child 1", 1)
    ]
    limitation = content.limitations[0]
    assert limitation.element == "foo"
    assert "an inline element this reader does not recognize is not read" in (
        limitation.message
    )


def test_read_docx_names_a_wrapper_inside_a_pass_through_wrapper(tmp_path: Path) -> None:
    """Read and report are one walk: a wrapper inside a wrapper is named, not lost."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Jane Candidate")
    paragraph._p.append(
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="odd">'
            "<w:foo><w:r><w:t>Nested visible</w:t></w:r></w:foo>"
            "</w:customXml>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Jane Candidate"]
    assert [(item.kind, item.location, item.element) for item in content.limitations] == [
        ("unknown_inline", "body child 1", "foo")
    ]
    assert content.limitations[0].blocks_source_review is True


def test_read_docx_names_a_wrapper_inside_the_branch_it_reads(tmp_path: Path) -> None:
    """The chosen alternative branch is inspected, so its own wrapper is named."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Alternative: ")
    paragraph._p.append(
        parse_xml(
            '<mc:AlternateContent '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            f'{nsdecls("w")}>'
            '<mc:Choice Requires="wps">'
            "<w:foo><w:r><w:t>CHOICE WRAPPED</w:t></w:r></w:foo>"
            "</mc:Choice>"
            "<mc:Fallback><w:r><w:t>FALLBACK</w:t></w:r></mc:Fallback>"
            "</mc:AlternateContent>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Alternative:"]
    assert [(item.kind, item.location, item.element) for item in content.limitations] == [
        ("unknown_inline", "body child 1", "foo")
    ]


def test_read_docx_skips_the_alternative_branch_it_does_not_read(tmp_path: Path) -> None:
    """The unread branch renders what was read, so its shapes are not reported."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Alternative: ")
    paragraph._p.append(
        parse_xml(
            '<mc:AlternateContent '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            f'{nsdecls("w")}>'
            '<mc:Choice Requires="wps"><w:r><w:t>CHOICE</w:t></w:r></mc:Choice>'
            "<mc:Fallback><w:sdt><w:sdtContent>"
            "<w:r><w:t>FALLBACK</w:t></w:r>"
            "<w:fldSimple w:instr='PAGE'><w:r><w:t>1</w:t></w:r></w:fldSimple>"
            "</w:sdtContent></w:sdt></mc:Fallback>"
            "</mc:AlternateContent>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Alternative: CHOICE"]
    assert content.limitations == []


def test_read_docx_reports_ruby_text_instead_of_dropping_it(tmp_path: Path) -> None:
    """Ruby base and annotation sit in nested runs, so a run's text misses them."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Name: ")
    paragraph._p.append(
        parse_xml(
            f'<w:r {nsdecls("w")}><w:ruby><w:rubyPr>'
            "<w:lid w:val=\"ja-JP\"/><w:dirty w:val=\"0\"/>"
            "</w:rubyPr>"
            "<w:rt><w:r><w:t>タナカ</w:t></w:r></w:rt>"
            "<w:rubyBase><w:r><w:t>田中</w:t></w:r></w:rubyBase>"
            "</w:ruby></w:r>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Name:"]
    assert [(item.kind, item.location, item.count) for item in content.limitations] == [
        ("ruby", "body child 1", 1)
    ]
    assert "base and annotation text is not read" in content.limitations[0].message
    assert content.limitations[0].blocks_source_review is True


def test_read_docx_reads_runs_hyperlinks_and_revisions_without_inline_noise(
    tmp_path: Path,
) -> None:
    """Run text, tabs, hyperlinks and revisions stay on their existing paths."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Jane ")
    run = paragraph.add_run("Candidate")
    run.add_tab()
    run.add_text("Berlin")
    paragraph._p.append(
        parse_xml(
            f'<w:hyperlink {nsdecls("w")}>'
            "<w:r><w:t>Linked</w:t></w:r></w:hyperlink>"
        )
    )
    paragraph._p.append(
        parse_xml(
            f'<w:ins {nsdecls("w")} w:id="1" w:author="a" w:date="2024-01-01T00:00:00Z">'
            "<w:r><w:t>Inserted</w:t></w:r></w:ins>"
        )
    )
    paragraph._p.append(
        parse_xml(
            f'<w:del {nsdecls("w")} w:id="2" w:author="a" w:date="2024-01-01T00:00:00Z">'
            "<w:r><w:delText>Deleted</w:delText></w:r></w:del>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Jane Candidate\tBerlinLinked"]
    assert [(item.kind, item.location, item.count) for item in content.limitations] == [
        ("tracked_change", "body", 2)
    ]


def test_read_docx_reports_an_unrecognized_block_wrapper(tmp_path: Path) -> None:
    """A wrapper this reader cannot read in place is named, never skipped."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    body = document.element.body
    body.insert(
        len(body) - 1,
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="odd">'
            "<w:p><w:r><w:t>Inside wrapper</w:t></w:r></w:p>"
            "<w:r><w:t>Wrapped visible</w:t></w:r>"
            "</w:customXml>"
        ),
    )
    document.add_paragraph("After wrapper")
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Jane Candidate", "After wrapper"]
    assert [(item.kind, item.location, item.count) for item in content.limitations] == [
        ("unknown_block", "body child 2", 1)
    ]
    limitation = content.limitations[0]
    assert limitation.element == "customXml"
    assert "body child 2 (customXml)" in limitation.message
    assert "does not recognize is not read" in limitation.message


def test_read_docx_reads_a_first_page_header_defined_before_its_using_section(
    tmp_path: Path,
) -> None:
    """A definition parked in an earlier section is read where Word displays it."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.sections[0].first_page_header.paragraphs[0].text = "Inherited first page"
    document.add_paragraph("Section one body")
    document.add_section()
    document.sections[1].different_first_page_header_footer = True
    document.add_paragraph("Section two body")
    document.save(path)

    content = read_docx(path)

    assert [
        (item.location, item.variant, item.section_index, item.text)
        for item in content.header_footer
    ] == [("header", "first_page", 2, "Inherited first page")]
    assert content.plain_text.splitlines()[0] == "Inherited first page"
    assert content.limitations == []


def test_read_docx_reads_runs_inside_an_inline_wrapper(tmp_path: Path) -> None:
    """A structural wrapper around runs keeps its text and its position."""
    path = tmp_path / "resume.docx"
    document = Document()
    citizenship = document.add_paragraph("Citizenship: ")
    citizenship._p.append(
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="field">'
            "<w:r><w:t>Inline wrapped</w:t></w:r></w:customXml>"
        )
    )
    middle = document.add_paragraph("Before ")
    middle._p.append(
        parse_xml(
            f'<w:smartTag {nsdecls("w")} w:element="tag">'
            "<w:r><w:t>middle</w:t></w:r></w:smartTag>"
        )
    )
    middle._p.append(
        parse_xml(f'<w:r {nsdecls("w")}><w:t xml:space="preserve"> after</w:t></w:r>')
    )
    table = document.add_table(rows=1, cols=1)
    table.rows[0].cells[0].paragraphs[0]._p.append(
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="field">'
            "<w:r><w:t>In cell</w:t></w:r></w:customXml>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Citizenship: Inline wrapped",
        "Before middle after",
        "In cell",
    ]
    assert content.limitations == []


def test_read_docx_reports_tracked_moves(tmp_path: Path) -> None:
    """A moved block is a tracked revision, so it is reported like an insertion."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    body = document.element.body
    for revision_id, tag in enumerate(("moveFrom", "moveTo"), start=1):
        body.insert(
            len(body) - 1,
            parse_xml(
                f'<w:{tag} {nsdecls("w")} w:id="{revision_id}" w:author="recruiter" '
                'w:date="2026-01-01T00:00:00Z">'
                "<w:p><w:r><w:t>Moved line</w:t></w:r></w:p>"
                f"</w:{tag}>"
            ),
        )
    document.save(path)

    content = read_docx(path)

    assert "Moved line" not in content.plain_text
    assert [(item.kind, item.location, item.count) for item in content.limitations] == [
        ("tracked_change", "body", 2)
    ]
    assert "moves are not resolved" in content.limitations[0].message


def test_read_docx_reports_an_embedded_alt_chunk(tmp_path: Path) -> None:
    """An alternate-format chunk holds text outside the body text stream."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    chunk = Part(
        PackURI("/word/chunk0.html"),
        "text/html",
        b"<html><body><p>Chunk text</p></body></html>",
        document.part.package,
    )
    chunk_id = document.part.relate_to(chunk, RT.A_F_CHUNK)
    body = document.element.body
    body.insert(
        len(body) - 1,
        parse_xml(f'<w:altChunk {nsdecls("w", "r")} r:id="{chunk_id}"/>'),
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Jane Candidate"]
    assert [(item.kind, item.location, item.count) for item in content.limitations] == [
        ("alt_chunk", "body", 1)
    ]
    assert "alternate-format chunk" in content.limitations[0].message
    assert "Review the source document" in content.limitations[0].message


def test_read_docx_with_embedded_image_preserves_text_extraction(tmp_path: Path) -> None:
    path = tmp_path / "resume-with-image.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    document.add_picture(io.BytesIO(_one_pixel_png()), width=Inches(0.25))
    document.add_picture(io.BytesIO(_one_pixel_png()), width=Inches(0.25))
    document.add_paragraph("Skills: Python, SQL")
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Jane Candidate", "Skills: Python, SQL"]
    assert content.plain_text == "Jane Candidate\nSkills: Python, SQL"
    # One entry per part with the count, not one per image.
    assert [(item.kind, item.location, item.count) for item in content.limitations] == [
        ("visual_element", "body", 2)
    ]
    assert "OCR is out of scope" in content.limitations[0].message
    # Pixels are not lost text, so this gap is reported but never blocks review.
    assert content.limitations[0].blocks_source_review is False


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


def _content_control(inner_xml: str) -> object:
    return parse_xml(
        f'<w:sdt {nsdecls("w")}><w:sdtContent>{inner_xml}</w:sdtContent></w:sdt>'
    )


def _one_pixel_png() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X3mNvwAAAABJRU5ErkJggg=="
    )
