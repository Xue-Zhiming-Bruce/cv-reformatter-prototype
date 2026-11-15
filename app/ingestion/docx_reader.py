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
- a content control (``w:sdt``) in every position the walk reaches: its
  paragraphs and tables at block level, its rows and cells in a table, its runs
  inline, each read in the position the control occupies, nested controls
  included. A control is recorded as read when the walk descends it, so the
  part-level ``content_control`` count below names only the controls left
  unread;
- any other block wrapper whose own children are only paragraphs, tables or
  content controls (``w:customXml`` and friends), read in place recursively;
- an Office Math zone (``m:oMath``, ``m:oMathPara``), block or inline: its
  characters are the ``m:t`` nodes under it, read in that order. A math zone is
  visible source text in its own namespace, so a decision that asked for ``w:t``
  alone dropped an equation as "carries no text";
- a footnote or endnote a ``w:footnoteReference``/``w:endnoteReference`` points
  at, read from its own part (``footnotes.xml``, ``endnotes.xml``) and emitted
  after the body blocks: a note is printed at the foot of the page, so it joins
  the text after the body and ahead of the footer. Notes a part does not supply
  stay reported (see ``note`` below);
- inline text, by one walk with one explicit decision per element of the inline
  content model (``_INLINE_DECISIONS``): a character the element renders is
  appended (``w:t``, ``w:tab``/``w:ptab`` as a tab, ``w:br``/``w:cr`` as the break
  python-docx renders, ``w:noBreakHyphen`` as U+2011, ``w:softHyphen`` as U+00AD,
  ``w:sym`` as the code point its ``w:char`` names, a ruby's base text); a
  container whose content is read in place is descended (a run, a hyperlink, a
  pass-through wrapper such as ``w:customXml``/``w:smartTag``/``w:dir``/``w:bdo``,
  a ruby's base, the selected alternative branch, and further runs inside any of
  them); markup that holds no visible character is ignored. Reading and reporting
  are one traversal, so a wrapper holding a wrapper is named where it is declined
  rather than read by one pass and audited by another;
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
- content held by a content control no walk descends: a control whose content is
  none of the blocks, rows, cells or runs the walks place (for example one inside
  tracked change markup, which is itself reported). Counted once per part, so a
  control in any position — body, cell, row, table, header or footer — is named
  instead of skipped;
- a block wrapper, or a table child, holding text in a shape that is not the
  blocks, rows or cells this reader handles — reported one per element with its
  name and child position;
- an inline element the walk does not read, at whatever depth it sits — reported
  one per element with its name and paragraph position, so a wrapper inside a
  pass-through wrapper, or inside the selected alternative branch, is named
  instead of disappearing. That includes every element with a ``report`` decision
  in the table (date-field parts such as ``w:dayShort``, ``w:pgNum``, a stray
  ``w:delText``, ``w:contentPart``, an embedded ``w:object``) and every element
  outside the model, whether or not it happens to hold a ``w:t``: ``w:sym`` and
  ``w:softHyphen`` render characters without one, which is why the decision is a
  table rather than a test for ``w:t``. An element holds text when its subtree
  carries a character in any text namespace (``w:t``, ``m:t``, ``a:t``, and the
  character-bearing run content), so a wrapper around an equation is named
  instead of reading as empty;
- a ruby annotation (``w:ruby/w:rt``): the base text is read, and the smaller
  pronunciation printed above it is reported rather than guessed into the line;
- a simple field's cached visible result (``w:fldSimple``), reported and never
  read: the runs inside it are the value a field last computed, which a field
  update can replace, so the cached value is named instead of entering the
  candidate's source text;
- a referenced note this reader did not read: an id no note part supplies, or a
  comment body, which lives in ``comments.xml`` as a margin annotation rather
  than page text and is deliberately not mixed into the candidate's text. Counted
  once per part over the reference marks themselves, so the report is per
  document rather than a blind spot;
- an embedded alternate-format chunk (``w:altChunk``) or included subdocument
  (``w:subDoc``), whose text lives in another part entirely;
- images, drawings and charts, one entry per part with the count found: their
  text, if any, is pixels or an embedded chart or worksheet part, and OCR is out
  of scope, so the reader never supplies it. Reported only: not reading pixels is
  not lost text, so these do not block approval;
- a symbol character whose ``w:char`` is a private-use code point (Wingdings,
  Symbol and other font-specific code pages), one entry per part naming the font:
  its code point is in the text, but the glyph it draws is the font's, so the
  meaning is font-specific. Reported only, for the same reason as an image.

What the walk never treats as content (the property set):

- the properties of the containers it descends (``w:customXmlPr``,
  ``w:smartTagPr``, ``w:sdtPr``, ``w:sdtEndPr``, ``w:dirPr``, ``w:fldData``, and
  ``w:pPr``/``w:rPr``). They describe their container instead of being visible
  text, so reporting one would refuse a document whose text was read completely.
  They are skipped structurally — every property element of these containers ends
  in ``Pr`` — so a property element this reader has not named yet is never
  reported as content either, while an element the content model does not allow
  and that does not end in ``Pr`` still is.

``w:fldSimple`` results are reported, not read. Visible text the reader never
supplied cannot be recovered by a later check, so a passing source-coverage audit
only proves that the text this reader supplied reached the output. The audit
reads the limitation list for the same reason and reports ``incomplete`` rather
than ``passed`` when the reader left source content unread. Every inline element
the walk declines is named from the decision that declines it, so the read set
and the reported set stay complementary at any nesting depth.

Shapes outside both lists above are outside this reader's detection: settings
and style parts (``styles.xml``, ``numbering.xml``, a theme) name no visible text
of their own. A chart is named through the drawing that holds it, but the chart's
own text lives in a chart part this reader never opens, and the same is true of a
worksheet behind an embedded object. An empty ``limitations`` therefore means
"none of the listed shapes was found", never "the source was read completely".
Source bytes and stored profiles are untouched: this module only reads.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Sequence

from docx import Document
from docx.document import Document as DocumentObject
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import parse_xml
from docx.oxml.text.paragraph import CT_P
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.parts.hdrftr import FooterPart, HeaderPart
from docx.section import Section as SectionObject

from app.ingestion.file_validator import validate_docx_file

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
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
_MATH = f"{_M}oMath"
_MATH_PARAGRAPH = f"{_M}oMathPara"
_MATH_TEXT = f"{_M}t"
_RUBY_BASE = f"{_W}rubyBase"
_RUBY_TEXT = f"{_W}rt"
_OBJECT = f"{_W}object"
_TAB = f"{_W}tab"
_POSITIONED_TAB = f"{_W}ptab"
_BREAK = f"{_W}br"
_CARRIAGE_RETURN = f"{_W}cr"
_NO_BREAK_HYPHEN = f"{_W}noBreakHyphen"
_SOFT_HYPHEN = f"{_W}softHyphen"
_SYMBOL = f"{_W}sym"
_SYMBOL_CHARACTER = f"{_W}char"
_SYMBOL_FONT = f"{_W}font"
_PAGE_BREAK_MARK = f"{_W}lastRenderedPageBreak"
_ALTERNATE_CONTENT = f"{_MC}AlternateContent"
_CHOICE = f"{_MC}Choice"
_FALLBACK = f"{_MC}Fallback"
_REQUIRES = f"{_MC}Requires"

#: Local names of the elements that carry a visible character, in every text
#: namespace the format uses: wordprocessingml ``w:t``, Office Math ``m:t`` and
#: DrawingML ``a:t`` all carry text, so a decision about whether an element holds
#: text cannot ask for ``w:t`` alone. The rest are the character-bearing run
#: content this reader renders.
_TEXT_LOCAL_NAMES = frozenset(
    {"t", "delText", "sym", "tab", "ptab", "br", "cr", "noBreakHyphen", "softHyphen"}
)

#: Block children one container walk reads in place.
_READ_CHILD_TAGS = frozenset({_PARAGRAPH, _TABLE, _CONTENT_CONTROL})
#: Note parts and the marks that point at them, in the order they are read.
_NOTE_REFERENCES: tuple[tuple[str, str], ...] = (
    ("footnote", f"{_W}footnoteReference"),
    ("endnote", f"{_W}endnoteReference"),
    ("comment", f"{_W}commentReference"),
)
_NOTE_PARTS: tuple[tuple[str, str, str], ...] = (
    ("footnote", RT.FOOTNOTES, f"{_W}footnote"),
    ("endnote", RT.ENDNOTES, f"{_W}endnote"),
)

#: Revision wrappers ``_find_limitations`` reports as ``tracked_change`` instead.
_TRACKED_WRAPPERS = frozenset({_INSERTED, _DELETED, _MOVED_FROM, _MOVED_TO})
#: Wrappers Word puts around runs, rows or cells without owning their content.
_STRUCTURAL_WRAPPERS = frozenset({f"{_W}customXml", f"{_W}smartTag"})
#: Property children of the containers this walk descends: they describe the
#: container instead of being content, so the walk skips them. ``w:fldData`` is
#: listed by name because it is the property element whose tag has no ``Pr``
#: suffix; ``_is_property`` covers the rest by that suffix, so a property element
#: this list does not name yet is still never reported as unread content.
_CONTAINER_PROPERTIES = frozenset(
    {
        f"{_W}customXmlPr",
        f"{_W}smartTagPr",
        f"{_W}sdtPr",
        f"{_W}sdtEndPr",
        f"{_W}dirPr",
        f"{_W}rubyPr",
        f"{_W}fldData",
    }
)

#: The elements the inline content model allows in a paragraph or a run.
#: Enumerated from ECMA-376 (``EG_PContent`` and ``EG_RunInnerContent``) plus the
#: markup-compatibility alternative, so ``_INLINE_DECISIONS`` can be checked
#: against a model rather than against itself.
_INLINE_CONTENT_TAGS = frozenset(
    {
        # paragraph content
        _RUN,
        _HYPERLINK,
        _FIELD,
        _CONTENT_CONTROL,
        _ALT_CHUNK,
        _SUB_DOCUMENT,
        _ALTERNATE_CONTENT,
        f"{_W}pPr",
        # run content
        _TEXT,
        _TAB,
        _POSITIONED_TAB,
        _BREAK,
        _CARRIAGE_RETURN,
        _NO_BREAK_HYPHEN,
        _SOFT_HYPHEN,
        _SYMBOL,
        _PAGE_BREAK_MARK,
        _RUBY,
        _MATH,
        _MATH_PARAGRAPH,
        _MATH_TEXT,
        _CONTROL_CONTENT,
        _DRAWING,
        _PICTURE,
        _OBJECT,
        f"{_W}rPr",
        f"{_W}contentPart",
        f"{_W}delText",
        f"{_W}fldChar",
        f"{_W}instrText",
        f"{_W}delInstrText",
        f"{_W}dayShort",
        f"{_W}dayLong",
        f"{_W}monthShort",
        f"{_W}monthLong",
        f"{_W}yearShort",
        f"{_W}yearLong",
        f"{_W}pgNum",
        f"{_W}annotationRef",
        f"{_W}footnoteRef",
        f"{_W}endnoteRef",
        f"{_W}separator",
        f"{_W}continuationSeparator",
        f"{_W}footnoteReference",
        f"{_W}endnoteReference",
        f"{_W}commentReference",
        # typed wrappers and zero-width markup
        *_STRUCTURAL_WRAPPERS,
        f"{_W}dir",
        f"{_W}bdo",
        *_TRACKED_WRAPPERS,
        f"{_W}proofErr",
        f"{_W}bookmarkStart",
        f"{_W}bookmarkEnd",
        f"{_W}commentRangeStart",
        f"{_W}commentRangeEnd",
        f"{_W}moveFromRangeStart",
        f"{_W}moveFromRangeEnd",
        f"{_W}moveToRangeStart",
        f"{_W}moveToRangeEnd",
        f"{_W}customXmlInsRangeStart",
        f"{_W}customXmlInsRangeEnd",
        f"{_W}customXmlDelRangeStart",
        f"{_W}customXmlDelRangeEnd",
        f"{_W}customXmlMoveFromRangeStart",
        f"{_W}customXmlMoveFromRangeEnd",
        f"{_W}customXmlMoveToRangeStart",
        f"{_W}customXmlMoveToRangeEnd",
        f"{_W}permStart",
        f"{_W}permEnd",
        # properties of the containers above, which are never content
        *_CONTAINER_PROPERTIES,
    }
)

InlineDecision = Literal["render", "descend", "counted", "empty", "report"]

#: One explicit decision per inline content element, and the whole reason this is
#: a table: deciding by "does it happen to hold a ``w:t``" drops every element
#: that renders a character without one (``w:sym``, ``w:softHyphen``, date-field
#: parts). An element outside this table is reported, never dropped, so an
#: unanticipated shape costs a limitation instead of text.
_INLINE_DECISIONS: dict[str, InlineDecision] = {
    # Characters the element itself renders.
    _TEXT: "render",
    _TAB: "render",
    _POSITIONED_TAB: "render",
    _BREAK: "render",
    _CARRIAGE_RETURN: "render",
    _NO_BREAK_HYPHEN: "render",
    _SOFT_HYPHEN: "render",
    _SYMBOL: "render",
    _PAGE_BREAK_MARK: "render",
    # Containers whose own content the walk reads in place.
    _RUN: "descend",
    _HYPERLINK: "descend",
    _RUBY: "descend",
    _ALTERNATE_CONTENT: "descend",
    **{tag: "descend" for tag in _STRUCTURAL_WRAPPERS},
    f"{_W}dir": "descend",
    f"{_W}bdo": "descend",
    # Office Math carries its text in ``m:t`` runs, in any of these namespaces.
    _MATH: "descend",
    _MATH_PARAGRAPH: "descend",
    _MATH_TEXT: "render",
    # Controls and the content they hold are read in the position they occupy.
    _CONTENT_CONTROL: "descend",
    _CONTROL_CONTENT: "descend",
    # Content a part-level count already names, so the walk must not name it twice.
    _DRAWING: "counted",
    _PICTURE: "counted",
    _ALT_CHUNK: "counted",
    _SUB_DOCUMENT: "counted",
    _FIELD: "counted",
    **{tag: "counted" for tag in _TRACKED_WRAPPERS},
    # Markup and field structure with no visible character of its own.
    f"{_W}pPr": "empty",
    f"{_W}rPr": "empty",
    **{tag: "empty" for tag in _CONTAINER_PROPERTIES},
    f"{_W}proofErr": "empty",
    # a complex field's result is ordinary runs, so only its structure is here
    f"{_W}fldChar": "empty",
    f"{_W}instrText": "empty",
    f"{_W}delInstrText": "empty",
    f"{_W}bookmarkStart": "empty",
    f"{_W}bookmarkEnd": "empty",
    f"{_W}commentRangeStart": "empty",
    f"{_W}commentRangeEnd": "empty",
    f"{_W}moveFromRangeStart": "empty",
    f"{_W}moveFromRangeEnd": "empty",
    f"{_W}moveToRangeStart": "empty",
    f"{_W}moveToRangeEnd": "empty",
    f"{_W}customXmlInsRangeStart": "empty",
    f"{_W}customXmlInsRangeEnd": "empty",
    f"{_W}customXmlDelRangeStart": "empty",
    f"{_W}customXmlDelRangeEnd": "empty",
    f"{_W}customXmlMoveFromRangeStart": "empty",
    f"{_W}customXmlMoveFromRangeEnd": "empty",
    f"{_W}customXmlMoveToRangeStart": "empty",
    f"{_W}customXmlMoveToRangeEnd": "empty",
    f"{_W}permStart": "empty",
    f"{_W}permEnd": "empty",
    # Names of the note bodies this reader reads (see ``_read_notes``). The mark
    # carries no text itself; the part-level ``note`` count names the ones no
    # note part supplied, and a comment's, whose part is never opened.
    f"{_W}footnoteReference": "counted",
    f"{_W}endnoteReference": "counted",
    f"{_W}commentReference": "counted",
    f"{_W}annotationRef": "empty",
    f"{_W}footnoteRef": "empty",
    f"{_W}endnoteRef": "empty",
    f"{_W}separator": "empty",
    f"{_W}continuationSeparator": "empty",
    # Visible content this walk does not read: reported by name.
    f"{_W}dayShort": "report",
    f"{_W}dayLong": "report",
    f"{_W}monthShort": "report",
    f"{_W}monthLong": "report",
    f"{_W}yearShort": "report",
    f"{_W}yearLong": "report",
    f"{_W}pgNum": "report",
    f"{_W}delText": "report",
    f"{_W}contentPart": "report",
    _OBJECT: "report",  # an embedded object's own text lives in another part
}

#: Run-content characters rendered as a fixed string, python-docx's own mapping.
_RUN_CHARACTERS: dict[str, str] = {
    _TAB: "\t",
    _POSITIONED_TAB: "\t",
    _CARRIAGE_RETURN: "\n",
    _NO_BREAK_HYPHEN: "\u2011",
    _SOFT_HYPHEN: "\u00ad",
    _PAGE_BREAK_MARK: "",
}

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
    "symbol",
    "note",
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
    "ruby": "a ruby (furigana) pronunciation annotation is not read",
    "note": (
        "a referenced footnote, endnote or comment body in another part is not read"
    ),
    "symbol": (
        "a symbol character from a font's own code page is emitted as its code "
        "point; the glyph it draws is font-specific"
    ),
    "visual_element": "image, drawing and chart text is not read; OCR is out of scope",
}

