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

- body paragraphs and top-level body tables;
- table rows and cells, read from the table's own XML in authored order:
  python-docx returns only direct ``w:tr``/``w:tc`` children, so a row or cell
  held by a content control or a structural wrapper (``w:customXml``,
  ``w:smartTag``) is read in the position it occupies instead of vanishing; a
  cell's block content then goes through the same block walk as the body, so a
  cell's block content control or wrapped paragraph is read where it sits; a
  table nested in a cell stays reported (see below);
- a block-level content control (``w:sdt``), whose paragraphs and tables are read
  in the position the control occupies, nested controls included;
- any other block wrapper whose own children are only paragraphs, tables or
  content controls (``w:customXml`` and friends), read in place recursively;
- inline text, by one walk that reads each child in the position it occupies:
  run text (python-docx's own rendering of tabs and breaks, so wrapped text keeps
  its spacing), runs held by a hyperlink or a pass-through wrapper
  (``w:customXml``, ``w:smartTag``, ``w:dir``, ``w:bdo``), and run text inside a
  selected alternative branch. Reading and reporting are one traversal, so a
  wrapper holding a wrapper is named where it is declined rather than read by one
  pass and audited by another;
- one branch of an inline markup-compatibility alternative
  (``mc:AlternateContent``): the ``mc:Choice`` whose ``Requires`` namespaces are
  all declared in the document, otherwise the ``mc:Fallback``. The branches are
  alternative renderings of the same content, so reading both would duplicate
  text and reading neither would drop it. The branch not chosen is not read and
  not reported, and no shape inside it is counted: it renders what was read;
- the same shapes inside a header/footer, read once per displayed variant.

What it reports instead of dropping (the whole limitation set, each naming its
location):

- a table inside a table cell, text-box content, and tracked insertions,
  deletions and moves (``w:ins``, ``w:del``, ``w:moveFrom``, ``w:moveTo``): a
  move is reported rather than read because Word keeps both the source and the
  destination copy under markup, so reading one would guess which is current;
- content held by a content control this reader does not descend into: an inline
  control inside a paragraph holds runs, and a control in a table position that
  does not hold the rows or cells it sits among cannot be read. Counted once per
  part, so a control in any position — body, cell, row, table, header or footer —
  is named instead of skipped;
- a block wrapper, or a table child, holding text in a shape that is not the
  blocks, rows or cells this reader handles — reported one per element with its
  name and child position;
- an inline element holding text that the inline walk does not read, at whatever
  depth it sits — reported one per element with its name and paragraph position,
  so a wrapper inside a pass-through wrapper, or inside the selected alternative
  branch, is named instead of disappearing;
- a ruby annotation (``w:r/w:ruby``): its base and annotation text sit in nested
  runs that python-docx's run text does not read, and guessing whether the base
  or the annotation is the text to keep would be a product decision;
- a simple field's cached visible result (``w:fldSimple``);
- an embedded alternate-format chunk (``w:altChunk``) or included subdocument
  (``w:subDoc``), whose text lives in another part entirely;
- images, drawings and charts, one entry per part with the count found: their
  text, if any, is pixels or an embedded chart or worksheet part, and OCR is out
  of scope, so the reader never supplies it. Reported only: not reading pixels is
  not lost text, so these do not block approval.

Inline content-control runs and ``w:fldSimple`` results are reported, not read:
reading them would need a second, hand-written paragraph-text parser beside
python-docx's own. Visible text the reader never supplied cannot be recovered by
a later check, so a passing source-coverage audit only proves that the text this
reader supplied reached the output. The audit reads the limitation list for the
same reason and reports ``incomplete`` rather than ``passed`` when the reader
left source content unread. Every inline element the walk declines is named from
the decision that declines it, so the read set and the reported set stay
complementary at any nesting depth.

