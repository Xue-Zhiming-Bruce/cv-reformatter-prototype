from collections import Counter
from collections.abc import Callable
from pathlib import Path
import base64
import io
import time
import zipfile

import pytest
from docx import Document
from docx.document import Document as DocumentObject
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches

from app.extraction.coverage_audit import audit_segmentation_lines
from app.ingestion.docx_reader import (
    ExtractedDocxContent,
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


def test_read_docx_reads_a_nested_table_in_its_parent_cell(tmp_path: Path) -> None:
    """Issue #56: a table in a cell keeps its own columns and its parent position."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Before table")
    outer = document.add_table(rows=1, cols=2)
    nested = outer.rows[0].cells[0].add_table(rows=1, cols=2)
    nested.rows[0].cells[0].text = "Nested one"
    nested.rows[0].cells[1].text = "Nested two"
    outer.rows[0].cells[1].text = "Sibling cell"
    document.add_paragraph("After table")
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Before table",
        "Nested one | Nested two | Sibling cell",
        "After table",
    ]
    # The nested table was read where it sat, so its own gap is closed.
    assert content.limitations == []
    assert content.plain_text.count("Nested one") == 1


def test_read_docx_reads_text_box_text_at_its_anchor_and_flags_tracked_changes(
    tmp_path: Path,
) -> None:
    """Visible content outside the supported shapes is attributed, never silent."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    boxed = document.add_paragraph("Contact: ")
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

    # The box's text follows the paragraph that anchors it; a tracked insertion is
    # still a revision this reader does not resolve.
    assert content.plain_text.splitlines() == [
        "Jane Candidate",
        "Contact:",
        "Boxed contact",
        "Skills:",
    ]
    assert sorted(item.kind for item in content.limitations) == ["tracked_change"]


def test_read_docx_reads_a_paragraph_nested_in_two_wrappers(tmp_path: Path) -> None:
    """Issue #56 AC2: ``customXml > smartTag > w:p`` is read once, with no limitation."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    document.element.body.insert(
        len(document.element.body) - 1,
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="resume">'
            '<w:smartTag w:uri="urn:example" w:element="skills">'
            "<w:p><w:r><w:t>Inside smart tag</w:t></w:r></w:p>"
            "</w:smartTag></w:customXml>"
        ),
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Jane Candidate", "Inside smart tag"]
    assert content.plain_text.count("Inside smart tag") == 1
    assert content.limitations == []


def test_read_docx_reads_a_table_nested_in_wrappers_in_a_cell(tmp_path: Path) -> None:
    """A wrapper of wrappers is walked the same way at block, row and cell level."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    table = document.add_table(rows=1, cols=1)
    table.rows[0].cells[0]._tc.append(
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="resume">'
            "<w:smartTag w:uri=\"urn:x\" w:element=\"role\">"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>NESTED_WRAPPED</w:t></w:r></w:p>"
            "</w:tc></w:tr></w:tbl>"
            "</w:smartTag></w:customXml>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Jane Candidate", "NESTED_WRAPPED"]
    assert content.limitations == []


def test_read_docx_reads_a_text_box_in_a_header(tmp_path: Path) -> None:
    """AC3: the same walk reads a box in a header, with the header's provenance."""
    path = tmp_path / "resume.docx"
    document = Document()
    header = document.sections[0].header
    header.paragraphs[0].text = "Header anchor"
    header.paragraphs[0]._p.append(
        parse_xml(
            f'<w:r {nsdecls("w")}>'
            '<w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml">'
            "<v:textbox><w:txbxContent><w:p><w:r><w:t>Header boxed</w:t></w:r></w:p>"
            "</w:txbxContent></v:textbox></v:shape></w:pict></w:r>"
        )
    )
    document.add_paragraph("Jane Candidate")
    document.save(path)

    content = read_docx(path)

    assert content.header_footer[0].text.splitlines() == ["Header anchor", "Header boxed"]
    assert content.plain_text.splitlines() == [
        "Header anchor",
        "Header boxed",
        "Jane Candidate",
    ]
    assert content.limitations == []


def test_read_docx_reads_a_text_box_through_its_own_blocks(tmp_path: Path) -> None:
    """A box holds blocks, so its table and its paragraph keep their own order."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    anchor = document.add_paragraph()
    anchor._p.append(
        parse_xml(
            f'<w:r {nsdecls("w")}>'
            '<w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml">'
            "<v:textbox><w:txbxContent>"
            "<w:p><w:r><w:t>Box first</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Box cell</w:t></w:r></w:p>"
            "</w:tc></w:tr></w:tbl>"
            "<w:p><w:r><w:t>Box last</w:t></w:r></w:p>"
            "</w:txbxContent></v:textbox></v:shape></w:pict></w:r>"
        )
    )
    document.add_paragraph("After box")
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Jane Candidate",
        "Box first",
        "Box cell",
        "Box last",
        "After box",
    ]
    assert content.limitations == []


def test_read_docx_reads_a_text_box_anchored_by_another_text_box_once(
    tmp_path: Path,
) -> None:
    """AC3: a box in a box is read once, in the outer box's own paragraph."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    anchor = document.add_paragraph("Anchor: ")
    anchor._p.append(
        parse_xml(
            f'<w:r {nsdecls("w")}>'
            '<w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml"><v:textbox>'
            "<w:txbxContent>"
            "<w:p><w:r><w:t>OUTER</w:t></w:r></w:p>"
            "<w:p><w:r><w:pict>"
            '<v:shape xmlns:v="urn:schemas-microsoft-com:vml"><v:textbox>'
            "<w:txbxContent><w:p><w:r><w:t>INNER</w:t></w:r></w:p></w:txbxContent>"
            "</v:textbox></v:shape></w:pict></w:r></w:p>"
            "</w:txbxContent>"
            "</v:textbox></v:shape></w:pict></w:r>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Jane Candidate",
        "Anchor:",
        "OUTER",
        "INNER",
    ]
    assert content.plain_text.count("INNER") == 1
    assert content.limitations == []


def test_read_docx_reads_a_text_box_in_a_table_in_a_text_box_once(
    tmp_path: Path,
) -> None:
    """AC3: the outer box's table carries the inner box, which is still read once."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    anchor = document.add_paragraph()
    anchor._p.append(
        parse_xml(
            f'<w:r {nsdecls("w")}>'
            '<w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml"><v:textbox>'
            "<w:txbxContent>"
            "<w:p><w:r><w:t>OUTER</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:pict>"
            '<v:shape xmlns:v="urn:schemas-microsoft-com:vml"><v:textbox>'
            "<w:txbxContent><w:p><w:r><w:t>BOX IN CELL</w:t></w:r></w:p></w:txbxContent>"
            "</v:textbox></v:shape></w:pict></w:r></w:p></w:tc></w:tr></w:tbl>"
            "</w:txbxContent>"
            "</v:textbox></v:shape></w:pict></w:r>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Jane Candidate",
        "OUTER",
        "BOX IN CELL",
    ]
    assert content.plain_text.count("BOX IN CELL") == 1
    assert content.limitations == []


def test_read_docx_does_not_read_a_text_box_in_the_branch_it_skipped(
    tmp_path: Path,
) -> None:
    """Branch selection decides the box too, so the box is not read twice."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Alternative: ")
    paragraph._p.append(
        parse_xml(
            '<mc:AlternateContent '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            f'{nsdecls("w")}>'
            '<mc:Choice><w:r><w:t>CHOSEN</w:t></w:r></mc:Choice>'
            "<mc:Fallback><w:r><w:pict>"
            '<v:shape xmlns:v="urn:schemas-microsoft-com:vml"><v:textbox>'
            "<w:txbxContent><w:p><w:r><w:t>SKIPPED BOX</w:t></w:r></w:p>"
            "</w:txbxContent></v:textbox></v:shape></w:pict></w:r></mc:Fallback>"
            "</mc:AlternateContent>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Alternative: CHOSEN"]
    assert content.limitations == []


def test_read_docx_names_a_field_inside_a_text_box_it_reads(tmp_path: Path) -> None:
    """AC5: reading a box does not start reading a field's cached result."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    anchor = document.add_paragraph()
    anchor._p.append(
        parse_xml(
            f'<w:r {nsdecls("w")}>'
            '<w:pict><v:shape xmlns:v="urn:schemas-microsoft-com:vml">'
            "<v:textbox><w:txbxContent><w:p><w:r><w:t>Read me</w:t></w:r>"
            '<w:fldSimple w:instr=" REF candidate_name ">'
            "<w:r><w:t>Field result</w:t></w:r></w:fldSimple>"
            "</w:p></w:txbxContent></v:textbox></v:shape></w:pict></w:r>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Jane Candidate", "Read me"]
    assert [(item.kind, item.count) for item in content.limitations] == [("field", 1)]


def test_read_docx_bounds_a_deeply_nested_table_chain(tmp_path: Path) -> None:
    """AC6: deep nesting is declined at a bound, not by the interpreter's limit."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    inner = "<w:p><w:r><w:t>DEEP TEXT</w:t></w:r></w:p>"
    for _ in range(59):
        inner = f"<w:tbl><w:tr><w:tc>{inner}</w:tc></w:tr></w:tbl>"
    document.element.body.insert(
        len(document.element.body) - 1,
        parse_xml(f'<w:tbl {nsdecls("w")}><w:tr><w:tc>{inner}</w:tc></w:tr></w:tbl>'),
    )
    document.save(path)

    content = read_docx(path)

    assert "Jane Candidate" in content.plain_text
    kinds = {item.kind for item in content.limitations}
    assert "nesting" in kinds
    assert content.plain_text.count("DEEP TEXT") == 0
    assert all(item.blocks_source_review for item in content.limitations)


def test_read_docx_bounds_a_deeply_nested_wrapper_chain(tmp_path: Path) -> None:
    """AC6: a wrapper chain past the bound is named once instead of recursed into."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    inner = "<w:p><w:r><w:t>DEEP WRAPPED</w:t></w:r></w:p>"
    for _ in range(119):
        inner = f'<w:customXml w:element="level">{inner}</w:customXml>'
    document.element.body.insert(
        len(document.element.body) - 1,
        parse_xml(f'<w:customXml {nsdecls("w")} w:element="level">{inner}</w:customXml>'),
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Jane Candidate"]
    (limitation,) = content.limitations
    assert limitation.kind == "unknown_block"
    assert limitation.element == "customXml"
    assert limitation.blocks_source_review is True


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


def test_read_docx_reads_content_controls_where_they_sit(tmp_path: Path) -> None:
    """A control's runs are read in place, in a paragraph and in a table cell."""
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

    assert content.plain_text.splitlines() == ["Phone: 555 0100", "In-cell value"]
    assert content.limitations == []


def test_read_docx_reports_a_content_control_no_walk_descends(tmp_path: Path) -> None:
    """A control inside tracked change content is named, never read or hidden."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Phone: ")
    paragraph._p.append(
        parse_xml(
            f'<w:ins {nsdecls("w")} w:id="1" w:author="a" w:date="2024-01-01T00:00:00Z">'
            "<w:sdt><w:sdtPr/><w:sdtContent><w:r><w:t>555 0100</w:t></w:r>"
            "</w:sdtContent></w:sdt>"
            "</w:ins>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Phone:"]
    assert sorted((item.kind, item.count) for item in content.limitations) == [
        ("content_control", 1),
        ("tracked_change", 1),
    ]
    control = next(item for item in content.limitations if item.kind == "content_control")
    assert "read in place" in control.message


def test_read_docx_reads_property_wrapped_content_without_a_limitation(
    tmp_path: Path,
) -> None:
    """A ``*Pr`` child describes its container, so it is never unread content."""
    path = tmp_path / "resume.docx"
    document = Document()
    body = document.element.body
    body.insert(
        len(body) - 1,
        parse_xml(
            f'<w:sdt {nsdecls("w")}><w:sdtPr><w:alias w:val="Summary"/></w:sdtPr>'
            "<w:sdtContent><w:p><w:r><w:t>Block control</w:t></w:r></w:p>"
            "</w:sdtContent><w:sdtEndPr/></w:sdt>"
        ),
    )
    wrapper = document.add_paragraph("Wrapper: ")
    wrapper._p.append(
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="field"><w:customXmlPr>'
            '<w:attr w:name="format" w:val="text"/>'
            "</w:customXmlPr><w:r><w:t>Python</w:t></w:r>"
            # a property element this reader has not named is still not content
            "<w:novelPr/></w:customXml>"
        )
    )
    wrapper._p.append(
        parse_xml(
            f'<w:smartTag {nsdecls("w")} w:uri="urn:x" w:element="skill">'
            '<w:smartTagPr><w:attr w:name="format" w:val="text"/></w:smartTagPr>'
            "<w:r><w:t>, SQL</w:t></w:r></w:smartTag>"
        )
    )
    inline = document.add_paragraph("Control: ")
    inline._p.append(
        parse_xml(
            f'<w:sdt {nsdecls("w")}><w:sdtPr><w:text/></w:sdtPr><w:sdtContent>'
            "<w:r><w:t>555 0100</w:t></w:r></w:sdtContent></w:sdt>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == [
        "Block control",
        "Wrapper: Python, SQL",
        "Control: 555 0100",
    ]
    assert content.limitations == []
    audit = audit_segmentation_lines(
        content.plain_text,
        content.plain_text.splitlines(),
        *unread_content_messages(content.limitations),
    )
    assert audit.status == "passed"


def test_read_docx_still_names_a_field_beside_property_wrappers(
    tmp_path: Path,
) -> None:
    """Skipping properties must not silence a content gap in the same paragraph."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Ref: ")
    paragraph._p.append(
        parse_xml(
            f'<w:customXml {nsdecls("w")} w:element="field">'
            '<w:customXmlPr><w:attr w:name="format" w:val="text"/></w:customXmlPr>'
            "<w:r><w:t>plain text</w:t></w:r></w:customXml>"
        )
    )
    paragraph._p.append(
        parse_xml(
            f'<w:fldSimple {nsdecls("w")} w:instr=" REF candidate_name ">'
            "<w:r><w:t>Field result</w:t></w:r></w:fldSimple>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Ref: plain text"]
    assert [(item.kind, item.count) for item in content.limitations] == [("field", 1)]
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


def test_read_docx_reads_ruby_base_text_and_reports_the_annotation(tmp_path: Path) -> None:
    """The base is the text in the line; the annotation is visible, unread text."""
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

    assert content.plain_text.splitlines() == ["Name: 田中"]
    assert [(item.kind, item.location, item.count) for item in content.limitations] == [
        ("ruby", "body child 1", 1)
    ]
    assert "pronunciation annotation is not read" in content.limitations[0].message
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


def test_read_docx_decides_every_element_of_the_inline_content_model(
    tmp_path: Path,
) -> None:
    """Guard: every inline element has a decision, so none can be dropped silently.

    Each element of the enumerated model is placed in its own paragraph. The walk
    must render, descend, count, ignore or report each one; an element added to
    the model without a decision fails here instead of in a review round.
    """
    from app.ingestion.docx_reader import (
        _INLINE_CONTENT_TAGS,
        _INLINE_DECISIONS,
        _MATH,
        _MATH_PARAGRAPH,
    )

    assert set(_INLINE_DECISIONS) == _INLINE_CONTENT_TAGS
    # Every math element has one entry, in the math namespace Word writes: a
    # ``w:oMath`` copy beside ``m:oMath`` would never match a document.
    assert {
        tag for tag in _INLINE_CONTENT_TAGS if _local_name(tag) in {"oMath", "oMathPara"}
    } == {_MATH, _MATH_PARAGRAPH}

    rendered = {
        "t": "",
        "tab": "\t",
        "ptab": "\t",
        "br": "\n",
        "cr": "\n",
        "noBreakHyphen": "\u2011",
        "softHyphen": "\u00ad",
        "sym": "",
        "lastRenderedPageBreak": "",
        "oMath": "",
        "oMathPara": "",
    }
    counted = {
        "drawing": "visual_element",
        "pict": "visual_element",
        "fldSimple": "field",
        "ins": "tracked_change",
        "del": "tracked_change",
        "moveFrom": "tracked_change",
        "moveTo": "tracked_change",
        "altChunk": "alt_chunk",
        "subDoc": "alt_chunk",
        "footnoteReference": "note",
        "endnoteReference": "note",
        "commentReference": "note",
    }
    path = tmp_path / "resume.docx"
    document = Document()
    order: list[tuple[str, str, str]] = []
    for index, (tag, decision) in enumerate(_INLINE_DECISIONS.items(), start=1):
        name = _local_name(tag)
        paragraph = document.add_paragraph("A")
        paragraph.add_run()._r.append(_bare_element(tag))
        paragraph.add_run("B")
        order.append((name, decision, f"body child {index}"))
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == [
        f"A{rendered[name] if decision == 'render' else ''}B"
        for name, decision, _ in order
    ]
    inline = {item.location: item for item in content.limitations if item.location != "body"}
    part_counted = {
        item.kind: item.count for item in content.limitations if item.location == "body"
    }
    for name, decision, location in order:
        if decision == "render" or decision == "descend" or decision == "empty":
            assert location not in inline, inline[location].message
        elif decision == "report":
            assert inline[location].kind == "unknown_inline"
            assert inline[location].element == name
        else:
            assert counted[name], name
            assert location not in inline
    assert part_counted == dict(
        Counter(counted[name] for name, decision, _ in order if decision == "counted")
    )


def test_read_docx_renders_symbol_characters_without_dropping_a_glyph(
    tmp_path: Path,
) -> None:
    """A w:sym is a character in the font's code page, never a silent omission."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Contact: ")
    paragraph.add_run()._r.append(
        parse_xml(
            f'<w:sym {nsdecls("w")} w:font="Wingdings" w:char="F028"/>'
        )
    )
    paragraph.add_run(" 555-0100 ")
    paragraph.add_run()._r.append(
        parse_xml(f'<w:sym {nsdecls("w")} w:font="Segoe UI Symbol" w:char="2713"/>')
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Contact: \uf028 555-0100 \u2713"]
    # Only the font-specific code page is reported: its glyph is not Unicode text.
    (limitation,) = content.limitations
    assert (limitation.kind, limitation.location, limitation.element) == (
        "symbol",
        "body child 1",
        "Wingdings",
    )
    assert "font-specific" in limitation.message
    # The code point did reach the text, so this is reported but never blocking.
    assert limitation.blocks_source_review is False


def test_read_docx_keeps_hyphen_characters_in_the_read_text(tmp_path: Path) -> None:
    """A soft or no-break hyphen renders a character, so the run text keeps it."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph()
    for text in ("pre", "post"):
        paragraph.add_run(text)
        if text == "pre":
            paragraph.add_run()._r.append(
                parse_xml(f'<w:softHyphen {nsdecls("w")}/>')
            )
    paragraph.add_run(" and no")
    paragraph.add_run()._r.append(parse_xml(f'<w:noBreakHyphen {nsdecls("w")}/>'))
    paragraph.add_run("break")
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["pre\u00adpost and no\u2011break"]
    assert content.limitations == []


def test_read_docx_reports_a_run_child_outside_the_content_model(
    tmp_path: Path,
) -> None:
    """An unanticipated run child is named whether or not it holds a w:t."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Jane Candidate")
    paragraph.add_run()._r.append(
        parse_xml(f'<w:futureRunContent {nsdecls("w")}/>')
    )
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Jane Candidate"]
    assert [(item.kind, item.location, item.element) for item in content.limitations] == [
        ("unknown_inline", "body child 1", "futureRunContent")
    ]
    assert content.limitations[0].blocks_source_review is True


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


def test_read_docx_names_text_a_drawing_carries_as_unread_text(tmp_path: Path) -> None:
    """A drawing is not only pixels: the text it draws is named, not passed as a picture."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Name: ")
    paragraph._p.append(
        parse_xml(
            f'<w:r {nsdecls("w", "a", "wp")} '
            'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            "<w:drawing><wp:inline><a:graphic><a:graphicData"
            ' uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            "<wps:wsp><wps:txBody><a:p><a:r><a:t>DRAWN TEXT</a:t></a:r></a:p>"
            "</wps:txBody></wps:wsp></a:graphicData></a:graphic></wp:inline></w:drawing>"
            "</w:r>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Name:"]
    # DrawingML text is visible content this reader does not read, so it is a text
    # box that blocks review rather than a picture reported in passing.
    assert [
        (item.kind, item.count, item.blocks_source_review)
        for item in content.limitations
    ] == [("text_box", 1, True)]
    audit = audit_segmentation_lines(
        content.plain_text,
        content.plain_text.splitlines(),
        *unread_content_messages(content.limitations),
    )
    assert audit.status == "incomplete"


def test_read_docx_names_text_a_drawing_carries_beside_a_text_box(
    tmp_path: Path,
) -> None:
    """AC5: reading a drawing's box does not silence the paint text beside it."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Shape: ")
    paragraph._p.append(
        parse_xml(
            f'<w:r {nsdecls("w", "a", "wp")} '
            'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            "<w:drawing><wp:inline><a:graphic><a:graphicData"
            ' uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            "<wps:wsp><wps:txBody><a:p><a:r><a:t>PAINT ONLY</a:t></a:r></a:p>"
            "</wps:txBody>"
            "<wps:txbx><w:txbxContent><w:p><w:r><w:t>BOXED</w:t></w:r></w:p>"
            "</w:txbxContent></wps:txbx></wps:wsp></a:graphicData></a:graphic></wp:inline>"
            "</w:drawing></w:r>"
        )
    )
    document.save(path)

    content = read_docx(path)

    # The box is read; the shape's own paint text is not, so the drawing keeps a
    # blocking text-box entry for it.
    assert content.plain_text.splitlines() == ["Shape:", "BOXED"]
    assert "PAINT ONLY" not in content.plain_text
    assert [
        (item.kind, item.count, item.blocks_source_review)
        for item in content.limitations
    ] == [("text_box", 1, True)]


def test_read_docx_names_text_a_group_shape_carries_beside_a_text_box(
    tmp_path: Path,
) -> None:
    """AC5: a group child's paint text is named even when a sibling child has a box."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Group: ")
    paragraph._p.append(
        parse_xml(
            f'<w:r {nsdecls("w", "a", "wp")} '
            'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
            'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup">'
            "<w:drawing><wp:inline><a:graphic><a:graphicData"
            ' uri="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup">'
            "<wpg:wgp>"
            "<wps:wsp><wps:txBody><a:p><a:r><a:t>PAINT ONLY</a:t></a:r></a:p>"
            "</wps:txBody></wps:wsp>"
            "<wps:wsp><wps:txbx><w:txbxContent><w:p><w:r><w:t>GROUP BOX</w:t></w:r></w:p>"
            "</w:txbxContent></wps:txbx></wps:wsp>"
            "</wpg:wgp></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.plain_text.splitlines() == ["Group:", "GROUP BOX"]
    assert [
        (item.kind, item.count, item.blocks_source_review)
        for item in content.limitations
    ] == [("text_box", 1, True)]


#: Prefixes for the DrawingML shape markup the truth table builds.
_SHAPE_NAMESPACES = (
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)


def _p(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _p_with(*runs: str) -> str:
    return f"<w:p>{''.join(runs)}</w:p>"


def _r(*content: str) -> str:
    return f"<w:r>{''.join(content)}</w:r>"


def _paint(text: str) -> str:
    """A shape whose only text is DrawingML paint: no walk renders ``a:t``."""
    return (
        "<wps:wsp><wps:txBody><a:p><a:r>"
        f"<a:t>{text}</a:t></a:r></a:p></wps:txBody></wps:wsp>"
    )


def _box(blocks: str) -> str:
    """A shape holding a ``w:txbxContent`` body Word 2010 writes (``wps:txbx``)."""
    return (
        "<wps:wsp><wps:txbx><w:txbxContent>"
        f"{blocks}</w:txbxContent></wps:txbx></wps:wsp>"
    )


def _legacy_box(blocks: str) -> str:
    """A VML text box, the other body Word writes around a block body."""
    return (
        "<w:pict><v:shape><v:textbox>"
        f"<w:txbxContent>{blocks}</w:txbxContent>"
        "</v:textbox></v:shape></w:pict>"
    )


def _drawing(*shapes: str) -> str:
    return (
        '<w:drawing><wp:inline><wp:extent cx="1" cy="1"/><a:graphic>'
        '<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/'
        f'wordprocessingShape">{"".join(shapes)}</a:graphicData>'
        "</a:graphic></wp:inline></w:drawing>"
    )


def _group_drawing(*shapes: str) -> str:
    return (
        '<w:drawing><wp:inline><wp:extent cx="1" cy="1"/><a:graphic>'
        '<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/'
        f'wordprocessingGroup"><wpg:wgp>{"".join(shapes)}</wpg:wgp>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing>"
    )


def _nested_box_chain(depth: int, leaf: str) -> str:
    """``depth`` drawings, each anchored in the box body of the one outside it."""
    for _ in range(depth):
        leaf = _r(_drawing(_box(_p_with(leaf))))
    return leaf


def _read_anchored_paragraph(
    tmp_path: Path, runs: str | Callable[[DocumentObject], str]
) -> ExtractedDocxContent:
    """Read a resume whose one body paragraph anchors these runs.

    A row that has to point at a part builds its runs against the document, so an
    image relationship id can be the one this package actually holds.
    """
    path = tmp_path / "resume.docx"
    document = Document()
    body = document.element.body
    markup = runs(document) if callable(runs) else runs
    body.insert(
        len(body) - 1,
        parse_xml(
            f'<w:p {nsdecls("w", "a", "wp")} {_SHAPE_NAMESPACES}>'
            f"<w:r><w:t>Anchor:</w:t></w:r>{markup}</w:p>"
        ),
    )
    document.save(path)
    return read_docx(path)


def _image_id(document: DocumentObject) -> str:
    """A relationship id the package resolves to an image, so a shape draws art."""
    return document.part.get_or_add_image(io.BytesIO(_one_pixel_png()))[0]


#: The drawing/text-box truth table: every shape a probe found or can nest, with
#: the text the walk must read and the gaps it must report. One row per cell, so a
#: fourth nesting cannot pass as art or go silent with unread text: a character is
#: quiet only when the walk read that exact content, and a box body's characters
#: are the box's whether or not it was read. A row whose markup points at a part
#: builds it against the document, so the id resolves the way a package does.
_DRAWING_TEXT_TRUTH_TABLE: list[
    tuple[
        str,
        str | Callable[[DocumentObject], str],
        list[str],
        list[tuple[str, int, bool]],
    ]
] = [
    (
        "paint_at_body_level",
        _r(_drawing(_paint("PAINT"))),
        ["Anchor:"],
        [("text_box", 1, True)],
    ),
    (
        "paint_beside_a_box_in_one_shape",
        _r(_drawing(_paint("PAINT") + _box(_p("BOX")))),
        ["Anchor:", "BOX"],
        [("text_box", 1, True)],
    ),
    (
        "paint_beside_a_box_in_a_group",
        _r(_group_drawing(_paint("PAINT"), _box(_p("GROUP BOX")))),
        ["Anchor:", "GROUP BOX"],
        [("text_box", 1, True)],
    ),
    ("box_only", _r(_drawing(_box(_p("BOX")))), ["Anchor:", "BOX"], []),
    (
        "paint_inside_a_read_legacy_box",
        _r(_legacy_box(_p("OUTER") + _p_with(_r(_drawing(_paint("PAINT IN BOX")))))),
        ["Anchor:", "OUTER"],
        [("text_box", 1, True)],
    ),
    (
        "paint_inside_a_read_wps_box",
        _r(_drawing(_box(_p("OUTER") + _p_with(_r(_drawing(_paint("PAINT IN BOX"))))))),
        ["Anchor:", "OUTER"],
        [("text_box", 1, True)],
    ),
    (
        "box_and_paint_inside_a_read_legacy_box",
        _r(
            _legacy_box(
                _p("OUTER")
                + _p_with(_r(_drawing(_box(_p("INNER BOX")) + _paint("PAINT BESIDE"))))
            )
        ),
        ["Anchor:", "OUTER", "INNER BOX"],
        [("text_box", 1, True)],
    ),
    (
        "box_and_paint_inside_a_read_wps_box",
        _r(
            _drawing(
                _box(
                    _p("OUTER")
                    + _p_with(
                        _r(_drawing(_box(_p("INNER BOX")) + _paint("PAINT BESIDE")))
                    )
                )
            )
        ),
        ["Anchor:", "OUTER", "INNER BOX"],
        [("text_box", 1, True)],
    ),
    (
        "paint_inside_the_inner_box_of_a_read_legacy_box",
        _r(
            _legacy_box(
                _p("OUTER")
                + _p_with(
                    _r(
                        _drawing(
                            _box(
                                _p("INNER BOX")
                                + _p_with(_r(_drawing(_paint("DEEP PAINT"))))
                            )
                        )
                    )
                )
            )
        ),
        ["Anchor:", "OUTER", "INNER BOX"],
        [("text_box", 1, True)],
    ),
    (
        "box_in_box",
        _r(_legacy_box(_p("OUTER") + _p_with(_r(_legacy_box(_p("INNER")))))),
        ["Anchor:", "OUTER", "INNER"],
        [],
    ),
    (
        "box_in_a_table_in_a_box",
        _r(
            _legacy_box(
                _p("OUTER")
                + "<w:tbl><w:tr><w:tc>"
                + _p_with(_r(_legacy_box(_p("BOX IN CELL"))))
                + "</w:tc></w:tr></w:tbl>"
            )
        ),
        ["Anchor:", "OUTER", "BOX IN CELL"],
        [],
    ),
    (
        "box_under_a_tracked_insertion",
        f'<w:ins w:id="1" w:author="recruiter">'
        f'{_r(_legacy_box(_p("TRACKED BOX")))}</w:ins>',
        ["Anchor:"],
        [("text_box", 1, True), ("tracked_change", 1, True)],
    ),
    (
        "paint_inside_an_unread_box_under_a_tracked_insertion",
        f'<w:ins w:id="1" w:author="recruiter">'
        f'{_r(_legacy_box(_p("UNREAD BOX") + _p_with(_r(_drawing(_paint("UNREAD PAINT"))))))}'
        "</w:ins>",
        ["Anchor:"],
        [("text_box", 2, True), ("tracked_change", 1, True)],
    ),
    (
        "paint_written_straight_into_a_read_box_body",
        _r(_legacy_box(_p_with("<w:r><a:t>BARE PAINT</a:t></w:r>"))),
        ["Anchor:"],
        [("unknown_inline", 1, True)],
    ),
    (
        "image_inside_a_read_box_body",
        lambda document: _r(
            _legacy_box(
                _p("OUTER")
                + _p_with(
                    _r(
                        '<w:pict><v:shape><v:imagedata r:id="'
                        f'{_image_id(document)}"/></v:shape></w:pict>'
                    )
                )
            )
        ),
        ["Anchor:", "OUTER"],
        [("visual_element", 1, False)],
    ),
    (
        "image_beside_a_read_box",
        lambda document: _r(
            _drawing(
                _box(_p("BOX")),
                '<wps:wsp><a:graphic><a:graphicData'
                ' uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                f'<a:blip r:embed="{_image_id(document)}"/>'
                "</a:graphicData></a:graphic></wps:wsp>",
            )
        ),
        ["Anchor:", "BOX"],
        [("visual_element", 1, False)],
    ),
    (
        "filled_shape_beside_a_read_box",
        _r(
            _drawing(
                _box(_p("BOX")),
                "<wps:wsp><wps:spPr><a:solidFill>"
                '<a:srgbClr val="FF0000"/></a:solidFill></wps:spPr></wps:wsp>',
            )
        ),
        ["Anchor:", "BOX"],
        [],
    ),
    (
        "legacy_shape_inside_a_read_box_body",
        _r(
            _legacy_box(
                _p("OUTER")
                + _p_with(
                    _r(
                        '<w:pict><v:shape><v:line from="0,0" to="1,1"/>'
                        "</v:shape></w:pict>"
                    )
                )
            )
        ),
        ["Anchor:", "OUTER"],
        [("visual_element", 1, False)],
    ),
    (
        "wordart_inside_a_read_box_body",
        _r(
            _drawing(
                _box(
                    _p("OUTER")
                    + _p_with(
                        _r(
                            '<w:pict><v:shape><v:textpath string="WORDART"/>'
                            "</v:shape></w:pict>"
                        )
                    )
                )
            )
        ),
        ["Anchor:", "OUTER"],
        [("visual_element", 1, False)],
    ),
]


@pytest.mark.parametrize(
    ("runs", "lines", "limitations"),
    [row[1:] for row in _DRAWING_TEXT_TRUTH_TABLE],
    ids=[row[0] for row in _DRAWING_TEXT_TRUTH_TABLE],
)
def test_read_docx_keeps_drawing_text_blocking_until_the_walk_reads_it(
    tmp_path: Path,
    runs: str,
    lines: list[str],
    limitations: list[tuple[str, int, bool]],
) -> None:
    """AC5/AC7: every cell of the truth table, one row per shape."""
    content = _read_anchored_paragraph(tmp_path, runs)

    assert content.plain_text.splitlines() == lines
    assert [
        (item.kind, item.count, item.blocks_source_review)
        for item in content.limitations
    ] == limitations
    # Art is never the bucket unread text falls into: it is reported, never blocking.
    assert all(
        not item.blocks_source_review
        for item in content.limitations
        if item.kind == "visual_element"
    )


def test_read_docx_bounds_a_deep_drawing_chain_inside_boxes(tmp_path: Path) -> None:
    """AC6: deep drawing nesting costs one pass, not one more pass per level.

    ``_MAX_BLOCK_DEPTH`` bounds the walk, so the art accounting beside it has to
    stay bounded too: a hostile package nests drawings this deep. ``prompt`` is a
    wide margin over the ~0.02s this takes; re-deciding every nested drawing grew
    by a factor of two per level instead (18s at depth 20, minutes at depth 24).
    """
    prompt = 10.0
    for leaf, lines, limitations in (
        ("<w:t>DEEP BOX</w:t>", ["Anchor:", "DEEP BOX"], []),
        (_r(_drawing(_paint("DEEP PAINT"))), ["Anchor:"], [("text_box", 1, True)]),
        (_drawing(), ["Anchor:"], [("visual_element", 1, False)]),
    ):
        started = time.perf_counter()
        content = _read_anchored_paragraph(tmp_path, _nested_box_chain(24, leaf))
        elapsed = time.perf_counter() - started

        assert elapsed < prompt
        assert content.plain_text.splitlines() == lines
        assert [
            (item.kind, item.count, item.blocks_source_review)
            for item in content.limitations
        ] == limitations


def test_read_docx_reports_paint_text_in_a_header_and_footer_box(tmp_path: Path) -> None:
    """AC3/AC5: a header/footer box is read, and the paint inside it still blocks."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    for location, marker in (("header", "HEADER"), ("footer", "FOOTER")):
        anchor = getattr(document.sections[0], location).paragraphs[0]
        anchor.text = f"{marker.title()} anchor"
        anchor._p.append(
            parse_xml(
                f'<w:r {nsdecls("w", "a", "wp")} {_SHAPE_NAMESPACES}>'
                f'{_legacy_box(_p(f"{marker} BOX") + _p_with(_r(_drawing(_paint(f"{marker} PAINT")))))}'
                "</w:r>"
            )
        )
    document.save(path)

    content = read_docx(path)

    assert [(item.location, item.text) for item in content.header_footer] == [
        ("header", "Header anchor\nHEADER BOX"),
        ("footer", "Footer anchor\nFOOTER BOX"),
    ]
    assert content.paragraphs == ["Jane Candidate"]
    assert [
        (item.kind, item.count, item.blocks_source_review)
        for item in content.limitations
    ] == [("text_box", 1, True), ("text_box", 1, True)]


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


def test_read_docx_reads_block_office_math(tmp_path: Path) -> None:
    """Office Math carries its text in m:t, so an equation is visible source text."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Skills")
    body = document.element.body
    body.insert(
        len(body) - 1,
        parse_xml(
            f'<m:oMathPara {nsdecls("m", "w")}>'
            "<m:oMath><m:r><m:t>LOST</m:t></m:r></m:oMath></m:oMathPara>"
        ),
    )
    document.add_paragraph("After")
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Skills", "LOST", "After"]
    assert content.limitations == []
    audit = audit_segmentation_lines(
        content.plain_text, content.plain_text.splitlines()
    )
    assert audit.status == "passed"


def test_read_docx_reads_inline_office_math(tmp_path: Path) -> None:
    """A math zone inside a paragraph is read in the position it occupies."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Function: ")
    paragraph._p.append(
        parse_xml(
            f'<m:oMath {nsdecls("m", "w")}><m:r><m:t>f(x)=x</m:t></m:r></m:oMath>'
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Function: f(x)=x"]
    assert content.limitations == []


def test_read_docx_names_a_wrapper_holding_office_math(tmp_path: Path) -> None:
    """Text is detected in every text namespace, so a math-only wrapper is named."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Skills")
    body = document.element.body
    body.insert(
        len(body) - 1,
        parse_xml(
            f'<w:customXml {nsdecls("w", "m")} w:element="equation">'
            "<m:oMathPara><m:oMath><m:r><m:t>LOST</m:t></m:r></m:oMath></m:oMathPara>"
            "</w:customXml>"
        ),
    )
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Skills"]
    assert [(item.kind, item.element) for item in content.limitations] == [
        ("unknown_block", "customXml")
    ]
    assert content.limitations[0].blocks_source_review is True


def test_read_docx_reports_a_math_child_the_walk_does_not_read(
    tmp_path: Path,
) -> None:
    """A math zone is one more inline container, so an unread child is named."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Equation: ")
    paragraph._p.append(
        parse_xml(
            f'<m:oMath {nsdecls("m", "w")}>'
            "<m:r><m:t>x+1</m:t></m:r>"
            "<w:object><w:r><w:t>EMBEDDED</w:t></w:r></w:object>"
            "</m:oMath>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Equation: x+1"]
    (limitation,) = content.limitations
    assert (limitation.kind, limitation.element) == ("unknown_inline", "object")
    assert limitation.blocks_source_review is True


def test_read_docx_reads_wordprocessing_text_inside_a_math_zone(
    tmp_path: Path,
) -> None:
    """The math walk renders w:t as well as m:t, so one collector cannot drop it."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Equation: ")
    paragraph._p.append(
        parse_xml(
            f'<m:oMath {nsdecls("m", "w")}>'
            "<m:r><m:t>x=</m:t></m:r><w:r><w:t>y</w:t></w:r>"
            "</m:oMath>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Equation: x=y"]
    assert content.limitations == []


def test_read_docx_reads_a_referenced_footnote(tmp_path: Path) -> None:
    """A footnote's text is printed on the page, so it is read from its part."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Body text")
    paragraph._p.append(
        parse_xml(f'<w:r {nsdecls("w")}><w:footnoteReference w:id="1"/></w:r>')
    )
    _add_note_part(document, "footnotes", {"1": "Footnote text"})
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Body text", "Footnote text"]
    assert content.limitations == []


def test_read_docx_reads_a_referenced_endnote(tmp_path: Path) -> None:
    """An endnote is read the same way, from its own part."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Body text")
    paragraph._p.append(
        parse_xml(f'<w:r {nsdecls("w")}><w:endnoteReference w:id="2"/></w:r>')
    )
    _add_note_part(document, "endnotes", {"2": "Endnote text"})
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Body text", "Endnote text"]
    assert content.limitations == []


def test_read_docx_names_a_referenced_note_without_its_part(tmp_path: Path) -> None:
    """A reference whose note no part supplies is named, not ignored."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Body text")
    paragraph._p.append(
        parse_xml(f'<w:r {nsdecls("w")}><w:footnoteReference w:id="4"/></w:r>')
    )
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Body text"]
    (limitation,) = content.limitations
    assert (limitation.kind, limitation.location, limitation.count) == ("note", "body", 1)
    assert "not read" in limitation.message
    assert limitation.blocks_source_review is True


def test_read_docx_names_a_comment_body_it_does_not_open(tmp_path: Path) -> None:
    """A comment is a margin annotation, so a reference is reported per document."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Body text")
    paragraph._p.append(
        parse_xml(f'<w:r {nsdecls("w")}><w:commentReference w:id="3"/></w:r>')
    )
    _add_note_part(document, "comments", {"3": "Recruiter note"})
    document.save(path)

    content = read_docx(path)

    assert content.paragraphs == ["Body text"]
    (limitation,) = content.limitations
    assert (limitation.kind, limitation.count) == ("note", 1)
    audit = audit_segmentation_lines(
        content.plain_text,
        content.plain_text.splitlines(),
        *unread_content_messages(content.limitations),
    )
    assert audit.status == "incomplete"


def test_read_docx_leaves_a_note_in_an_unselected_branch_unread(
    tmp_path: Path,
) -> None:
    """Only a reference the walk reaches may bring a note's text into the read."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Body text ")
    paragraph._p.append(
        parse_xml(
            "<mc:AlternateContent "
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            f'{nsdecls("w")}>'
            '<mc:Choice Requires="wps"><w:r><w:t>CHOSEN</w:t></w:r></mc:Choice>'
            '<mc:Fallback><w:r><w:footnoteReference w:id="1"/></w:r></mc:Fallback>'
            "</mc:AlternateContent>"
        )
    )
    selected = document.add_paragraph("Selected: ")
    selected._p.append(
        parse_xml(f'<w:r {nsdecls("w")}><w:footnoteReference w:id="2"/></w:r>')
    )
    _add_note_part(
        document, "footnotes", {"1": "Discarded note", "2": "Selected note"}
    )
    document.save(path)

    content = read_docx(path)

    # The discarded branch renders nothing, so its note may not print either.
    assert content.paragraphs == ["Body text CHOSEN", "Selected:", "Selected note"]
    assert "Discarded note" not in content.plain_text
    assert content.limitations == []
    audit = audit_segmentation_lines(
        content.plain_text, content.plain_text.splitlines()
    )
    assert audit.status == "passed"


def test_read_docx_names_text_a_part_a_drawing_references_holds(
    tmp_path: Path,
) -> None:
    """A chart's title lives in the chart part, so it is unread text, not a picture."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Skills: Python")
    chart_id = _add_xml_part(
        document,
        "/word/charts/chart1.xml",
        "application/vnd.openxmlformats-officedocument.drawingml.chart+xml",
        _chart_part_xml("CHART VISIBLE LABEL"),
        RT.CHART,
    )
    paragraph._p.append(
        _drawing_with(f'<c:chart {nsdecls("c", "r")} r:id="{chart_id}"/>')
    )
    document.save(path)

    content = read_docx(path)

    assert "CHART VISIBLE LABEL" not in content.plain_text
    (limitation,) = content.limitations
    assert (
        limitation.kind,
        limitation.location,
        limitation.count,
    ) == ("embedded_part", "body", 1)
    assert limitation.blocks_source_review is True
    assert "chart, diagram or embedded object" in limitation.message
    audit = audit_segmentation_lines(
        content.plain_text,
        content.plain_text.splitlines(),
        *unread_content_messages(content.limitations),
    )
    assert audit.status == "incomplete"


def test_read_docx_names_every_part_a_drawing_references_not_known_types(
    tmp_path: Path,
) -> None:
    """The rule is the relationship target, not a list of part types."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Skills: Python")
    data_id = _add_xml_part(
        document,
        "/word/diagrams/data1.xml",
        "application/vnd.openxmlformats-officedocument.drawingml.diagramData+xml",
        f'<dgm:dataModel {nsdecls("dgm", "a")}><dgm:ptLst><dgm:pt><dgm:t>'
        "<a:p><a:r><a:t>DIAGRAM NODE</a:t></a:r></a:p></dgm:t></dgm:pt></dgm:ptLst>"
        "</dgm:dataModel>",
        RT.DIAGRAM_DATA,
    )
    paragraph._p.append(
        _drawing_with(
            f'<dgm:relIds {nsdecls("dgm", "r")} r:dm="{data_id}"/>'
        )
    )
    document.save(path)

    content = read_docx(path)

    assert "DIAGRAM NODE" not in content.plain_text
    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_names_a_drawing_reference_the_package_cannot_resolve(
    tmp_path: Path,
) -> None:
    """An id the package cannot resolve is not known to be pixels either."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Skills: Python")
    paragraph._p.append(
        _drawing_with(f'<c:chart {nsdecls("c", "r")} r:id="rId404"/>')
    )
    document.save(path)

    content = read_docx(path)

    (limitation,) = content.limitations
    assert (limitation.kind, limitation.count) == ("embedded_part", 1)
    assert limitation.blocks_source_review is True


