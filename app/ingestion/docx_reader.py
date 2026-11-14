"""Read candidate DOCX text without losing source order or hiding source gaps.

Order and header policy (issue #49):

- Body content is read in authored order, so a table stays between the
  paragraphs around it: collecting every paragraph and every table separately
  and joining them paragraph-first moved table text to the end, which detaches a
  company/role row from the bullets it describes.
- Section headers and footers are read with their source location
  (``ExtractedHeaderFooter``). Header text joins ``plain_text`` ahead of the body
  and footer text after it: that is page order, not an invented one.
- A header/footer is read once, in the section that defines it. A section linked
  to the previous one inherits an already-read definition, and a header repeated
  on every page is a presentation fact rather than more source text; emitting
  either again would duplicate candidate facts and invent reading order.

What this reader reads (the whole read set):

- body paragraphs and top-level body tables;
- a block-level content control (``w:sdt``) at body level, whose paragraphs and
  tables are read in the position the control occupies, nested controls
  included;
- the same paragraph and table shapes inside a header/footer, read once per
  defining section.

What it reports instead of dropping (the whole limitation set, each naming its
location):

- a table inside a table cell, text-box content, and tracked
  insertions/deletions;
- a content control it does not descend into — one wrapping paragraph runs, or
  sitting in a table cell, header or footer;
- a simple field's cached visible result (``w:fldSimple``).

Inline content-control runs and ``w:fldSimple`` results are reported, not read:
reading them would need a second, hand-written paragraph-text parser beside
python-docx's own. Visible text the reader never supplied cannot be recovered by
a later check, so a passing source-coverage audit only proves that the text this
reader supplied reached the output.

Shapes outside both lists above are outside this reader's detection: another
block wrapper, a comment, or a drawing callout can still be dropped with no
limitation. An empty ``limitations`` therefore means "none of the listed shapes
was found", never "the source was read completely". Images carry no extractable
text and OCR is out of scope. Source bytes and stored profiles are untouched:
this module only reads.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.ingestion.file_validator import validate_docx_file

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_PARAGRAPH = f"{_W}p"
_TABLE = f"{_W}tbl"
_TEXT_BOX = f"{_W}txbxContent"
_INSERTED = f"{_W}ins"
_DELETED = f"{_W}del"
_CONTENT_CONTROL = f"{_W}sdt"
_CONTROL_CONTENT = f"{_W}sdtContent"
_FIELD = f"{_W}fldSimple"

#: Parents under which the body walk descends into a content control.
_READ_CONTROL_PARENTS = frozenset({f"{_W}body", _CONTROL_CONTENT})

LimitationKind = Literal[
    "nested_table", "text_box", "tracked_change", "content_control", "field"
]

_UNSUPPORTED_CONTENT_REVIEW: dict[LimitationKind, str] = {
    "nested_table": "a table inside a table cell is not read",
    "text_box": "text-box content is not read",
    "tracked_change": "tracked insertions/deletions are not resolved",
    "content_control": (
        "a content control inside a paragraph, table cell, header or footer is not read"
    ),
    "field": "a field's cached visible result is not read",
}


@dataclass(frozen=True)
class ExtractedParagraph:
    text: str


@dataclass(frozen=True)
class ExtractedTable:
    rows: list[list[str]]


@dataclass(frozen=True)
class ExtractedHeaderFooter:
    """Header/footer text and the section whose definition supplied it."""

    location: Literal["header", "footer"]
    section_index: int
    text: str


@dataclass(frozen=True)
class ExtractedContentLimitation:
    """Visible content this reader cannot read, attributed to where it sits."""

    kind: LimitationKind
    location: str
    count: int

    @property
    def message(self) -> str:
        return (
            f"{self.location}: {self.count} {self.kind.replace('_', ' ')} found; "
            f"{_UNSUPPORTED_CONTENT_REVIEW[self.kind]}. Review the source document "
            "before approving this profile."
        )


@dataclass(frozen=True)
class ExtractedDocxContent:
    source_path: Path
    blocks: list[ExtractedParagraph | ExtractedTable] = field(default_factory=list)
    header_footer: list[ExtractedHeaderFooter] = field(default_factory=list)
    limitations: list[ExtractedContentLimitation] = field(default_factory=list)

    @property
    def paragraphs(self) -> list[str]:
        return [block.text for block in self.blocks if isinstance(block, ExtractedParagraph)]

    @property
    def tables(self) -> list[ExtractedTable]:
        return [block for block in self.blocks if isinstance(block, ExtractedTable)]

    @property
    def plain_text(self) -> str:
        parts = [
            section.text for section in self.header_footer if section.location == "header"
        ]
        parts.extend(_block_text(block) for block in self.blocks)
        parts.extend(
            section.text for section in self.header_footer if section.location == "footer"
        )
        return "\n".join(parts).strip()


def read_docx(path: str | Path) -> ExtractedDocxContent:
    file_path = validate_docx_file(path)
    document = Document(str(file_path))

    blocks = _read_body_blocks(document.element.body, document)

    header_footer: list[ExtractedHeaderFooter] = []
    limitations = _find_limitations(document.element.body, "body")
    for section_index, section in enumerate(document.sections, start=1):
        for location, content in (
            ("header", section.header),
            ("footer", section.footer),
        ):
            if content.is_linked_to_previous:
                continue
            lines = [
                paragraph.text.strip()
                for paragraph in content.paragraphs
                if paragraph.text.strip()
            ]
            for table in content.tables:
                lines.extend(_table_lines(_read_table(table)))
            if lines:
                header_footer.append(
                    ExtractedHeaderFooter(
                        location=location,
                        section_index=section_index,
                        text="\n".join(lines),
                    )
                )
            limitations.extend(
                _find_limitations(
                    content.part.element, f"{location} of section {section_index}"
                )
            )

    return ExtractedDocxContent(
        source_path=file_path,
        blocks=blocks,
        header_footer=header_footer,
        limitations=limitations,
    )


def _read_body_blocks(
    container: BaseOxmlElement, document: DocumentObject
) -> list[ExtractedParagraph | ExtractedTable]:
    """Paragraphs and tables of one container, in authored order.

    Descends into a block-level content control so its paragraphs and tables keep
    the position the control occupies instead of vanishing.
    """
    blocks: list[ExtractedParagraph | ExtractedTable] = []
    for child in container.iterchildren(_PARAGRAPH, _TABLE, _CONTENT_CONTROL):
        if child.tag == _PARAGRAPH:
            text = Paragraph(child, document).text.strip()
            if text:
                blocks.append(ExtractedParagraph(text=text))
        elif child.tag == _TABLE:
            table = _read_table(Table(child, document))
            if table.rows:
                blocks.append(table)
        else:
            content = child.find(_CONTROL_CONTENT)
            if content is not None:
                blocks.extend(_read_body_blocks(content, document))
    return blocks


def _read_table(table: Table) -> ExtractedTable:
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        if any(cells):
            rows.append(cells)
    return ExtractedTable(rows=rows)


def _table_lines(table: ExtractedTable) -> list[str]:
    return [" | ".join(cell for cell in row if cell) for row in table.rows]


def _block_text(block: ExtractedParagraph | ExtractedTable) -> str:
    if isinstance(block, ExtractedParagraph):
        return block.text
    return "\n".join(_table_lines(block))


def _find_limitations(
    element: BaseOxmlElement, location: str
) -> list[ExtractedContentLimitation]:
    counts: dict[LimitationKind, int] = {
        "nested_table": sum(1 for table in element.iter(_TABLE) if _has_table_ancestor(table)),
        "text_box": len(element.findall(f".//{_TEXT_BOX}")),
        "tracked_change": len(element.findall(f".//{_INSERTED}"))
        + len(element.findall(f".//{_DELETED}")),
        "content_control": sum(1 for _ in _unread_content_controls(element)),
        "field": len(element.findall(f".//{_FIELD}")),
    }
    return [
        ExtractedContentLimitation(kind=kind, location=location, count=count)
        for kind, count in counts.items()
        if count
    ]


def _unread_content_controls(
    element: BaseOxmlElement,
) -> list[BaseOxmlElement]:
    """Content controls some visible text stays behind, matching ``_read_body_blocks``.

    A control whose parent is the body or another control's content is descended
    into and its text is read; one wrapping paragraph runs, or sitting in a table
    cell, header or footer, is not.
    """
    return [
        control
        for control in element.iter(_CONTENT_CONTROL)
        if control.getparent() is None
        or control.getparent().tag not in _READ_CONTROL_PARENTS
    ]


def _has_table_ancestor(element: BaseOxmlElement) -> bool:
    parent = element.getparent()
    while parent is not None:
        if parent.tag == _TABLE:
            return True
        parent = parent.getparent()
    return False