Shapes outside both lists above are outside this reader's detection: comment
bodies live in ``comments.xml``, and OLE-object text lives in an embedded part,
so no ``w:t`` in the main document marks either. A chart is named through the
drawing that holds it, but the chart's own text lives in a chart part this
reader never opens. An empty ``limitations`` therefore means "none of the listed
shapes was found", never "the source was read completely". Source bytes and
stored profiles are untouched: this module only reads.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Sequence

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.text.paragraph import CT_P
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.parts.hdrftr import FooterPart, HeaderPart
from docx.section import Section as SectionObject

from app.ingestion.file_validator import validate_docx_file

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_PARAGRAPH = f"{_W}p"
_TABLE = f"{_W}tbl"
_ROW = f"{_W}tr"
_CELL = f"{_W}tc"
_RUN = f"{_W}r"
_HYPERLINK = f"{_W}hyperlink"
_TEXT = f"{_W}t"
_TEXT_BOX = f"{_W}txbxContent"
_INSERTED = f"{_W}ins"
_DELETED = f"{_W}del"
_MOVED_FROM = f"{_W}moveFrom"
_MOVED_TO = f"{_W}moveTo"
_ALT_CHUNK = f"{_W}altChunk"
_SUB_DOCUMENT = f"{_W}subDoc"
_DRAWING = f"{_W}drawing"
_PICTURE = f"{_W}pict"
_CONTENT_CONTROL = f"{_W}sdt"
_CONTROL_CONTENT = f"{_W}sdtContent"
_FIELD = f"{_W}fldSimple"
_RUBY = f"{_W}ruby"
_OBJECT = f"{_W}object"
_ALTERNATE_CONTENT = f"{_MC}AlternateContent"
_CHOICE = f"{_MC}Choice"
_FALLBACK = f"{_MC}Fallback"
_REQUIRES = f"{_MC}Requires"

#: Block children one container walk reads in place.
_READ_CHILD_TAGS = frozenset({_PARAGRAPH, _TABLE, _CONTENT_CONTROL})
#: Revision wrappers ``_find_limitations`` reports as ``tracked_change`` instead.
_TRACKED_WRAPPERS = frozenset({_INSERTED, _DELETED, _MOVED_FROM, _MOVED_TO})
#: Wrappers Word puts around runs, rows or cells without owning their content.
_STRUCTURAL_WRAPPERS = frozenset({f"{_W}customXml", f"{_W}smartTag"})
#: Inline containers whose children are read in the position they occupy.
_INLINE_TRANSPARENT = frozenset(
    {_RUN, _HYPERLINK, f"{_W}dir", f"{_W}bdo"}
) | _STRUCTURAL_WRAPPERS
#: Inline elements whose text a part-level count already names, so the inline
#: walk must skip them instead of naming the same loss twice.
_INLINE_NAMED_ELSEWHERE = _TRACKED_WRAPPERS | {
    _CONTENT_CONTROL,
    _FIELD,
    _DRAWING,
    _PICTURE,
    _OBJECT,
}
#: Run children python-docx renders as text; ``str()`` of each is that rendering.
_RUN_TEXT_TAGS = frozenset(
    {_TEXT, f"{_W}tab", f"{_W}br", f"{_W}cr", f"{_W}noBreakHyphen", f"{_W}ptab"}
)

LimitationKind = Literal[
    "nested_table",
    "text_box",
    "tracked_change",
    "content_control",
    "field",
    "alt_chunk",
    "unknown_block",
    "unknown_inline",
    "ruby",
    "visual_element",
]

_UNSUPPORTED_CONTENT_REVIEW: dict[LimitationKind, str] = {
    "nested_table": "a table inside a table cell is not read",
    "text_box": "text-box content is not read",
    "tracked_change": "tracked insertions, deletions and moves are not resolved",
    "content_control": (
        "content inside a content control this reader cannot read in place is not read"
    ),
    "field": "a field's cached visible result is not read",
    "alt_chunk": (
        "an embedded alternate-format chunk (w:altChunk or w:subDoc) is not read"
    ),
    "unknown_block": "a block element this reader does not recognize is not read",
    "unknown_inline": (
        "an inline element this reader does not recognize is not read"
    ),
    "ruby": "a ruby (furigana) annotation's base and annotation text is not read",
    "visual_element": "image, drawing and chart text is not read; OCR is out of scope",
}