def test_read_docx_ignores_text_in_a_drawing_choice_it_cannot_use(
    tmp_path: Path,
) -> None:
    """The fallback renders when the choice needs a namespace the document lacks."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Skills: Python")
    image_id, _image = document.part.get_or_add_image(io.BytesIO(_one_pixel_png()))
    paragraph._p.append(
        parse_xml(
            f'<w:r {nsdecls("w", "a", "wp", "r", "pic")}>'
            "<w:drawing><wp:inline><wp:extent cx=\"1\" cy=\"1\"/>"
            '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
            '<mc:Choice Requires="acme"><a:graphic><a:graphicData'
            ' uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            "<a:t>UNUSABLE CHOICE TEXT</a:t></a:graphicData></a:graphic></mc:Choice>"
            "<mc:Fallback><a:graphic><a:graphicData"
            ' uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic><pic:blipFill><a:blip r:embed="{image_id}"/></pic:blipFill></pic:pic>'
            "</a:graphicData></a:graphic></mc:Fallback>"
            "</mc:AlternateContent></wp:inline></w:drawing></w:r>"
        )
    )
    document.save(path)

    content = read_docx(path)

    (limitation,) = content.limitations
    assert limitation.kind == "visual_element"
    assert limitation.blocks_source_review is False


def test_read_docx_resolves_a_drawing_against_the_part_that_holds_it(
    tmp_path: Path,
) -> None:
    """A header's picture is related to the header part, not to the body."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    header = document.sections[0].header
    header.paragraphs[0].add_run().add_picture(
        io.BytesIO(_one_pixel_png()), width=Inches(0.25)
    )
    document.save(path)

    content = read_docx(path)

    (limitation,) = content.limitations
    assert (limitation.kind, limitation.location) == (
        "visual_element",
        "default header of section 1",
    )
    assert limitation.blocks_source_review is False


