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
  cell's block content control or wrapped paragraph is read where it sits, and so
  is a table nested in the cell: its rows keep their columns, it is emitted in the
  cell that holds it rather than flattened into the parent row, and it is read
  once (issue #56);
- a content control (``w:sdt``) in every position the walk reaches: its
  paragraphs and tables at block level, its rows and cells in a table, its runs
  inline, each read in the position the control occupies, nested controls
  included. A control is recorded as read when the walk descends it, so the
  part-level ``content_control`` count below names only the controls left
  unread;
- any other block wrapper whose own children are only paragraphs, tables, content
  controls or further such wrappers (``w:customXml`` and friends), read in place
  recursively: a wrapper holding a wrapper is a block wrapper too, so
  ``w:customXml > w:smartTag > w:p`` is read where it sits instead of being
  declined by a test that only looked one level down (issue #56);
- the text of a text box (``w:txbxContent``) a paragraph anchors, read through the
  same block walk as the body, so a paragraph, table or wrapper inside the box is
  read by the same rules. A text box floats relative to its anchor: the package
  records where it is anchored, not where a viewer paints it, so the convention is
  that box text is emitted as blocks of its own directly after the anchoring
  paragraph, in the order the drawing's own selected markup holds its boxes. That
  is the one authored position the file records: it neither invents a page
  position nor splices box text into the middle of the anchoring line, and a
  paragraph holding two boxes reads neither twice, and a box another box's
  paragraph anchors is read where that inner paragraph sits rather than queued
  again at the outer anchor. A drawing whose text is
  DrawingML paint (``a:t``) is not read this way and stays reported as a
  ``text_box`` wherever that character sits — including inside a box body this
  walk read, and beside a box the same drawing or group shape holds: reading a
  body closes that body's own characters only, and reading paint as candidate
  text would need a character-level claim this reader does not make;
- "in place" is bounded: wrappers, content controls, table cells and text boxes
  each cost one level of ``_MAX_BLOCK_DEPTH``, and a descent past that bound is
  reported as a ``nesting`` limitation instead of being followed. A package can
  nest thousands of levels; the reader's bound, not the interpreter's recursion
  limit, decides where reading stops, so the same document always gets the same
  answer (issue #56);
- an Office Math zone (``m:oMath``, ``m:oMathPara``), block or inline: its
  children go through the same inline walk as a paragraph's, so ``m:t`` renders
  in order, the math constructs (``m:r``, ``m:f``, ``m:sSup`` and the rest of
  ``_MATH_CONTAINERS``) are descended, and a child outside that model is named
  rather than lost. One walk, not a collector that reads ``m:t`` and drops the
  rest: a math zone is visible source text in its own namespace, so a decision
  that asked for ``w:t`` alone dropped an equation as "carries no text";
- a footnote or endnote a ``w:footnoteReference``/``w:endnoteReference`` points
  at, read from its own part (``footnotes.xml``, ``endnotes.xml``) and emitted
  after the body blocks: a note is printed at the foot of the page, so it joins
  the text after the body and ahead of the footer. Only a reference the walk
  itself reaches is followed, so a note pointed at from an alternative branch
  that was not chosen prints nothing and is not read. Notes a part does not
  supply stay reported (see ``note`` below);
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

- a table inside a table cell, or a text box, that the walk did not reach — one
  past ``_MAX_BLOCK_DEPTH``, one inside content the walk declines — and tracked
  insertions, deletions and moves (``w:ins``, ``w:del``, ``w:moveFrom``,
  ``w:moveTo``): a move is reported rather than read because Word keeps both the
  source and the destination copy under markup, so reading one would guess which
  is current. A gap this reader reads keeps no entry: the entry names only what is
  still unread;
- content the depth bound declined (``nesting``): the container the walk stopped at
  is named, because text past ``_MAX_BLOCK_DEPTH`` is source text nobody read;
- content held by a content control no walk descends: a control whose content is
  none of the blocks, rows, cells or runs the walks place (for example one inside
  tracked change markup, which is itself reported). Counted once per part, so a
  control in any position — body, cell, row, table, header or footer — is named
  instead of skipped;
- a block wrapper, or a table child, holding text in a shape that is not the
  blocks, rows or cells this reader handles — reported one per element with its
  name and child position;
- an entity reference (``&name;``): the XML parser is configured never to
  expand one, so the node stands for text this reader cannot see. Counted once
  per part wherever it sits — block level, in a run, inside ``w:t`` — so a
  document whose text lives in a DTD entity blocks instead of passing as read;
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
- drawings that hold only art (a legacy picture, a raster image, a drawing with no
  text of its own), one entry per kind and document part with the count of drawings
  found — a drawing or picture another one holds is decided by the outermost one,
  so a shape is named once: OCR is out of scope, so the reader never supplies it.
  Reported only: art
  this reader does not interpret is not lost text, so these do not block approval.
  A drawing that carries a character is not one of them — its text is visible
  content nobody read, so it is counted as a text box and blocks, wherever the
  character sits: art never takes in a character — and neither is a
  drawing that points at another part: a chart, a diagram or an embedded object
  keeps its text in the part its relationship names, so every relationship target
  that is not an image is reported as an ``embedded_part`` and blocks, whether or
  not this reader knows that part's format. A vector image is XML, so it counts as
  art only when every element in it draws a shape this reader recognises: an SVG
  text element, and any construct added to the format later, block instead of
  passing, as does a part that does not parse or whose root is not SVG. A
  ``foreignObject`` blocks whatever it holds, empty or not, because it holds HTML
  this reader does not read; an entity reference blocks because the parser never
  expands it, so its text is content nobody saw. A ``style`` element is allowed and
  its rules are not read, so text a CSS ``content:`` rule paints is outside this
  reader's claim. Vector art is not read as a character stream at all, so an
  outline that spells a word is art here, not text. Relationship ids are resolved
  against the part that owns the drawing, and the branch selection the walk uses
  also decides which of a drawing's own ``mc:AlternateContent`` branches contributes
  text;
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
of their own. A chart, a diagram or an embedded object is named through the
drawing that holds it and blocks review: its text lives in the part the drawing
points at, which this reader never opens. An empty ``limitations`` therefore
means "none of the listed shapes was found", never "the source was read
completely".
Source bytes and stored profiles are untouched: this module only reads.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal, Mapping, Sequence

from docx import Document
from docx.document import Document as DocumentObject
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.parts.hdrftr import FooterPart, HeaderPart
from docx.section import Section as SectionObject
from lxml import etree

from app.ingestion.file_validator import validate_docx_file

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
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
#: Office Math's content model: every element a math zone nests a character in,
#: from ECMA-376 (``CT_OMathPara``, ``CT_OMath``, ``CT_OMathArg`` and the constructs
#: inside them). ``m:t`` is the only one that carries text; the rest are structure,
#: and a math child outside this set is reported by the same inline walk that
#: decides about run content.
_MATH_CONTAINERS = frozenset(
    f"{_M}{name}"
    for name in (
        "acc", "bar", "borderBox", "box", "d", "deg", "den", "e", "eqArr", "f",
        "fName", "func", "groupChr", "lim", "limLow", "limUpp", "m", "mr", "nary",
        "num", "oMath", "oMathPara", "phant", "r", "rad", "sPre", "sSub", "sSubSup",
        "sSup", "sub", "sup",
    )
)
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

#: The elements that own the characters they hold: a text-box body owns the text
#: the box carries, a drawing owns the text it paints. The nearest one above a
#: character decides who is answerable for it.
_TEXT_OWNERS = frozenset({_DRAWING, _PICTURE, _TEXT_BOX})
#: Local names of the markup that draws content this reader never calls text: a
#: DrawingML image fill, a VML image fill, and VML WordArt, whose characters live
#: in a ``string`` attribute no text node carries.
_ART_LOCAL_NAMES = frozenset({"blip", "imagedata", "textpath"})
#: Block children one container walk reads in place.
_READ_CHILD_TAGS = frozenset({_PARAGRAPH, _TABLE, _CONTENT_CONTROL})
#: The mark in the text -> the kind of note it points at. Only a mark the walk
#: itself reaches brings a note in: one in an alternative branch that was not
#: chosen is neither read nor counted.
_NOTE_REFERENCES: dict[str, str] = {
    f"{_W}footnoteReference": "footnote",
    f"{_W}endnoteReference": "endnote",
    f"{_W}commentReference": "comment",
}
#: The part each readable note kind lives in, by kind.
_NOTE_PARTS: dict[str, tuple[str, str]] = {
    "footnote": (RT.FOOTNOTES, f"{_W}footnote"),
    "endnote": (RT.ENDNOTES, f"{_W}endnote"),
}

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
        *_MATH_CONTAINERS,
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
    # Office Math: the walk descends the constructs and renders their ``m:t``.
    **{tag: "descend" for tag in _MATH_CONTAINERS},
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
    "nesting",
    "tracked_change",
    "content_control",
    "field",
    "alt_chunk",
    "unknown_block",
    "unknown_inline",
    "ruby",
    "symbol",
    "note",
    "embedded_part",
    "visual_element",
]

_UNSUPPORTED_CONTENT_REVIEW: dict[LimitationKind, str] = {
    "nested_table": "a table inside a table cell is not read",
    "text_box": "text-box content is not read",
    "nesting": "content nested deeper than this reader reads is not read",
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
        "an inline element this reader does not recognize is not read, and neither "
        "is text an entity reference would expand to"
    ),
    "ruby": "a ruby (furigana) pronunciation annotation is not read",
    "note": (
        "a referenced footnote, endnote or comment body in another part is not read"
    ),
    "embedded_part": (
        "text inside a part a drawing points at (a chart, diagram or embedded "
        "object) is not read"
    ),
    "symbol": (
        "a symbol character from a font's own code page is emitted as its code "
        "point; the glyph it draws is font-specific"
    ),
    "visual_element": (
        "art is not read as text, so a character drawn as art is missed; OCR is out "
        "of scope and a vector shape is not interpreted as characters"
    ),
}

#: Kinds where visible text is known to be lost, so approval and generation fail
#: closed. ``visual_element`` and ``symbol`` are reported only: the first is art
#: this reader does not interpret (OCR is out of scope), and the second is a
#: font-glyph code point that did reach the text with its meaning intact as far as
#: this reader can tell, so refusing the document would reject ordinary resumes for
#: a headshot or a Wingdings bullet.
_BLOCKING_KINDS: frozenset[LimitationKind] = frozenset(
    {
        "nested_table",
        "text_box",
        "nesting",
        "tracked_change",
        "content_control",
        "field",
        "alt_chunk",
        "unknown_block",
        "unknown_inline",
        "ruby",
        "note",
        "embedded_part",
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


#: How many block containers one descent may open — wrappers, content controls,
#: table cells and text boxes each cost one level. A package can nest thousands;
#: past this bound the walk reports what it declines instead of recursing until the
#: interpreter's own limit decides, which is neither deterministic nor recoverable.
_MAX_BLOCK_DEPTH = 24


@dataclass
class _ReadAccount:
    """What one part's walks read, so the counts can name only what they left.

    ``controls`` holds every content control a walk descended, and ``discarded``
    every ``mc:AlternateContent`` branch that was not chosen. Both hold the
    elements themselves, not ``id()``: lxml hands out a fresh proxy for the same
    element once the old proxy is collected, so an id would stop matching.
    ``referenced_notes`` holds every note reference the walk itself reached, in
    walk order, and ``notes`` the ones whose text it then read: reading from the
    walk's own references is what keeps a note in an alternative branch that was
    not chosen out of the text, and the difference names the notes a part did not
    supply.
    """

    controls: set[BaseOxmlElement] = field(default_factory=set)
    discarded: set[BaseOxmlElement] = field(default_factory=set)
    #: Tables read where they sat — a nested table among them — and ``w:txbxContent``
    #: bodies read, so the part-level counts name only what no walk reached.
    tables: set[BaseOxmlElement] = field(default_factory=set)
    text_boxes: set[BaseOxmlElement] = field(default_factory=set)
    #: ``(kind, id)`` of every note reference the walk reached, in walk order.
    referenced_notes: dict[tuple[str, str], None] = field(default_factory=dict)
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
        _find_limitations(
            body, "body", account, document.element.nsmap, document.part
        )
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
                    _find_limitations(
                        displayed.element,
                        label,
                        account,
                        document.element.nsmap,
                        displayed.part,
                    )
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
    """Read every note the walks referenced, in the order the walks reached them.

    The references are the walk's own, not a scan of the part: a note referenced
    only from an ``mc:AlternateContent`` branch that was not chosen renders no
    text on the selected branch, so it must not pull its own text into the
    candidate's. A note is printed at the foot of the page, so its text joins the
    body blocks after the body and ahead of the footer: page order again, not an
    invented one. A note whose part is missing, whose id the part does not hold,
    or that lives in a part this reader does not open (a comment's) is left unread
    and stays counted by ``_find_limitations``, which reports one entry per part.
    """
    blocks: list[ExtractedParagraph | ExtractedTable] = []
    limitations: list[ExtractedContentLimitation] = []
    notes_by_kind: dict[str, tuple[Part, dict[str, BaseOxmlElement]] | None] = {}
    for kind, note_id in list(account.referenced_notes):
        if kind not in _NOTE_PARTS:
            continue
        if kind not in notes_by_kind:
            relationship, item_tag = _NOTE_PARTS[kind]
            notes_by_kind[kind] = _note_items(document, relationship, item_tag)
        kind_part = notes_by_kind[kind]
        if kind_part is None:
            continue
        part, notes = kind_part
        note = notes.get(note_id)
        if note is None:
            continue
        label = f"{kind} {note_id}"
        note_blocks, note_block_limitations = _read_blocks(note, document, label, account)
        limitations.extend(
            _find_limitations(
                note, label, account, document.element.nsmap, part
            )
            + note_block_limitations
        )
        blocks.extend(note_blocks)
        account.notes.add((kind, note_id))
    return blocks, limitations


def _note_items(
    document: DocumentObject, relationship: str, item_tag: str
) -> tuple[Part, dict[str, BaseOxmlElement]] | None:
    """One note part and its notes by id, or nothing when the document has no part."""
    try:
        part = document.part.part_related_by(relationship)
    except KeyError:
        return None
    return part, {
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
    depth: int = 0,
) -> tuple[list[ExtractedParagraph | ExtractedTable], list[ExtractedContentLimitation]]:
    """Blocks of one container in authored order, plus what it cannot read.

    A child is read in place when it is a paragraph, a table, a content control,
    or a wrapper whose own children are only those shapes or further such wrappers
    (``w:customXml`` and friends). A text-bearing child outside that set is named
    in a limitation instead of being skipped; a child carrying no text has nothing
    to lose.

    A cell's block content comes back here too, so a table nested in a cell is read
    where it sits rather than flattened into its parent: its rows keep their
    columns and its text keeps the cell it belongs to. ``depth`` bounds that
    descent at ``_MAX_BLOCK_DEPTH``. Every control and text box this walk descends
    is recorded in ``account``, and a control it does not is named by
    ``_find_limitations``.
    """
    if depth > _MAX_BLOCK_DEPTH:
        return [], (
            [_nesting_limitation(location, container)] if _carries_text(container) else []
        )
    blocks: list[ExtractedParagraph | ExtractedTable] = []
    limitations: list[ExtractedContentLimitation] = []
    for index, child in enumerate(container.iterchildren(), start=1):
        child_location = f"{location} child {index}"
        if child.tag in (_PARAGRAPH, _MATH, _MATH_PARAGRAPH):
            text_boxes: list[BaseOxmlElement] = []
            text, inline_limitations = _read_inline_text(
                child,
                child_location,
                document.element.nsmap,
                account,
                text_boxes,
            )
            limitations.extend(inline_limitations)
            if text:
                blocks.append(ExtractedParagraph(text=text))
            box_blocks, box_limitations = _read_text_boxes(
                text_boxes, document, child_location, account, depth
            )
            blocks.extend(box_blocks)
            limitations.extend(box_limitations)
        elif child.tag == _TABLE:
            account.tables.add(child)
            rows, table_limitations = _read_rows(child, document, location, account, depth)
            limitations.extend(table_limitations)
            if rows:
                blocks.append(ExtractedTable(rows=rows))
        elif child.tag in _TRACKED_WRAPPERS:
            continue
        elif child.tag == _CONTENT_CONTROL:
            content = child.find(_CONTROL_CONTENT)
            if content is not None:
                account.controls.add(child)
                sub_blocks, sub_limitations = _read_blocks(
                    content, document, child_location, account, depth + 1
                )
                blocks.extend(sub_blocks)
                limitations.extend(sub_limitations)
        elif _is_block_wrapper(child):
            sub_blocks, sub_limitations = _read_blocks(
                child, document, child_location, account, depth + 1
            )
            blocks.extend(sub_blocks)
            limitations.extend(sub_limitations)
        elif _carries_text(child):
            limitations.append(
                ExtractedContentLimitation(
                    kind="unknown_block",
                    location=child_location,
                    element=_local_name(child),
                    count=1,
                )
            )
    return blocks, limitations


def _read_text_boxes(
    text_boxes: Sequence[BaseOxmlElement],
    document: DocumentObject,
    location: str,
    account: _ReadAccount,
    depth: int,
) -> tuple[list[ExtractedParagraph | ExtractedTable], list[ExtractedContentLimitation]]:
    """Text-box bodies a paragraph anchors, read after the paragraph itself.

    A text box floats relative to its anchor, so its page position is not in the
    package; the anchoring paragraph is the one authored position the file records.
    Reading the box as blocks of its own, directly after that paragraph, is the
    convention: it neither invents a page position nor splices box text into the
    middle of the anchoring line. Two boxes in one paragraph keep the order the
    drawing's own selected markup holds them.
    """
    blocks: list[ExtractedParagraph | ExtractedTable] = []
    limitations: list[ExtractedContentLimitation] = []
    for box in text_boxes:
        account.text_boxes.add(box)
        box_blocks, box_limitations = _read_blocks(
            box, document, f"{location} text box", account, depth + 1
        )
        blocks.extend(box_blocks)
        limitations.extend(box_limitations)
    return blocks, limitations


def _nesting_limitation(
    location: str, container: BaseOxmlElement
) -> ExtractedContentLimitation:
    """One descent this reader declined at ``_MAX_BLOCK_DEPTH``."""
    return ExtractedContentLimitation(
        kind="nesting",
        location=location,
        element=_local_name(container),
        count=1,
    )


def _read_inline_text(
    container: BaseOxmlElement,
    location: str,
    namespaces: Mapping[str, str],
    account: _ReadAccount,
    text_boxes: list[BaseOxmlElement],
) -> tuple[str, list[ExtractedContentLimitation]]:
    """Text of one inline container and every inline shape the walk declined to read.

    A paragraph and a block-level math zone are both inline containers, so both
    are read here: an office-math child the walk has not seen is named by the same
    decision table that handles run content, instead of a second collector that
    would read ``m:t`` and lose everything else.

    Reading and reporting are one traversal, so a child is read, descended, or
    named at the moment it is declined; a wrapper holding a wrapper cannot be read
    by one pass and audited by another.
    """
    pieces: list[str] = []
    limitations: list[ExtractedContentLimitation] = []
    _read_inline(
        container, pieces, limitations, location, namespaces, account, text_boxes
    )
    return "".join(pieces).strip(), limitations


def _read_inline(
    container: BaseOxmlElement,
    pieces: list[str],
    limitations: list[ExtractedContentLimitation],
    location: str,
    namespaces: Mapping[str, str],
    account: _ReadAccount,
    text_boxes: list[BaseOxmlElement],
) -> None:
    """Append one inline container's text in authored order, deciding every child.

    A paragraph, a run, a math construct and a chosen alternative branch are all
    inline containers, and each child takes one exit from ``_INLINE_DECISIONS``:
    render a character, descend, leave to a part-level count, ignore markup that
    holds no visible character, or report it by name. A child missing from that
    table is reported, so an element nobody anticipated costs a limitation rather
    than its text.
    """
    for child in container.iterchildren():
        if _is_entity(child):
            continue  # its expansion is unread text, counted once per part
        if _is_property(child):
            continue
        decision = _INLINE_DECISIONS.get(child.tag, "report")
        if decision == "render":
            _render_character(child, pieces, limitations, location)
        elif decision == "descend":
            if child.tag == _CONTENT_CONTROL:
                # Read in place, so the part-level count must not call it unread.
                account.controls.add(child)
            target = (
                _live_branch(child, namespaces, account)
                if child.tag == _ALTERNATE_CONTENT
                else child
            )
            if target is None:
                continue  # no branch to read: nothing renders to render again
            if child.tag == _RUBY:
                _read_ruby(child, pieces, limitations, location, namespaces, account, text_boxes)
            else:
                _read_inline(target, pieces, limitations, location, namespaces, account, text_boxes)
        elif decision == "counted":
            if child.tag in (_DRAWING, _PICTURE):
                # The drawing itself is left to the part-level count; the text boxes
                # it holds are ordinary blocks, collected for the block walk.
                text_boxes.extend(_drawing_text_boxes(child, namespaces, account))
            note_kind = _NOTE_REFERENCES.get(child.tag)
            if note_kind is not None:
                # Recorded where the walk reaches it, so only a reference the
                # selected branch renders can bring a note's text into the read.
                account.referenced_notes.setdefault(
                    (note_kind, child.get(f"{_W}id") or ""), None
                )
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
    text_boxes: list[BaseOxmlElement],
) -> None:
    """Read a ruby's base text and report its pronunciation annotation.

    The base is the text shown in the line, so it is read; ``w:rt`` is the small
    annotation printed above it, which is visible content this reader keeps out of
    the line rather than guess where it belongs.
    """
    base = ruby.find(_RUBY_BASE)
    if base is not None:
        _read_inline(base, pieces, limitations, location, namespaces, account, text_boxes)
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


def _drawing_text_boxes(
    drawing: BaseOxmlElement,
    namespaces: Mapping[str, str],
    account: _ReadAccount,
) -> list[BaseOxmlElement]:
    """The text-box bodies one drawing holds, in the order its selected markup has them.

    ``_selected_elements`` resolves the drawing's own ``mc:AlternateContent``, so a
    box in the branch that was not chosen is not read: it renders what the chosen
    branch renders.

    Only the boxes this drawing anchors are returned. A box anchored by another
    box's paragraph is read when that box body is walked, so queuing it here too
    would read its text twice: once at the outer anchor and once in its own place.
    """
    boxes = [
        item
        for item in _selected_elements(drawing, account, namespaces)
        if item.tag == _TEXT_BOX
    ]
    return [box for box in boxes if not _inside(box, set(boxes))]


def _is_block_wrapper(element: BaseOxmlElement, depth: int = 0) -> bool:
    """True when every child is a block shape this reader handles, or carries no text.

    A structural wrapper holding only further structural wrappers is a block wrapper
    too, so ``w:customXml > w:smartTag > w:p`` is read instead of being declined by a
    test that only looked one level down; the recursion is bounded by
    ``_MAX_BLOCK_DEPTH``, past which the wrapper is reported rather than read.
    """
    if depth > _MAX_BLOCK_DEPTH:
        return False
    children = list(element.iterchildren())
    return bool(children) and all(
        child.tag in _READ_CHILD_TAGS
        or (child.tag in _STRUCTURAL_WRAPPERS and _is_block_wrapper(child, depth + 1))
        or not _carries_text(child)
        for child in children
    )


def _local_name(element: BaseOxmlElement) -> str:
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else "element"


def _is_entity(node: BaseOxmlElement) -> bool:
    """True for an entity reference: markup whose expansion this parser never makes.

    python-docx parses with entity resolution off, so an entity stays a node and
    what it stands for never reaches the tree. A node standing for text nobody can
    read is unread text, so it blocks wherever it sits instead of passing as markup
    that draws nothing.
    """
    return node.tag is etree.Entity


def _namespace(element: BaseOxmlElement) -> str:
    tag = element.tag
    return tag[1:].split("}", 1)[0] if isinstance(tag, str) and tag.startswith("{") else ""


def _read_rows(
    table: BaseOxmlElement,
    document: DocumentObject,
    location: str,
    account: _ReadAccount,
    depth: int,
) -> tuple[list[list[str]], list[ExtractedContentLimitation]]:
    """Rows of one table in authored order, whatever wraps them.

    ``Table.rows`` returns only direct ``w:tr`` children, so a row held by a
    content control or a structural wrapper disappeared without even being named.
    The table's own XML is walked instead; a child that holds text but is neither
    a row nor a wrapper this reader descends is reported with its name and
    position, and ``depth`` bounds a chain of such wrappers.
    """
    if depth > _MAX_BLOCK_DEPTH:
        return [], (
            [_nesting_limitation(location, table)] if _carries_text(table) else []
        )
    rows: list[list[str]] = []
    limitations: list[ExtractedContentLimitation] = []
    for index, child in enumerate(table.iterchildren(), start=1):
        if child.tag == _ROW:
            cells, cell_limitations = _read_cells(
                child, document, location, account, depth
            )
            limitations.extend(cell_limitations)
            if any(cells):
                rows.append(cells)
        elif child.tag in (_CONTENT_CONTROL, *_STRUCTURAL_WRAPPERS):
            holder = (
                child.find(_CONTROL_CONTENT)
                if child.tag == _CONTENT_CONTROL
                else child
            )
            if holder is None:
                continue
            if child.tag == _CONTENT_CONTROL:
                account.controls.add(child)
            sub_rows, sub_limitations = _read_rows(
                holder, document, location, account, depth + 1
            )
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
    depth: int,
) -> tuple[list[str], list[ExtractedContentLimitation]]:
    """Cells of one row in authored order, reading each one through the block walk.

    ``Row.cells`` returns only direct ``w:tc`` children, so a cell held by a
    content control or a structural wrapper was skipped the same way a wrapped
    row was. A cell holds paragraphs and nested tables, so the block walk reads
    it: a cell-level content control or wrapped paragraph is read in place, and a
    table inside the cell keeps its rows, columns and parent-cell position.
    Cell content keeps the container's location, so repeated gaps stay one entry
    per container.
    """
    if depth > _MAX_BLOCK_DEPTH:
        return [], ([_nesting_limitation(location, row)] if _carries_text(row) else [])
    cells: list[str] = []
    limitations: list[ExtractedContentLimitation] = []
    for index, child in enumerate(row.iterchildren(), start=1):
        if child.tag == _CELL:
            cell_blocks, cell_limitations = _read_blocks(
                child, document, location, account, depth + 1
            )
            limitations.extend(cell_limitations)
            cells.append("\n".join(_block_text(block) for block in cell_blocks).strip())
        elif child.tag in (_CONTENT_CONTROL, *_STRUCTURAL_WRAPPERS):
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
                holder, document, location, account, depth + 1
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


def _counted(
    element: BaseOxmlElement,
    tag: str,
    account: _ReadAccount,
    read: frozenset[BaseOxmlElement] | set[BaseOxmlElement] = frozenset(),
) -> int:
    """Elements with this tag that no walk read and no discarded branch holds."""
    return sum(
        1
        for item in element.iter(tag)
        if item not in read and not _inside(item, account.discarded)
    )


def _find_limitations(
    element: BaseOxmlElement,
    location: str,
    account: _ReadAccount,
    namespaces: Mapping[str, str],
    owner: Part,
) -> list[ExtractedContentLimitation]:
    """Visible shapes in one part that no walk read, counted by kind.

    ``content_control`` counts every control the walks did not descend, so a
    control in a position this reader cannot traverse — body, cell, row, table,
    header or footer — is named instead of disappearing. A control the walk did
    descend is read, and naming it again would be false alarm. A nested table or a
    text box works the same way: ``account`` holds the ones a walk read, and only
    the rest are counted. A shape inside an
    alternative branch that was not chosen is not counted: the chosen branch
    renders the same content, so it is not missing.

    ``owner`` is the part the element belongs to, because a drawing can point at
    another part through a relationship id that only that part's relationships
    resolve. Drawings are decided first: selecting their branches registers the
    branches that render nothing, so the counts below skip what a discarded branch
    holds. Entity references are counted here rather than at each walk position: an
    entity node is text in whatever element holds it, block, run or ``w:t``, and one
    count over the part cannot miss a position a walk does not reach.
    """
    drawings = _drawing_limitation_counts(element, account, namespaces, owner)
    counts: dict[LimitationKind, int] = {
        # A table or a text box the walk read keeps no entry: the gap is closed, so
        # naming it again would refuse a document whose text was read.
        "nested_table": sum(
            1
            for table in element.iter(_TABLE)
            if _has_ancestor(table, _TABLE)
            and table not in account.tables
            and not _inside(table, account.discarded)
        ),
        "text_box": _counted(element, _TEXT_BOX, account, account.text_boxes)
        + drawings["text_box"],
        "tracked_change": sum(
            _counted(element, tag, account) for tag in _TRACKED_WRAPPERS
        ),
        "content_control": sum(
            1
            for control in element.iter(_CONTENT_CONTROL)
            if control not in account.controls
            and not _inside(control, account.discarded)
        ),
        "unknown_inline": sum(
            1
            for item in element.iter()
            if _is_entity(item) and not _inside(item, account.discarded)
        ),
        "field": _counted(element, _FIELD, account),
        "alt_chunk": _counted(element, _ALT_CHUNK, account)
        + _counted(element, _SUB_DOCUMENT, account),
        "note": sum(
            1
            for reference_tag, kind in _NOTE_REFERENCES.items()
            for reference in element.iter(reference_tag)
            if (kind, reference.get(f"{_W}id")) not in account.notes
            and not _inside(reference, account.discarded)
        ),
        "embedded_part": drawings["embedded_part"],
        "visual_element": drawings["visual_element"],
    }
    return [
        ExtractedContentLimitation(kind=kind, location=location, count=count)
        for kind, count in counts.items()
        if count
    ]


def _visual_elements(
    element: BaseOxmlElement, account: _ReadAccount
) -> list[BaseOxmlElement]:
    """Outermost drawings and legacy pictures in a part, minus discarded branches.

    A drawing or picture another one holds is not a gap of its own: the drawing
    around it decides about the characters and the parts inside it, so counting
    the inner one as well would name the same content twice.
    """
    found = [
        item
        for tag in (_DRAWING, _PICTURE)
        for item in element.iter(tag)
        if not _inside(item, account.discarded)
    ]
    owner = set(found)
    return [item for item in found if not _inside(item, owner)]


def _drawing_limitation_counts(
    element: BaseOxmlElement,
    account: _ReadAccount,
    namespaces: Mapping[str, str],
    owner: Part,
) -> dict[LimitationKind, int]:
    """Drawings in a part, one entry per outermost drawing by the one gap it is."""
    counts: dict[LimitationKind, int] = {
        "text_box": 0,
        "embedded_part": 0,
        "visual_element": 0,
    }
    for drawing in _visual_elements(element, account):
        kind = _drawing_limitation(drawing, account, namespaces, owner)
        if kind is not None:
            counts[kind] += 1
    return counts


def _drawing_limitation(
    drawing: BaseOxmlElement,
    account: _ReadAccount,
    namespaces: Mapping[str, str],
    owner: Part,
) -> LimitationKind | None:
    """The one gap a drawing is, decided on what it renders and what it points at.

    A drawing's own characters are text this reader did not read, and so is the
    text of any part it points at that is not an image: a chart, a diagram and an
    embedded object each keep their text in the part the relationship names, which
    this reader never opens. A relationship id the package cannot resolve, or one
    whose target is external, is not known to be pixels either, so both are
    reported the same way. A text box the drawing holds keeps its own text-box
    entry, so ``None`` sends that element to the text-box count instead of naming
    the same content twice — unless the drawing also draws art no box accounts
    for, which nothing else names: an image fill, VML WordArt's characters, or a
    nested drawing or picture with no box body below it.

    Art is decided in the same pass that decides the rest, because a nested drawing
    is not re-decided: every drawing inside this one is answered by the flags this
    scan collects, so the cost stays the size of the subtree instead of doubling
    per nesting level.

    A character belongs to the nearest drawing or ``w:txbxContent`` body above it
    (``_text_box_owns``). A body's own text is that box's business: the box walk
    reads it, and the text-box count names the body when no walk did. Every other
    character is paint — DrawingML ``a:t``, also when the drawing that paints it
    sits inside a box body the walk read — and no walk renders it, so the drawing
    keeps a blocking ``text_box`` entry for it.
    """
    has_text = has_text_box = unread_part = art = False
    boxed = {drawing}
    nested: list[BaseOxmlElement] = []
    for item in _selected_elements(drawing, account, namespaces):
        if item.tag == _TEXT_BOX:
            has_text_box = True
            _mark_boxed_drawings(item, boxed)
        elif _local_name(item) in _ART_LOCAL_NAMES:
            art = True
        elif _local_name(item) in _TEXT_LOCAL_NAMES and not _text_box_owns(item):
            has_text = True
        elif item.tag in (_DRAWING, _PICTURE):
            nested.append(item)
        for name, relationship_id in item.attrib.items():
            if name.startswith(_R) and _is_unread_target(owner, relationship_id):
                unread_part = True
    if unread_part:
        return "embedded_part"
    if has_text:
        return "text_box"
    if has_text_box and not (art or any(item not in boxed for item in nested)):
        return None
    return "visual_element"


def _mark_boxed_drawings(box: BaseOxmlElement, boxed: set[BaseOxmlElement]) -> None:
    """Add the drawings a text-box body below them accounts for.

    ``boxed`` holds the outermost drawing already, so walking up from a body to the
    first drawing in it also means every drawing above that one is in it: the walk
    stops there instead of climbing the whole nesting level by level.
    """
    parent = box.getparent()
    while parent is not None and parent not in boxed:
        if parent.tag in (_DRAWING, _PICTURE):
            boxed.add(parent)
        parent = parent.getparent()


def _text_box_owns(element: BaseOxmlElement) -> bool:
    """True when the nearest drawing or text-box body above it is a body.

    Suppression is keyed on that owner, never on "a box is somewhere above": a
    box body's own characters are the box's, while the paint of a drawing nested
    in a body the walk read is still text nobody read. A body no walk reached is
    counted by the text-box count whether or not it was read, so its text is named
    once either way.
    """
    parent = element.getparent()
    while parent is not None:
        if parent.tag in _TEXT_OWNERS:
            return parent.tag == _TEXT_BOX
        parent = parent.getparent()
    return False


def _is_unread_target(owner: Part, relationship_id: str) -> bool:
    """True when a drawn relationship points at a part this reader cannot call art.

    A chart, a diagram and an embedded object each keep text in the part the id
    names, and an id the package cannot resolve is not known to be art either, so
    both are text this reader did not read. An external target holds no text in the
    package at all, so it is not a gap. An image is art — except a vector one,
    which is XML and can hold markup that draws a character.
    """
    relationship = owner.rels.get(relationship_id)
    if relationship is None:
        return True
    if relationship.is_external:
        return False
    part = relationship.target_part
    if part.content_type != "image/svg+xml":
        return not part.content_type.startswith("image/")
    return _svg_holds_text(part.blob)


_SVG_NAMESPACE = "http://www.w3.org/2000/svg"

#: SVG elements that draw a shape, a shape's scaffolding or a filter, and nothing
#: else. A vector part is art only when every element in it is one of these: a
#: construct this reader does not know could draw a character, so not being here
#: blocks instead of passing. That keeps the report honest without maintaining a
#: list of text elements, which no list can complete.
_SVG_GRAPHICS_LOCAL_NAMES = frozenset(
    {
        "svg",
        "g",
        "defs",
        "use",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "image",
        "style",
        "switch",
        "symbol",
        "marker",
        "clipPath",
        "mask",
        "pattern",
        "linearGradient",
        "radialGradient",
        "stop",
        "filter",
        "feBlend",
        "feColorMatrix",
        "feComponentTransfer",
        "feComposite",
        "feConvolveMatrix",
        "feDiffuseLighting",
        "feDisplacementMap",
        "feDistantLight",
        "feDropShadow",
        "feFlood",
        "feFuncA",
        "feFuncB",
        "feFuncG",
        "feFuncR",
        "feGaussianBlur",
        "feImage",
        "feMerge",
        "feMergeNode",
        "feMorphology",
        "feOffset",
        "fePointLight",
        "feSpecularLighting",
        "feSpotLight",
        "feTile",
        "feTurbulence",
    }
)

#: SVG elements that describe an image. A viewer draws none of them.
_SVG_METADATA_LOCAL_NAMES = frozenset({"title", "desc", "metadata"})


def _svg_holds_text(blob: bytes) -> bool:
    """True unless every element of a vector part can be proven to draw a shape.

    Inverted on purpose: an allowlist of text elements cannot be exhaustive, so a
    construct this reader does not recognise must not pass as art. The part is art
    only when its root is an SVG ``svg`` and every element under it is a shape, a
    shape container or a filter primitive, with ``title``, ``desc`` and
    ``metadata`` allowed and ignored because nothing draws them. A ``foreignObject``
    holds HTML this reader does not read, so it blocks whatever it holds, empty or
    not: whether foreign markup paints a character is not a question character data
    can answer. An entity reference blocks too, because this parser never expands
    one, so what it stands for is text nobody read. A part that does not parse, or
    whose root is not SVG, is not known to be art either, so both are reported as
    text this reader did not read. A ``style`` element is allowed and its rules are
    not read: text a CSS ``content:`` rule paints is art this reader cannot deny,
    which is a limit of this reader rather than a claim about CSS.

    shortcut: vector art is not interpreted at all, so an outline that spells a word
    is art here rather than text, and so is a character drawn by a construct added
    to the format later — both are reported as art; upgrade when the product has to
    make a character-level claim about vector art.
    """
    try:
        root = parse_xml(blob)
    except (SyntaxError, ValueError):
        return True
    if root.tag != f"{{{_SVG_NAMESPACE}}}svg":
        return True
    return _svg_element_holds_text(root)


def _svg_element_holds_text(element: BaseOxmlElement) -> bool:
    """True when an SVG element or anything under it can draw a character."""
    for child in element.iterchildren():
        if _is_entity(child):
            return True  # an expansion nobody made may draw a character
        if not isinstance(child.tag, str):
            continue  # a comment or a processing instruction draws nothing
        name = _local_name(child)
        if _namespace(child) != _SVG_NAMESPACE:
            return True  # markup from outside SVG, where only SVG belongs
        if name in _SVG_METADATA_LOCAL_NAMES or name == "style":
            continue  # describes or styles the image; neither draws a character
        if name == "foreignObject":
            return True  # HTML this reader does not read, whatever it holds
        if name not in _SVG_GRAPHICS_LOCAL_NAMES:
            return True  # an element that may draw a character this reader missed
        if _svg_element_holds_text(child):
            return True
    return False


def _selected_elements(
    element: BaseOxmlElement,
    account: _ReadAccount,
    namespaces: Mapping[str, str],
) -> Iterator[BaseOxmlElement]:
    """Descendants the element renders, with its own alternative branches resolved.

    A drawing is decided as one element, so the selection the inline walk makes for
    block content has to happen here too: a branch that was not chosen renders
    nothing, so neither its text nor a part it points at may be counted.
    """
    for child in element.iterchildren():
        if not isinstance(child.tag, str) or child.tag in (_CHOICE, _FALLBACK):
            continue
        if child.tag == _ALTERNATE_CONTENT:
            branch = _live_branch(child, namespaces, account)
            if branch is not None:
                yield from _selected_elements(branch, account, namespaces)
            continue
        yield child
        yield from _selected_elements(child, account, namespaces)


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