#: Kinds where visible text is known to be lost, so approval and generation fail
#: closed. ``visual_element`` is reported only: its content is pixels or an
#: embedded chart part, the reader never claims to read it, and OCR is out of
#: scope, so refusing the document would reject ordinary resumes for a headshot.
_BLOCKING_KINDS: frozenset[LimitationKind] = frozenset(
    {
        "nested_table",
        "text_box",
        "tracked_change",
        "content_control",
        "field",
        "alt_chunk",
        "unknown_block",
        "unknown_inline",
        "ruby",
    }
)

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

    @property
    def blocks_source_review(self) -> bool:
        """True when this gap lost text, so approval must not proceed on it."""
        return self.kind in _BLOCKING_KINDS


def unread_content_messages(
    limitations: Sequence[ExtractedContentLimitation],
) -> tuple[list[str], list[str]]:
    """(every reported limitation, those where visible text was lost)."""
    return [limitation.message for limitation in limitations], [
        limitation.message for limitation in limitations if limitation.blocks_source_review
    ]


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


@dataclass
class _ReadAccount:
    """What one part's walks read, so the counts can name only what they left.

    ``controls`` holds every content control a walk descended, and ``discarded``
    every ``mc:AlternateContent`` branch that was not chosen. Both hold the
    elements themselves, not ``id()``: lxml hands out a fresh proxy for the same
    element once the old proxy is collected, so an id would stop matching.
    """

    controls: set[BaseOxmlElement] = field(default_factory=set)
    discarded: set[BaseOxmlElement] = field(default_factory=set)