def _drawing_of_vector_image(tmp_path: Path, svg: str) -> ExtractedDocxContent:
    """A resume holding one drawing whose picture part is this vector image."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Skills: Python")
    _add_svg_drawing(document, svg)
    document.save(path)
    return read_docx(path)


def test_read_docx_names_text_a_vector_image_holds(tmp_path: Path) -> None:
    """An SVG is XML: a text element in it draws a character this reader never read."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/>'
        "<text>SVG LABEL</text></svg>",
    )

    assert "SVG LABEL" not in content.plain_text
    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_names_text_an_svg_prefix_does_not_hide(tmp_path: Path) -> None:
    """A local name decides the element, so a namespace prefix cannot hide text."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<s:svg xmlns:s="http://www.w3.org/2000/svg"><s:text>LOST SVG TEXT</s:text>'
        "</s:svg>",
    )

    assert "LOST SVG TEXT" not in content.plain_text
    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_names_a_tspan_only_vector_image_holds(tmp_path: Path) -> None:
    """A ``tspan`` draws a character too, so it is text as much as ``text`` is."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg"><tspan>TSPAN TEXT</tspan></svg>',
    )

    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_names_a_vector_image_that_does_not_parse(tmp_path: Path) -> None:
    """A part this reader cannot parse is not known to be pixels, so it is named."""
    content = _drawing_of_vector_image(
        tmp_path, '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1"'
    )

    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_keeps_a_text_free_vector_image_as_pixels(tmp_path: Path) -> None:
    """A vector icon draws shapes, not characters, so it stays a reported picture."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1v1z"/></svg>',
    )

    (limitation,) = content.limitations
    assert limitation.kind == "visual_element"
    assert limitation.blocks_source_review is False


def test_read_docx_keeps_an_svg_comment_naming_text_as_pixels(tmp_path: Path) -> None:
    """A comment is not an element, so text inside one is not text that renders."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/>'
        "<!-- <text is only a comment --></svg>",
    )

    (limitation,) = content.limitations
    assert limitation.kind == "visual_element"
    assert limitation.blocks_source_review is False