#: Kinds where visible text is known to be lost, so approval and generation fail
#: closed. ``visual_element`` and ``symbol`` are reported only: the first is
#: pixels or an embedded chart part (OCR is out of scope), and the second is a
#: font-glyph code point that did reach the text with its meaning intact as far
#: as this reader can tell, so refusing the document would reject ordinary
#: resumes for a headshot or a Wingdings bullet.
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
        "note",
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
    ``notes`` holds the referenced notes that were read, so the count names the
    notes a part did not supply.
    """

    controls: set[BaseOxmlElement] = field(default_factory=set)
    discarded: set[BaseOxmlElement] = field(default_factory=set)
    #: ``(kind, id)`` of every referenced note whose text this walk read.
    notes: set[tuple[str, str]] = field(default_factory=set)


def read_docx(path: str | Path) -> ExtractedDocxContent:
    file_path = validate_docx_file(path)
    document = Document(str(file_path))

    body = document.element.body
    account = _ReadAccount()
    blocks, block_limitations = _read_blocks(body, document, "body", account)
    note_blocks, note_limitations = _read_notes(document, account)
    blocks.extend(note_blocks)
    limitations = (
        _find_limitations(body, "body", account)
        + block_limitations
        + note_limitations
    )

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


def _read_notes(
    document: DocumentObject, account: _ReadAccount
) -> tuple[list[ExtractedParagraph | ExtractedTable], list[ExtractedContentLimitation]]:
    """Read every referenced footnote and endnote, in reference order.

    A note is printed at the foot of the page, so its text joins the body blocks
    after the body and ahead of the footer: page order again, not an invented
    one. A note whose part is missing, whose id the part does not hold, or that
    lives in a part this reader does not open (a comment's) is left unread and
    stays counted by ``_find_limitations``, which reports one entry per part.
    """
    blocks: list[ExtractedParagraph | ExtractedTable] = []
    limitations: list[ExtractedContentLimitation] = []
    body = document.element.body
    for kind, relationship, item_tag in _NOTE_PARTS:
        references = _referenced_note_ids(body, f"{_W}{kind}Reference")
        if not references:
            continue
        notes = _note_items(document, relationship, item_tag)
        for note_id in references:
            note = notes.get(note_id)
            if note is None:
                continue
            label = f"{kind} {note_id}"
            note_blocks, note_block_limitations = _read_blocks(
                note, document, label, account
            )
            limitations.extend(
                _find_limitations(note, label, account) + note_block_limitations
            )
            blocks.extend(note_blocks)
            account.notes.add((kind, note_id))
    return blocks, limitations


def _referenced_note_ids(container: BaseOxmlElement, reference_tag: str) -> list[str]:
    """Ids of one note kind's references, in document order and without repeats."""
    ids: list[str] = []
    for reference in container.iter(reference_tag):
        note_id = reference.get(f"{_W}id")
        if note_id is not None and note_id not in ids:
            ids.append(note_id)
    return ids


def _note_items(
    document: DocumentObject, relationship: str, item_tag: str
) -> dict[str, BaseOxmlElement]:
    """One note part's notes by id, or nothing when the document has no such part."""
    try:
        part = document.part.part_related_by(relationship)
    except KeyError:
        return {}
    return {
        note_id: note
        for note in parse_xml(part.blob).iter(item_tag)
        if (note_id := note.get(f"{_W}id")) is not None
    }


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
        elif child.tag in (_MATH, _MATH_PARAGRAPH):
            text = _math_text(child).strip()
            if text:
                blocks.append(ExtractedParagraph(text=text))
        elif _carries_text(child):
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
    """Append one inline container's text in authored order, deciding every child.

    A paragraph, a run and a chosen alternative branch are all inline containers,
    and each child takes one exit from ``_INLINE_DECISIONS``: render a character,
    descend, leave to a part-level count, ignore markup that holds no visible
    character, or report it by name. A child missing from that table is reported,
    so an element nobody anticipated costs a limitation rather than its text.
    """
    for child in container.iterchildren():
        if _is_property(child):
            continue
        decision = _INLINE_DECISIONS.get(child.tag, "report")
        if decision == "render":
            _render_character(child, pieces, limitations, location)
        elif decision == "descend":
            if child.tag == _CONTENT_CONTROL:
                # Read in place, so the part-level count must not call it unread.
                account.controls.add(child)
            if child.tag in (_MATH, _MATH_PARAGRAPH):
                # A math zone's own tree is not run content: read the characters
                # its ``m:t`` nodes carry instead of walking ``m:r``/``m:f``.
                pieces.append(_math_text(child))
            elif child.tag == _RUBY:
                _read_ruby(child, pieces, limitations, location, namespaces, account)
            elif child.tag == _ALTERNATE_CONTENT:
                branch = _live_branch(child, namespaces, account)
                if branch is not None:
                    _read_inline(branch, pieces, limitations, location, namespaces, account)
            else:
                _read_inline(child, pieces, limitations, location, namespaces, account)
        elif decision == "report":
            limitations.append(
                ExtractedContentLimitation(
                    kind="unknown_inline",
                    location=location,
                    element=_local_name(child),
                    count=1,
                )
            )


def _render_character(
    element: BaseOxmlElement,
    pieces: list[str],
    limitations: list[ExtractedContentLimitation],
    location: str,
) -> None:
    """Append the character one run-content element renders.

    ``w:sym`` is not Unicode text: ``w:char`` is a slot in the named font's own
    code page, so a private-use code point is emitted and reported as a symbol
    whose visible glyph this reader cannot vouch for.
    """
    tag = element.tag
    if tag in (_TEXT, _MATH_TEXT):
        pieces.append(element.text or "")
    elif tag == _BREAK:
        # python-docx renders a text-wrapping break as a newline and a page or
        # column break as nothing.
        pieces.append(str(element))
    elif tag == _SYMBOL:
        character = _symbol_character(element)
        pieces.append(character)
        if _is_private_use(character):
            limitations.append(
                ExtractedContentLimitation(
                    kind="symbol",
                    location=location,
                    element=element.get(_SYMBOL_FONT),
                    count=1,
                )
            )
    else:
        pieces.append(_RUN_CHARACTERS[tag])


def _symbol_character(element: BaseOxmlElement) -> str:
    """The character a ``w:sym`` renders, decoded from its hexadecimal ``w:char``."""
    try:
        return chr(int(element.get(_SYMBOL_CHARACTER) or "", 16))
    except ValueError:
        return ""


def _is_private_use(character: str) -> bool:
    """True when a code point is a font glyph slot rather than Unicode text."""
    return bool(character) and "\ue000" <= character <= "\uf8ff"


def _is_property(element: BaseOxmlElement) -> bool:
    """True for a container's property child: metadata, never content.

    Reporting one would refuse a document whose visible text was read completely,
    so the walk skips them structurally rather than deciding about them as
    content. Every property element of these containers ends in ``Pr``, which
    also covers one this reader has not named yet.
    """
    return (
        element.tag in _CONTAINER_PROPERTIES
        or _local_name(element).endswith("Pr")
    )


def _math_text(element: BaseOxmlElement) -> str:
    """Characters of one Office Math zone, in the order its ``m:t`` nodes carry them."""
    return "".join(node.text or "" for node in element.iter(_MATH_TEXT))


def _carries_text(element: BaseOxmlElement) -> bool:
    """True when the element or a descendant carries a visible character.

    Namespace-agnostic on purpose: an ``m:oMath`` holds its text in ``m:t``, so a
    decision that asked for ``w:t`` alone dropped a whole math paragraph as
    "carries no text", exactly as it would for any future text namespace.
    """
    return any(
        isinstance(item.tag, str) and _local_name(item) in _TEXT_LOCAL_NAMES
        for item in element.iter()
    )


def _read_ruby(
    ruby: BaseOxmlElement,
    pieces: list[str],
    limitations: list[ExtractedContentLimitation],
    location: str,
    namespaces: Mapping[str, str],
    account: _ReadAccount,
) -> None:
    """Read a ruby's base text and report its pronunciation annotation.

    The base is the text shown in the line, so it is read; ``w:rt`` is the small
    annotation printed above it, which is visible content this reader keeps out of
    the line rather than guess where it belongs.
    """
    base = ruby.find(_RUBY_BASE)
    if base is not None:
        _read_inline(base, pieces, limitations, location, namespaces, account)
    if ruby.find(_RUBY_TEXT) is not None:
        limitations.append(
            ExtractedContentLimitation(kind="ruby", location=location, count=1)
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
        child.tag in _READ_CHILD_TAGS or not _carries_text(child)
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
        elif _carries_text(child):
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
        elif _carries_text(child):
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
        "note": sum(
            1
            for kind, reference_tag in _NOTE_REFERENCES
            for reference in element.iter(reference_tag)
            if (kind, reference.get(f"{_W}id")) not in account.notes
            and not _inside(reference, account.discarded)
        ),
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