def read_docx(path: str | Path) -> ExtractedDocxContent:
    file_path = validate_docx_file(path)
    document = Document(str(file_path))

    body = document.element.body
    account = _ReadAccount()
    blocks, block_limitations = _read_blocks(body, document, "body", account)
    limitations = _find_limitations(body, "body", account) + block_limitations

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
                    displayed.element, document, label, account
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
                    _find_limitations(displayed.element, label, account)
                    + part_limitations
                )

    return ExtractedDocxContent(
        source_path=file_path,
        blocks=blocks,
        header_footer=header_footer,
        limitations=_merge_limitations(limitations),
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
    container: BaseOxmlElement,
    document: DocumentObject,
    location: str,
    account: _ReadAccount,
    *,
    read_tables: bool = True,
) -> tuple[list[ExtractedParagraph | ExtractedTable], list[ExtractedContentLimitation]]:
    """Blocks of one container in authored order, plus what it cannot read.

    A child is read in place when it is a paragraph, a table, a content control,
    or a wrapper whose own children are only those shapes (``w:customXml`` and
    friends). A text-bearing child outside that set is named in a limitation
    instead of being skipped; a child carrying no text has nothing to lose.

    ``read_tables`` is off for a table cell: a nested table is reported rather
    than read, because flattening one into its parent cell loses its columns.
    Every control this walk descends is recorded in ``account``, and a control it
    does not is named by ``_find_limitations``.
    """
    blocks: list[ExtractedParagraph | ExtractedTable] = []
    limitations: list[ExtractedContentLimitation] = []
    for index, child in enumerate(container.iterchildren(), start=1):
        if child.tag == _PARAGRAPH:
            text, inline_limitations = _read_paragraph(
                child,
                f"{location} child {index}",
                document.element.nsmap,
                account,
            )
            limitations.extend(inline_limitations)
            if text:
                blocks.append(ExtractedParagraph(text=text))
        elif child.tag == _TABLE and read_tables:
            rows, table_limitations = _read_rows(child, document, location, account)
            limitations.extend(table_limitations)
            if rows:
                blocks.append(ExtractedTable(rows=rows))
        elif child.tag == _TABLE:
            # A nested table stays unread, so nothing inside it is reached either.
            continue
        elif child.tag in _TRACKED_WRAPPERS:
            continue
        elif child.tag == _CONTENT_CONTROL:
            content = child.find(_CONTROL_CONTENT)
            if content is not None:
                account.controls.add(child)
                sub_blocks, sub_limitations = _read_blocks(
                    content,
                    document,
                    f"{location} child {index}",
                    account,
                    read_tables=read_tables,
                )
                blocks.extend(sub_blocks)
                limitations.extend(sub_limitations)
        elif _is_block_wrapper(child):
            sub_blocks, sub_limitations = _read_blocks(
                child,
                document,
                f"{location} child {index}",
                account,
                read_tables=read_tables,
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
    return blocks, limitations


def _read_paragraph(
    paragraph: CT_P,
    location: str,
    namespaces: Mapping[str, str],
    account: _ReadAccount,
) -> tuple[str, list[ExtractedContentLimitation]]:
    """Text of one paragraph and every inline shape the walk declined to read.

    Reading and reporting are one traversal, so a child is read, descended, or
    named at the moment it is declined; a wrapper holding a wrapper cannot be read
    by one pass and audited by another.
    """
    pieces: list[str] = []
    limitations: list[ExtractedContentLimitation] = []
    _read_inline(paragraph, pieces, limitations, location, namespaces, account)
    return "".join(pieces).strip(), limitations


def _read_inline(
    container: BaseOxmlElement,
    pieces: list[str],
    limitations: list[ExtractedContentLimitation],
    location: str,
    namespaces: Mapping[str, str],
    account: _ReadAccount,
) -> None:
    """Append one inline container's text in authored order, naming what it declines.

    A child is read (text), descended (a run, a hyperlink, a structural wrapper),
    skipped because a part-level count already names its loss (revisions, fields,
    controls, drawings), reported (ruby, an element holding text this walk does
    not read), or passed over because it holds no text at all.
    """
    for child in container.iterchildren():
        tag = child.tag
        if tag in _RUN_TEXT_TAGS:
            pieces.append(str(child))
        elif tag == _ALTERNATE_CONTENT:
            branch = _live_branch(child, namespaces, account)
            if branch is not None:
                _read_inline(branch, pieces, limitations, location, namespaces, account)
        elif tag in _INLINE_TRANSPARENT:
            _read_inline(child, pieces, limitations, location, namespaces, account)
        elif tag in _INLINE_NAMED_ELSEWHERE:
            continue
        elif tag == _RUBY:
            limitations.append(
                ExtractedContentLimitation(kind="ruby", location=location, count=1)
            )
        elif child.find(f".//{_TEXT}") is not None:
            limitations.append(
                ExtractedContentLimitation(
                    kind="unknown_inline",
                    location=location,
                    element=_local_name(child),
                    count=1,
                )
            )


def _live_branch(
    alternative: BaseOxmlElement,
    namespaces: Mapping[str, str],
    account: _ReadAccount,
) -> BaseOxmlElement | None:
    """The one ``mc:AlternateContent`` branch this reader reads.

    ``mc:Choice`` and ``mc:Fallback`` are alternative renderings of the same
    content, so reading both duplicates it and reading neither drops it. Every
    branch not chosen is recorded in ``account``: the shapes inside it are another
    rendering of content that was read, so counting them would report text that is
    not missing.
    """
    branch = next(
        (
            choice
            for choice in alternative.findall(_CHOICE)
            if _requires_declared(choice, alternative, namespaces)
        ),
        alternative.find(_FALLBACK),
    )
    for candidate in alternative:
        if candidate is not branch and candidate.tag in {_CHOICE, _FALLBACK}:
            account.discarded.add(candidate)
    return branch


def _requires_declared(
    choice: BaseOxmlElement,
    element: BaseOxmlElement,
    namespaces: Mapping[str, str],
) -> bool:
    """True when every namespace prefix the choice requires is declared."""
    # Word writes ``mc:Requires``; hand-built markup often writes it unqualified.
    requires = choice.get(_REQUIRES) or choice.get("Requires")
    if not requires:
        return True
    return all(
        prefix in namespaces or prefix in element.nsmap for prefix in requires.split()
    )


def _is_block_wrapper(element: BaseOxmlElement) -> bool:
    """True when every child is a block shape this reader handles, or carries no text."""
    children = list(element.iterchildren())
    return bool(children) and all(
        child.tag in _READ_CHILD_TAGS or child.find(f".//{_TEXT}") is None
        for child in children
    )


def _local_name(element: BaseOxmlElement) -> str:
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else "element"


def _read_rows(
    table: BaseOxmlElement,
    document: DocumentObject,
    location: str,
    account: _ReadAccount,
) -> tuple[list[list[str]], list[ExtractedContentLimitation]]:
    """Rows of one table in authored order, whatever wraps them.

    ``Table.rows`` returns only direct ``w:tr`` children, so a row held by a
    content control or a structural wrapper disappeared without even being named.
    The table's own XML is walked instead; a child that holds text but is neither
    a row nor a wrapper this reader descends is reported with its name and
    position.
    """
    rows: list[list[str]] = []
    limitations: list[ExtractedContentLimitation] = []
    for index, child in enumerate(table.iterchildren(), start=1):
        if child.tag == _ROW:
            cells, cell_limitations = _read_cells(child, document, location, account)
            limitations.extend(cell_limitations)
            if any(cells):
                rows.append(cells)
        elif child.tag == _CONTENT_CONTROL or child.tag in _STRUCTURAL_WRAPPERS:
            holder = (
                child.find(_CONTROL_CONTENT)
                if child.tag == _CONTENT_CONTROL
                else child
            )
            if holder is None:
                continue
            if child.tag == _CONTENT_CONTROL:
                account.controls.add(child)
            sub_rows, sub_limitations = _read_rows(holder, document, location, account)
            rows.extend(sub_rows)
            limitations.extend(sub_limitations)
        elif child.find(f".//{_TEXT}") is not None:
            limitations.append(
                ExtractedContentLimitation(
                    kind="unknown_block",
                    location=f"{location} table child {index}",
                    element=_local_name(child),
                    count=1,
                )
            )
    return rows, limitations


def _read_cells(
    row: BaseOxmlElement,
    document: DocumentObject,
    location: str,
    account: _ReadAccount,
) -> tuple[list[str], list[ExtractedContentLimitation]]:
    """Cells of one row in authored order, reading each one through the block walk.

    ``Row.cells`` returns only direct ``w:tc`` children, so a cell held by a
    content control or a structural wrapper was skipped the same way a wrapped
    row was. A cell holds paragraphs and nested tables, so the block walk reads
    it: a cell-level content control or wrapped paragraph is read in place. Cell
    content keeps the container's location, so repeated gaps stay one entry per
    container.
    """
    cells: list[str] = []
    limitations: list[ExtractedContentLimitation] = []
    for index, child in enumerate(row.iterchildren(), start=1):
        if child.tag == _CELL:
            cell_blocks, cell_limitations = _read_blocks(
                child, document, location, account, read_tables=False
            )
            limitations.extend(cell_limitations)
            cells.append("\n".join(_block_text(block) for block in cell_blocks).strip())
        elif child.tag == _CONTENT_CONTROL or child.tag in _STRUCTURAL_WRAPPERS:
            holder = (
                child.find(_CONTROL_CONTENT)
                if child.tag == _CONTENT_CONTROL
                else child
            )
            if holder is None:
                continue
            if child.tag == _CONTENT_CONTROL:
                account.controls.add(child)
            sub_cells, sub_limitations = _read_cells(
                holder, document, location, account
            )
            cells.extend(sub_cells)
            limitations.extend(sub_limitations)
        elif child.find(f".//{_TEXT}") is not None:
            limitations.append(
                ExtractedContentLimitation(
                    kind="unknown_block",
                    location=f"{location} row child {index}",
                    element=_local_name(child),
                    count=1,
                )
            )
    return cells, limitations


def _table_lines(table: ExtractedTable) -> list[str]:
    return [" | ".join(cell for cell in row if cell) for row in table.rows]


def _block_text(block: ExtractedParagraph | ExtractedTable) -> str:
    if isinstance(block, ExtractedParagraph):
        return block.text
    return "\n".join(_table_lines(block))


def _counted(element: BaseOxmlElement, tag: str, account: _ReadAccount) -> int:
    """Elements with this tag that no walk read and no discarded branch holds."""
    return sum(
        1 for item in element.iter(tag) if not _inside(item, account.discarded)
    )


def _find_limitations(
    element: BaseOxmlElement,
    location: str,
    account: _ReadAccount,
) -> list[ExtractedContentLimitation]:
    """Visible shapes in one part that no walk read, counted by kind.

    ``content_control`` counts every control the walks did not descend, so a
    control in a position this reader cannot traverse — body, cell, row, table,
    header or footer — is named instead of disappearing. A control the walk did
    descend is read, and naming it again would be false alarm. A shape inside an
    alternative branch that was not chosen is not counted: the chosen branch
    renders the same content, so it is not missing.
    """
    counts: dict[LimitationKind, int] = {
        "nested_table": sum(
            1
            for table in element.iter(_TABLE)
            if _has_ancestor(table, _TABLE)
            and not _inside(table, account.discarded)
        ),
        "text_box": _counted(element, _TEXT_BOX, account),
        "tracked_change": sum(
            _counted(element, tag, account) for tag in _TRACKED_WRAPPERS
        ),
        "content_control": sum(
            1
            for control in element.iter(_CONTENT_CONTROL)
            if control not in account.controls
            and not _inside(control, account.discarded)
        ),
        "field": _counted(element, _FIELD, account),
        "alt_chunk": _counted(element, _ALT_CHUNK, account)
        + _counted(element, _SUB_DOCUMENT, account),
        "visual_element": _visual_element_count(element, account),
    }
    return [
        ExtractedContentLimitation(kind=kind, location=location, count=count)
        for kind, count in counts.items()
        if count
    ]


def _visual_element_count(element: BaseOxmlElement, account: _ReadAccount) -> int:
    """Images, drawings and charts in a part, counted once per visible element."""
    drawings = sum(
        1
        for item in element.iter(_DRAWING)
        if not _has_ancestor(item, _DRAWING)
        and not _inside(item, account.discarded)
    )
    legacy_pictures = sum(
        1
        for item in element.iter(_PICTURE)
        if not _has_ancestor(item, _PICTURE)
        and item.find(f".//{_TEXT_BOX}") is None
        and not _inside(item, account.discarded)
    )
    return drawings + legacy_pictures


def _merge_limitations(
    limitations: Sequence[ExtractedContentLimitation],
) -> list[ExtractedContentLimitation]:
    """One entry per kind, location and element, so a repeated gap stays one line."""
    merged: dict[tuple[str, str, str | None], ExtractedContentLimitation] = {}
    for limitation in limitations:
        key = (limitation.kind, limitation.location, limitation.element)
        previous = merged.get(key)
        merged[key] = ExtractedContentLimitation(
            kind=limitation.kind,
            location=limitation.location,
            element=limitation.element,
            count=limitation.count + (previous.count if previous else 0),
        )
    return list(merged.values())


def _inside(element: BaseOxmlElement, containers: set[BaseOxmlElement]) -> bool:
    """True when this element sits inside one of ``containers``."""
    parent = element.getparent()
    while parent is not None:
        if parent in containers:
            return True
        parent = parent.getparent()
    return False


def _has_ancestor(element: BaseOxmlElement, tag: str) -> bool:
    parent = element.getparent()
    while parent is not None:
        if parent.tag == tag:
            return True
        parent = parent.getparent()
    return False