def test_read_docx_keeps_vector_metadata_as_pixels(tmp_path: Path) -> None:
    """``title``, ``desc`` and ``metadata`` describe the image; no viewer draws them."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg"><title>Star icon</title>'
        '<desc>A star, not a word</desc><metadata>edited by a tool</metadata></svg>',
    )

    (limitation,) = content.limitations
    assert limitation.kind == "visual_element"
    assert limitation.blocks_source_review is False


def test_read_docx_names_foreign_html_a_vector_image_holds(tmp_path: Path) -> None:
    """A ``foreignObject`` holds HTML, where a character is page text."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg"'
        ' xmlns:xhtml="http://www.w3.org/1999/xhtml">'
        '<foreignObject width="10" height="10"><xhtml:div>FOREIGN TEXT</xhtml:div>'
        "</foreignObject></svg>",
    )

    assert "FOREIGN TEXT" not in content.plain_text
    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_names_a_vector_image_whose_foreign_html_is_empty(
    tmp_path: Path,
) -> None:
    """A ``foreignObject`` holds HTML this reader does not read, empty or not."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject width="1" height="1"/>'
        "</svg>",
    )

    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_names_a_vector_image_whose_foreign_html_is_whitespace(
    tmp_path: Path,
) -> None:
    """Whitespace is not evidence: the markup beside it is still HTML."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject width="1" height="1">'
        " \n </foreignObject></svg>",
    )

    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_names_a_vector_image_whose_foreign_input_holds_a_value(
    tmp_path: Path,
) -> None:
    """An HTML ``input`` paints its value, which no character data holds."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg"'
        ' xmlns:xhtml="http://www.w3.org/1999/xhtml">'
        '<foreignObject width="10" height="10">'
        '<xhtml:input value="HIDDEN INPUT SENTENCE"/></foreignObject></svg>',
    )

    assert "HIDDEN INPUT SENTENCE" not in content.plain_text
    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_names_an_entity_a_vector_image_expands_to_text(
    tmp_path: Path,
) -> None:
    """An entity the parser never expands stands for text nobody read."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<!DOCTYPE svg [<!ENTITY label "HIDDEN SVG SENTENCE">]>'
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/>'
        "&label;</svg>",
    )

    assert "HIDDEN SVG SENTENCE" not in content.plain_text
    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_names_an_entity_a_vector_image_expands_to_an_element(
    tmp_path: Path,
) -> None:
    """An entity standing for a ``text`` element is that element's text, unread."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<!DOCTYPE svg [<!ENTITY label "<text>HIDDEN SVG TEXT</text>">]>'
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/>'
        "&label;</svg>",
    )

    assert "HIDDEN SVG TEXT" not in content.plain_text
    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_names_an_entity_in_paragraph_text(tmp_path: Path) -> None:
    """A ``w:t`` holding an entity drops text past the reference, so it blocks."""
    path = _paragraph_entity_docx(tmp_path)

    content = read_docx(path)

    assert "Jay Candidate" in content.plain_text
    assert "here" not in content.plain_text
    (limitation,) = content.limitations
    assert (limitation.kind, limitation.location, limitation.count) == (
        "unknown_inline",
        "body",
        1,
    )
    assert limitation.blocks_source_review is True


def test_read_docx_names_a_vector_part_that_is_not_svg(tmp_path: Path) -> None:
    """A part typed as SVG whose root is something else is not known to be art.

    The root decides, not its children: elements in the SVG namespace under another
    root are still not an image this reader can call art.
    """
    content = _drawing_of_vector_image(
        tmp_path,
        '<document xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/>'
        "</document>",
    )

    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_names_an_unknown_element_a_vector_image_holds(
    tmp_path: Path,
) -> None:
    """A construct this reader does not know may draw a character, so it blocks."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/>'
        "<unknownShape/></svg>",
    )

    (limitation,) = content.limitations
    assert limitation.kind == "embedded_part"
    assert limitation.blocks_source_review is True


