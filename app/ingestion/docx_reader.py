"""Read candidate DOCX text without losing source order or hiding source gaps.

Order and header policy (issue #49):

- Body content is read in authored order, so a table stays between the
  paragraphs around it: collecting every paragraph and every table separately
  and joining them paragraph-first moved table text to the end, which detaches a
  company/role row from the bullets it describes.
- Section headers and footers are read with their source location and variant
  (``ExtractedHeaderFooter``). Header text joins ``plain_text`` ahead of the body
  and footer text after it: that is page order, not an invented one. Variants
  read for one section come in default, first-page, even-page order.
- A header/footer is read once, in the section that displays it, in authored
  order (a table between two header paragraphs stays between them). Word uses the
  nearest definition at or before the using section, so a definition parked in an
  earlier section is read at the section that inherits and displays it; a header
  repeated on every page is a presentation fact rather than more source text, and
  emitting it again would duplicate candidate facts.
- The first-page variant is read only when the section displays it (``titlePg``)
  and the even-page variant only when the document enables it
  (``oddAndEvenPagesHeaderFooter``). Word keeps the text of a variant no page
  shows out of view, so reading it would invent facts; it is not reported either.

What this reader reads (the whole read set):

- body paragraphs and top-level body tables, and the paragraphs inside table
  cells;
- a block-level content control (``w:sdt``) at body level, whose paragraphs and
  tables are read in the position the control occupies, nested controls
  included;
- any other block wrapper whose own children are only paragraphs, tables or
  content controls (``w:customXml`` and friends), read in place recursively;
- runs held by a transparent inline wrapper (``w:customXml``, ``w:smartTag``,
  ``w:dir`` and the like): python-docx reads only direct runs and hyperlinks, so
  such a wrapper is unwrapped on a copy and its runs read where they sit;
- the same shapes inside a header/footer, read once per displayed variant.

What it reports instead of dropping (the whole limitation set, each naming its
location):

- a table inside a table cell, text-box content, and tracked insertions,
  deletions and moves (``w:ins``, ``w:del``, ``w:moveFrom``, ``w:moveTo``): a
  move is reported rather than read because Word keeps both the source and the
  destination copy under markup, so reading one would guess which is current;
- a content control inside a paragraph;
- a block wrapper holding text in a shape that is not paragraphs, tables or
  controls — reported one per element with its name and child position;
- a simple field's cached visible result (``w:fldSimple``);
- an embedded alternate-format chunk (``w:altChunk``), whose text lives in
  another part entirely.

Inline content-control runs and ``w:fldSimple`` results are reported, not read:
reading them would need a second, hand-written paragraph-text parser beside
python-docx's own. Visible text the reader never supplied cannot be recovered by
a later check, so a passing source-coverage audit only proves that the text this
reader supplied reached the output. The audit reads the limitation list for the
same reason and reports ``incomplete`` rather than ``passed`` when the reader
left source content unread.

Shapes outside both lists above are outside this reader's detection: comment
bodies live in ``comments.xml``, and chart, worksheet or OLE-object text lives in
an embedded part, so no ``w:t`` in the main document marks either. Images carry
no extractable text and OCR is out of scope. An empty ``limitations`` therefore
means "none of the listed shapes was found", never "the source was read
completely". Source bytes and stored profiles are untouched: this module only
reads.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.text.paragraph import CT_P
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.parts.hdrftr import FooterPart, HeaderPart
from docx.section import Section as SectionObject
from docx.table import Table, _Cell

from app.ingestion.file_validator import validate_docx_file

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_PARAGRAPH = f"{_W}p"
_TABLE = f"{_W}tbl"
_RUN = f"{_W}r"
_HYPERLINK = f"{_W}hyperlink"
_TEXT = f"{_W}t"
_TEXT_BOX = f"{_W}txbxContent"
_INSERTED = f"{_W}ins"
_DELETED = f"{_W}del"
_MOVED_FROM = f"{_W}moveFrom"
_MOVED_TO = f"{_W}moveTo"
_ALT_CHUNK = f"{_W}altChunk"
_CONTENT_CONTROL = f"{_W}sdt"
_CONTROL_CONTENT = f"{_W}sdtContent"
_FIELD = f"{_W}fldSimple"

#: Block children one container walk reads in place.
_READ_CHILD_TAGS = frozenset({_PARAGRAPH, _TABLE, _CONTENT_CONTROL})
#: Revision wrappers ``_find_limitations`` reports as ``tracked_change`` instead.
_TRACKED_WRAPPERS = frozenset({_INSERTED, _DELETED, _MOVED_FROM, _MOVED_TO})
#: Inline shapes whose text stays unread, so an inline wrapper holding one is skipped.
_REPORTED_INLINE_TAGS = _TRACKED_WRAPPERS | {_CONTENT_CONTROL, _FIELD}
#: Inline elements whose text python-docx already reads, still searched for wrappers.
_READ_INLINE_TAGS = frozenset({_RUN, _HYPERLINK})

LimitationKind = Literal[
    "nested_table",
    "text_box",
    "tracked_change",
    "content_control",
    "field",
    "alt_chunk",
    "unknown_block",
]

_UNSUPPORTED_CONTENT_REVIEW: dict[LimitationKind, str] = {
    "nested_table": "a table inside a table cell is not read",
    "text_box": "text-box content is not read",
    "tracked_change": "tracked insertions, deletions and moves are not resolved",
    "content_control": (
        "a content control inside a paragraph or table cell is not read"
    ),
    "field": "a field's cached visible result is not read",
    "alt_chunk": "an embedded alternate-format chunk (w:altChunk) is not read",
    "unknown_block": "a block element this reader does not recognize is not read",
}

HeaderVariant = Literal["default", "first_page", "even_page"]

#: Header/footer variant accessors per role, in the order they are read.
_VARIANT_ACCESSORS: dict[str, dict[HeaderVariant, str]] = {
    "header": {
        "default": "header",
        "first_page": "first_page_header",
        "even_page": "even_page_header",
    },
    "footer": {
        "default": "footer",
        "first_page": "first_page_footer",
        "even_page": "even_page_footer",
    },
}


@dataclass(frozen=True)
class ExtractedParagraph:
    text: str


@dataclass(frozen=True)
class ExtractedTable:
    rows: list[list[str]]


@dataclass(frozen=True)
class ExtractedHeaderFooter:
    """Header/footer text, the variant and section that supplied it."""

    location: Literal["header", "footer"]
    variant: HeaderVariant
    section_index: int
    text: str


@dataclass(frozen=True)
class ExtractedContentLimitation:
    """Visible content this reader cannot read, attributed to where it sits."""

    kind: LimitationKind
    location: str
    count: int
    #: Local name of the unread element, when one element caused the limitation.
    element: str | None = None

    @property
    def message(self) -> str:
        subject = (
            self.location if self.element is None else f"{self.location} ({self.element})"
        )
        return (
            f"{subject}: {self.count} {self.kind.replace('_', ' ')} found; "
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

    body = document.element.body
    blocks, block_limitations = _read_blocks(body, document, "body")
    limitations = _find_limitations(body, "body") + block_limitations

    header_footer: list[ExtractedHeaderFooter] = []
    read_parts: set[str] = set()
    sections = document.sections
    for section_index, section in enumerate(sections):
        for location in ("header", "footer"):
            for variant in _enabled_variants(document, section):
                displayed = _displayed_part(sections, section_index, location, variant)
                if displayed is None or str(displayed.partname) in read_parts:
                    continue
                read_parts.add(str(displayed.partname))
                label = f"{variant} {location} of section {section_index + 1}"
                part_blocks, part_limitations = _read_blocks(
                    displayed.element, document, label
                )
                text = "\n".join(_block_text(block) for block in part_blocks)
                if text:
                    header_footer.append(
                        ExtractedHeaderFooter(
                            location=location,
                            variant=variant,
                            section_index=section_index + 1,
                            text=text,
                        )
                    )
                limitations.extend(
                    _find_limitations(displayed.element, label) + part_limitations
                )

    return ExtractedDocxContent(
        source_path=file_path,
        blocks=blocks,
        header_footer=header_footer,
        limitations=limitations,
    )


def _enabled_variants(
    document: DocumentObject, section: SectionObject
) -> list[HeaderVariant]:
    """Header/footer variants Word actually shows for this section, in read order."""
    variants: list[HeaderVariant] = ["default"]
    if section.different_first_page_header_footer:
        variants.append("first_page")
    if document.settings.odd_and_even_pages_header_footer:
        variants.append("even_page")
    return variants


def _displayed_part(
    sections: Sequence[SectionObject],
    section_index: int,
    location: str,
    variant: HeaderVariant,
) -> HeaderPart | FooterPart | None:
    """Header/footer part Word shows for this section and variant, if any.

    Word uses the nearest definition at or before the using section, so a
    first-page definition parked in a section that does not enable it still
    reaches the pages of a later section that does.
    """
    accessor = _VARIANT_ACCESSORS[location][variant]
    for section in reversed(sections[: section_index + 1]):
        content = getattr(section, accessor)
        if not content.is_linked_to_previous:
            return content.part
    return None


def _read_blocks(
    container: BaseOxmlElement, document: DocumentObject, location: str
) -> tuple[list[ExtractedParagraph | ExtractedTable], list[ExtractedContentLimitation]]:
    """Blocks of one container in authored order, plus what it cannot read.

    A child is read in place when it is a paragraph, a table, a content control,
    or a wrapper whose own children are only those shapes (``w:customXml`` and
    friends). A text-bearing child outside that set is named in a limitation
    instead of being skipped; a child carrying no text has nothing to lose.
    """
    blocks: list[ExtractedParagraph | ExtractedTable] = []
    limitations: list[ExtractedContentLimitation] = []
    unread_controls = 0
    for index, child in enumerate(container.iterchildren(), start=1):
        if child.tag == _PARAGRAPH:
            # ``Paragraph.text`` reads runs, hyperlinks and the inline wrappers
            # ``_paragraph_text`` unwraps, but nothing else it is given.
            unread_controls += len(child.findall(f".//{_CONTENT_CONTROL}"))
            text = _paragraph_text(child).strip()
            if text:
                blocks.append(ExtractedParagraph(text=text))
        elif child.tag == _TABLE:
            # Cells are read as plain text; nothing inside them is descended into.
            unread_controls += len(child.findall(f".//{_CONTENT_CONTROL}"))
            table = _read_table(Table(child, document))
            if table.rows:
                blocks.append(table)
        elif child.tag in _TRACKED_WRAPPERS:
            continue
        elif child.tag == _CONTENT_CONTROL:
            content = child.find(_CONTROL_CONTENT)
            if content is not None:
                sub_blocks, sub_limitations = _read_blocks(
                    content, document, f"{location} child {index}"
                )
                blocks.extend(sub_blocks)
                limitations.extend(sub_limitations)
        elif _is_block_wrapper(child):
            sub_blocks, sub_limitations = _read_blocks(
                child, document, f"{location} child {index}"
            )
            blocks.extend(sub_blocks)
            limitations.extend(sub_limitations)
        elif child.find(f".//{_TEXT}") is not None:
            limitations.append(
                ExtractedContentLimitation(
                    kind="unknown_block",
                    location=f"{location} child {index}",
                    element=_local_name(child),
                    count=1,
                )
            )
    if unread_controls:
        limitations.insert(
            0,
            ExtractedContentLimitation(
                kind="content_control", location=location, count=unread_controls
            ),
        )
    return blocks, limitations


def _paragraph_text(paragraph: CT_P) -> str:
    """Text of one paragraph, including runs a transparent inline wrapper holds.

    ``CT_P.text`` joins only direct ``w:r``/``w:hyperlink`` children, so a
    ``w:customXml``, ``w:smartTag`` or similar structural wrapper around runs
    would otherwise vanish. A paragraph without one takes python-docx's own path
    unchanged; a wrapped one is unwrapped on a copy, so its runs read in the
    position they occupy. Content controls, fields and revisions are left alone:
    ``_find_limitations`` reports them instead.
    """
    if not _inline_wrappers(paragraph):
        return paragraph.text
    clone = deepcopy(paragraph)
    while wrappers := _inline_wrappers(clone):
        for wrapper in wrappers:
            for child in list(wrapper):
                wrapper.addprevious(child)
            wrapper.getparent().remove(wrapper)
    return clone.text


def _inline_wrappers(element: BaseOxmlElement) -> list[BaseOxmlElement]:
    """Transparent inline wrappers holding text python-docx would skip."""
    wrappers: list[BaseOxmlElement] = []
    for child in element.iterchildren():
        if child.tag in _REPORTED_INLINE_TAGS:
            continue
        if child.tag in _READ_INLINE_TAGS:
            wrappers.extend(_inline_wrappers(child))
        elif child.find(f".//{_TEXT}") is not None:
            wrappers.append(child)
        else:
            wrappers.extend(_inline_wrappers(child))
    return wrappers


def _is_block_wrapper(element: BaseOxmlElement) -> bool:
    """True when every child is a block shape this reader handles, or carries no text."""
    children = list(element.iterchildren())
    return bool(children) and all(
        child.tag in _READ_CHILD_TAGS or child.find(f".//{_TEXT}") is None
        for child in children
    )


def _local_name(element: BaseOxmlElement) -> str:
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else "element"


def _read_table(table: Table) -> ExtractedTable:
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [_cell_text(cell) for cell in row.cells]
        if any(cells):
            rows.append(cells)
    return ExtractedTable(rows=rows)


def _cell_text(cell: _Cell) -> str:
    return "\n".join(
        _paragraph_text(paragraph._p) for paragraph in cell.paragraphs
    ).strip()


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
        "tracked_change": sum(
            len(element.findall(f".//{tag}")) for tag in _TRACKED_WRAPPERS
        ),
        "field": len(element.findall(f".//{_FIELD}")),
        "alt_chunk": len(element.findall(f".//{_ALT_CHUNK}")),
    }
    return [
        ExtractedContentLimitation(kind=kind, location=location, count=count)
        for kind, count in counts.items()
        if count
    ]


def _has_table_ancestor(element: BaseOxmlElement) -> bool:
    parent = element.getparent()
    while parent is not None:
        if parent.tag == _TABLE:
            return True
        parent = parent.getparent()
    return False