def test_read_docx_keeps_a_vector_image_of_recognised_shapes(tmp_path: Path) -> None:
    """Shapes, their scaffolding and filters are art the reader can vouch for."""
    content = _drawing_of_vector_image(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<defs><linearGradient id="g"><stop offset="0"/></linearGradient>'
        '<clipPath id="c"><rect width="1" height="1"/></clipPath></defs>'
        '<style>.s{fill:red}</style>'
        '<g clip-path="url(#c)"><path d="M0 0h1v1z" fill="url(#g)"/>'
        '<circle r="1"/><ellipse rx="1" ry="1"/><line x2="1"/><polygon points="0,0"/>'
        '<polyline points="0,0"/><image href="pixel.png"/><use href="#g"/></g>'
        "<switch><rect width=\"1\" height=\"1\"/></switch></svg>",
    )

    (limitation,) = content.limitations
    assert limitation.kind == "visual_element"
    assert limitation.blocks_source_review is False


def test_read_docx_counts_each_drawing_that_points_at_an_unread_part(
    tmp_path: Path,
) -> None:
    """One entry per kind, counting drawings: two drawings of one part are two."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Skills: Python")
    svg_id = _add_svg_drawing(
        document,
        '<svg xmlns="http://www.w3.org/2000/svg"><text>SHARED LABEL</text></svg>',
    )
    document.add_paragraph()._p.append(_svg_drawing_run(svg_id))
    document.save(path)

    content = read_docx(path)

    (limitation,) = content.limitations
    assert (limitation.kind, limitation.count) == ("embedded_part", 2)


def test_read_docx_does_not_name_an_external_picture_link_as_text(
    tmp_path: Path,
) -> None:
    """An externally linked picture holds no text in the package at all."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Skills: Python")
    link_id = document.part.rels.get_or_add_ext_rel(
        RT.IMAGE, "https://example.com/headshot.png"
    )
    paragraph._p.append(
        parse_xml(
            f'<w:r {nsdecls("w")} xmlns:v="urn:schemas-microsoft-com:vml">'
            f'<w:pict><v:shape><v:imagedata r:id="{link_id}"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
            "</v:shape></w:pict></w:r>"
        )
    )
    document.save(path)

    content = read_docx(path)

    (limitation,) = content.limitations
    assert limitation.kind == "visual_element"
    assert limitation.blocks_source_review is False


def test_read_docx_ignores_text_in_a_drawing_branch_it_did_not_choose(
    tmp_path: Path,
) -> None:
    """The drawing is one container: only the selected branch renders text."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Skills: Python")
    image_id, _image = document.part.get_or_add_image(io.BytesIO(_one_pixel_png()))
    paragraph._p.append(
        parse_xml(
            f'<w:r {nsdecls("w", "a", "wp", "r", "pic")}>'
            "<w:drawing><wp:inline><wp:extent cx=\"1\" cy=\"1\"/>"
            '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
            '<mc:Choice Requires="wps"><a:graphic><a:graphicData'
            ' uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic><pic:blipFill><a:blip r:embed="{image_id}"/></pic:blipFill></pic:pic>'
            "</a:graphicData></a:graphic></mc:Choice>"
            "<mc:Fallback><a:graphic><a:graphicData"
            ' uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            "<a:t>DISCARDED TEXT</a:t></a:graphicData></a:graphic></mc:Fallback>"
            "</mc:AlternateContent></wp:inline></w:drawing></w:r>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert "DISCARDED TEXT" not in content.plain_text
    # The chosen branch is a picture: pixels, reported and never blocking.
    assert [(item.kind, item.count) for item in content.limitations] == [
        ("visual_element", 1)
    ]
    assert content.limitations[0].blocks_source_review is False
    audit = audit_segmentation_lines(
        content.plain_text, content.plain_text.splitlines()
    )
    assert audit.status == "passed"


def test_read_docx_counts_text_in_the_drawing_branch_it_chooses(tmp_path: Path) -> None:
    """Branch selection must not hide the chosen branch's own text."""
    path = tmp_path / "resume.docx"
    document = Document()
    paragraph = document.add_paragraph("Skills: Python")
    image_id, _image = document.part.get_or_add_image(io.BytesIO(_one_pixel_png()))
    paragraph._p.append(
        parse_xml(
            f'<w:r {nsdecls("w", "a", "wp", "r", "pic")}>'
            "<w:drawing><wp:inline><wp:extent cx=\"1\" cy=\"1\"/>"
            '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
            '<mc:Choice Requires="wps"><a:graphic><a:graphicData'
            ' uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            "<a:t>SELECTED TEXT</a:t></a:graphicData></a:graphic></mc:Choice>"
            "<mc:Fallback><a:graphic><a:graphicData"
            ' uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic><pic:blipFill><a:blip r:embed="{image_id}"/></pic:blipFill></pic:pic>'
            "</a:graphicData></a:graphic></mc:Fallback>"
            "</mc:AlternateContent></wp:inline></w:drawing></w:r>"
        )
    )
    document.save(path)

    content = read_docx(path)

    assert "SELECTED TEXT" not in content.plain_text
    (limitation,) = content.limitations
    assert limitation.kind == "text_box"
    assert limitation.blocks_source_review is True


def _add_xml_part(
    document: object, partname: str, content_type: str, xml: str, relationship: str
) -> str:
    """Attach a part a drawing references and return its relationship id."""
    part = Part(
        PackURI(partname), content_type, xml.encode(), document.part.package
    )
    return document.part.relate_to(part, relationship)


def _paragraph_entity_docx(tmp_path: Path) -> Path:
    """A resume whose ``w:t`` holds an entity reference, which python-docx cannot write."""
    path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jay Candidate")
    document.add_paragraph("Also ENTITY here")
    document.save(path)

    with zipfile.ZipFile(path) as package:
        parts = [(item, package.read(item.filename)) for item in package.infolist()]
    body = next(data for item, data in parts if item.filename == "word/document.xml")
    body = body.replace(b"ENTITY", b"&sentence;").replace(
        b"<w:document",
        b'<!DOCTYPE document [<!ENTITY sentence "HIDDEN SENTENCE">]><w:document',
        1,
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        for item, data in parts:
            package.writestr(
                item, body if item.filename == "word/document.xml" else data
            )
    return path


def _svg_drawing_run(svg_id: str) -> object:
    """A drawing run whose picture is the part this relationship id names."""
    return parse_xml(
        f'<w:r {nsdecls("w", "a", "wp", "r")}>'
        '<w:drawing><wp:inline><wp:extent cx="1" cy="1"/><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<a:blip r:embed="{svg_id}"/>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )


def _add_svg_drawing(document: object, svg: str) -> str:
    """Append a drawing whose picture part is this vector image; return its id."""
    svg_id = _add_xml_part(
        document, "/word/media/image1.svg", "image/svg+xml", svg, RT.IMAGE
    )
    document.add_paragraph()._p.append(_svg_drawing_run(svg_id))
    return svg_id


def _chart_part_xml(label: str) -> str:
    """A chart part whose title is visible text, in the shape Word writes it."""
    return (
        '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<c:chart><c:title><c:tx><c:rich><a:bodyPr/><a:p><a:r><a:t>"
        f"{label}</a:t></a:r></a:p></c:rich></c:tx></c:title></c:chart>"
        "</c:chartSpace>"
    )


def _drawing_with(reference_xml: str) -> object:
    """A drawing whose graphic data holds one reference to another part."""
    return parse_xml(
        f'<w:r {nsdecls("w", "a", "wp")}>'
        '<w:drawing><wp:inline><wp:extent cx="1" cy="1"/><a:graphic><a:graphicData'
        ' uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        f"{reference_xml}</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )


def _add_note_part(document: object, root: str, items: dict[str, str]) -> None:
    """Attach a footnotes/endnotes/comments part holding one paragraph per note."""
    relationship = {
        "footnotes": RT.FOOTNOTES,
        "endnotes": RT.ENDNOTES,
        "comments": RT.COMMENTS,
    }[root]
    item = root[:-1]
    xml = f'<w:{root} {nsdecls("w")}>' + "".join(
        f'<w:{item} w:id="{note_id}"><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:{item}>'
        for note_id, text in items.items()
    ) + f"</w:{root}>"
    part = Part(
        PackURI(f"/word/{root}.xml"),
        f"application/vnd.openxmlformats-officedocument.wordprocessingml.{root}+xml",
        xml.encode(),
        document.part.package,
    )
    document.part.relate_to(part, relationship)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _bare_element(tag: str) -> object:
    name = _local_name(tag)
    if "officeDocument/2006/math" in tag:
        return parse_xml(f'<m:{name} {nsdecls("m")}/>')
    if name == "AlternateContent":
        return parse_xml(
            '<mc:AlternateContent '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"/>'
        )
    return parse_xml(f'<w:{name} {nsdecls("w")}/>')


def _content_control(inner_xml: str) -> object:
    return parse_xml(
        f'<w:sdt {nsdecls("w")}><w:sdtContent>{inner_xml}</w:sdtContent></w:sdt>'
    )


def _one_pixel_png() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X3mNvwAAAABJRU5ErkJggg=="
    )
