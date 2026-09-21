"""Pipeline E5 Lane B — constrained authored HTML/CSS template (experiment-only).

Owner-authorized E5 milestone (PIPELINE_E_PLAN.md §10 second strategy;
E5 work order). Not part of the active product architecture and not an
approved roadmap item. This module is called ONLY by the existing Pipeline E
runner (`e_pipeline.run_e5`); it is not a second runner and owns no
workflow of its own.

What it is: the smallest deterministic boundary around a LIVE Builder's
authored HTML/CSS template. The agent outputs a typed
`AuthoredTemplateCandidate` (HTML + CSS + declared typed slots); the shell
(this module) validates the safety boundary, fills the typed slots ONLY
from the reviewed candidate render context, renders through the pinned
Chrome path, and verifies candidate content accounting. The agent never
receives filesystem or shell write access and never touches candidate facts.

Slot vocabulary (declared, typed, shell-filled — no template engine):

- `{{candidate:name}}` / `{{candidate:contact}}` — header slots;
- `{{each:<collection>}} ... {{/each}}` — declared repeating collections
  (summary, experience, education, skills, languages, certifications,
  additional); item tokens inside: `{{item}}`, `{{item_head}}`,
  `{{item_detail}}`, `{{item_meta}}`, `{{item_bullets}}`.
- Missing optional content renders empty; no conditional syntax exists.

Presentation labels (owner decision 2026-09-21):

- `{{label:<label_id>}}` renders ONE owner-approved presentation label (the
  measured section heading). The id must be a member of the owner-approved
  subset of the shell-owned catalog passed to validation/fill; a `proposed`
  label (measured but not yet explicitly approved by the owner) is NOT
  renderable and fails closed. The TEXT always comes from the catalog,
  is HTML-escaped by the shell, and is inserted in a SECOND pass after every
  candidate slot is resolved — so a label value can never be re-parsed as a
  candidate slot, markup, or template syntax.
- The Builder can only REFERENCE ids: a fixed visible text node is rejected
  outright, even when its words are generic resume vocabulary.
- HONEST LIMIT: the direct visible-text channel is closed, but the authored
  HTML/CSS representation still carries inherent steganographic risk
  (encoding, spacing, attribute choices) that no string gate enumerates.

Hard safety boundary enforced by `validate_authored_template`:

- no JavaScript, no event handlers, no executable or embeddable content
  (script/iframe/object/embed/form/video/audio/canvas/base/applet);
- no remote or network-loaded URLs (http/https/protocol-relative), no
  `url(...)` CSS functions, no `@import`, no data: URLs, no src/href
  attributes at all (the template loads no external assets of any kind);
- no target-person literals and no candidate facts hardcoded into the
  HTML/CSS (candidate values enter ONLY through slot filling);
- NO comments in the reusable artifact (HTML `<!-- -->` and CSS `/* */`
  are rejected — a comment is free persistent text with no rendering
  value), and no CSS `content:` property (fixed visible text outside the
  typed slot channel);
- the reusable record carries NO free-text metadata at all: no rationale,
  no pagination prose, no region lists, no expected measurements, no slot
  descriptions. `evidence_refs` is the only metadata field, and it must
  contain ONLY evidence IDs the shell actually issued (membership is
  checked at validation time, not string shape).

  HONEST LIMIT: these closures remove the direct, persistent free-text
  fields. The authored HTML/CSS representation itself still carries
  inherent steganographic risk (encoding, spacing, attribute tricks, or
  presentation choices that no string gate enumerates); the shell does NOT
  claim the authored channel is theoretically closed — only that the known
  direct text channels are;
- every slot token in the HTML must resolve to a DECLARED slot category
  (header tokens to a declared header category; each-regions to a declared
  repeating category).

Candidate-value encoding (deterministic fill): every scalar CandidateDocument
text is HTML-escaped at fill time — candidate values are text nodes only,
never markup; `<li>`/`<br>`/container markup is generated exclusively by this
shell; values are substituted in ONE pass and are NEVER re-scanned, stripped
or rewritten afterwards — a candidate value carrying `{{...}}` therefore
survives the fill verbatim and fails closed at the residual-token check
(the original CandidateDocument is never modified).
"""

from __future__ import annotations

import html as _html_lib
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from tests.experiments.c2_candidates import CandidateDocument
from tests.experiments.e_pipeline import EvidenceModel, PresentationLabel

# ---------------------------------------------------------------------------
# Typed slot vocabulary
# ---------------------------------------------------------------------------

SIMPLE_SLOTS = ("candidate_name", "candidate_contact")
COLLECTIONS = (
    "summary", "experience", "education", "skills", "languages",
    "certifications", "additional",
)
ITEM_TOKENS = ("item", "item_head", "item_detail", "item_meta", "item_bullets", "item_text")

_EACH_RE = re.compile(r"\{\{each:([a-z_]+)\}\}(.*?)\{\{/each\}\}", re.DOTALL)
_TOKEN_RE = re.compile(r"\{\{([a-z_:]+)\}\}")
# Presentation-label marker: the ONLY channel for fixed visible template text.
# The label id is shell-issued (`e_pipeline._presentation_label_catalog`); the
# Builder may reference an id but can never submit the text it renders.
_LABEL_MARKER_RE = re.compile(r"\{\{label:([a-z0-9_.\-]+)\}\}")
_FORBIDDEN_PATTERNS = (
    ("<script", "javascript element"),
    ("javascript:", "javascript URL"),
    ("<iframe", "embeddable frame"),
    ("<object", "embeddable object"),
    ("<embed", "embeddable object"),
    ("<form", "form element"),
    ("<video", "media element"),
    ("<audio", "media element"),
    ("<canvas", "executable canvas"),
    ("<applet", "java applet"),
    ("<base", "URL rewriter"),
    ("<link", "external asset link"),
    ("@import", "CSS import"),
    ("data:", "data URL"),
    ("url(", "CSS url() function"),
    ("<!--", "html comment (free persistent text)"),
    ("/*", "css comment (free persistent text)"),
)
# CSS `content:` injects fixed visible text outside the typed slot channel;
# the lookbehind avoids false positives on `justify-content:`-style
# properties. Checked as a regex, not a substring.
_CSS_CONTENT_RE = re.compile(r"(?<![\w-])content\s*:", re.IGNORECASE)
_EVENT_HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
_REMOTE_URL_RE = re.compile(r"(?:https?:)?//[^\s\"'><)]+", re.IGNORECASE)
_ATTR_URL_RE = re.compile(r"\b(?:src|href)\s*=", re.IGNORECASE)
_EVAL_RE = re.compile(r"\b(?:eval|fetch|xmlhttprequest)\b", re.IGNORECASE)

# Evidence refs must be members of the SHELL-ISSUED evidence id vocabulary
# (`EvidenceStore._next_id` -> `ev.<kind>.<seq>`). The string shape below is
# only the first filter; validate_authored_template also requires every ref
# to be an id the shell actually recorded (`known_evidence_ids`).
_EVIDENCE_ID_RE = re.compile(
    r"^ev\.(page_overview|region_crop|adobe_element|local_measurement|coverage_audit)\.[0-9]+$"
)

# Generic resume presentation vocabulary. DIAGNOSTIC ONLY (owner decision
# 2026-09-21): it is used to decide which target words are 'person facts' for
# the heuristic literal diagnostic below. It is NOT an authorization for the
# Builder to write visible text — every fixed visible text node is now
# rejected by `validate_authored_template`, whether or not its words are in
# this list. Fixed visible text may only enter through `{{label:<id>}}` with a
# shell-issued id.
_TEMPLATE_GENERIC_VOCABULARY = {
    "summary", "profile", "experience", "education", "skills", "languages",
    "certifications", "certification", "projects", "achievements", "awards",
    "scholarships", "interests", "additional", "details", "references",
    "available", "upon", "request", "contact", "resume", "curriculum",
    "vitae", "university", "college", "institute", "school", "degree",
    "bachelor", "master", "science", "computer", "engineering", "business",
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december", "present",
    "united", "states", "city", "province", "phone", "email", "envelope",
    "github", "linkedin", "website", "portfolio", "location", "address",
    "name", "candidate", "section", "entry", "item", "content", "main",
    "header", "footer", "label", "rail", "column", "grid", "page", "body",
    "html", "class", "div", "span", "style", "font", "text", "title",
    "list", "item_head", "item_detail", "item_meta", "item_bullets",
    "padding", "margin", "width", "height", "display", "flex",
    "color", "size", "weight", "family", "sans", "serif",
    "border", "background", "line", "left", "right", "top", "bottom",
    "none", "solid", "important", "media", "print", "between", "justify",
    "space", "center", "align", "items", "wrap", "direction", "auto", "table",
    "cell", "bold", "italic", "letter", "spacing", "transform", "uppercase",
    "stretch", "gap", "rows", "value", "values", "generic",
    # Common generic resume section labels that may appear verbatim in a
    # target document (presentation labels, never person facts):
    "about", "conferences", "publications", "articles", "posters",
    "courses", "coursework", "hobbies", "volunteer", "volunteering",
    "internships", "objective", "strengths", "affiliations", "honors",
    "activities", "workshops", "talks", "accomplishments", "expertise",
    "proficiencies", "competencies", "technical", "personal",
    "professional", "academic", "career", "employment", "work", "history",
    "objective", "mission", "vision", "full", "name", "first", "last",
}


class AuthoredSlot(EvidenceModel):
    """One declared typed slot: what value category may be bound here. The
    declaration is ONLY category/repeating/required — no token, no free-text
    description: the HTML marker closure derives everything else from the
    category, and any extra string would be an unused person-fact channel."""

    category: Literal[
        "candidate_name", "candidate_contact", "summary", "experience",
        "education", "skills", "languages", "certifications", "additional",
    ]
    repeating: bool = False
    required: bool = False


class AuthoredTemplateCandidate(EvidenceModel):
    """A LIVE Builder's authored template candidate (E5 Lane B).

    The HTML carries only typed slot tokens; candidate values enter at
    render time through the shell's deterministic fill. The record carries
    NO free-text metadata: repeating/optional structure is expressed by the
    HTML each-regions and the slot declarations themselves, pagination is
    rendered and measured (never declared), and measurement explanations
    live in the run trace. `evidence_refs` is the single metadata field and
    must cite only shell-issued evidence ids."""

    template_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    html: str
    css: str = ""
    slots: list[AuthoredSlot]
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_refs_are_typed(self) -> "AuthoredTemplateCandidate":
        """First filter (string shape): only `ev.<kind>.<n>` ids pass.
        Membership in the shell's actually-issued ids is enforced by
        `validate_authored_template` (fail closed)."""
        bad_refs = [ref for ref in self.evidence_refs if not _EVIDENCE_ID_RE.match(ref)]
        if bad_refs:
            raise ValueError(
                "evidence_refs must be typed ev.<kind>.<n> evidence IDs "
                f"(page_overview|region_crop|adobe_element|local_measurement|"
                f"coverage_audit): {bad_refs[:3]}"
            )
        return self


# ---------------------------------------------------------------------------
# Safety validation (deterministic shell boundary; no sanitizer dependency)
# ---------------------------------------------------------------------------


def _norm_text(value: str) -> str:
    """Whitespace-normalized casefold used for literal presence checks."""
    return re.sub(r"\s+", " ", value).strip().casefold()


def _occurrences(haystack: str, needle: str) -> int:
    """Word-boundary-aware occurrence count (a leaf whose value is a prefix
    of a longer value is not double-counted)."""
    if not needle:
        return 0
    return len(re.findall(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack))


def _label_index(labels: list[PresentationLabel] | None) -> dict[str, PresentationLabel]:
    """Shell-approved label id -> entry. A `proposed` entry is NOT renderable
    (only an explicit owner approval makes a label usable), and a duplicated
    id is ambiguous (two texts for one marker). Both fail closed."""
    index: dict[str, PresentationLabel] = {}
    for label in labels or []:
        if label.status != "approved":
            raise ValueError(
                f"presentation label {label.label_id!r} is not owner-approved "
                "(proposed labels are not renderable)"
            )
        if label.label_id in index:
            raise ValueError(
                f"ambiguous presentation label id in the catalog: {label.label_id!r}"
            )
        index[label.label_id] = label
    return index


def presentation_label_markers(html: str) -> list[str]:
    """The presentation-label ids one template references (normalized
    spellings), in document order — the shell records which labels a lane
    actually referenced."""
    return _LABEL_MARKER_RE.findall(_normalize_slot_markers(html))


def _target_person_words(target_pdf: Path) -> set[str]:
    """Distinctive words of the TARGET document (>=4 letters) minus the
    generic template vocabulary. These are presentation-diagnostic evidence
    only; none of them may become fixed template text."""
    from app.ingestion.pdf_reader import read_pdf_text

    words = re.findall(r"[A-Za-z]{4,}", read_pdf_text(target_pdf))
    return {word.casefold() for word in words} - _TEMPLATE_GENERIC_VOCABULARY


def validate_authored_template(
    candidate: AuthoredTemplateCandidate,
    *,
    target_pdf: Path,
    render_candidate: CandidateDocument | None = None,
    known_evidence_ids: set[str] | None = None,
    labels: list[PresentationLabel] | None = None,
) -> dict[str, Any]:
    """Enforce the full Lane B safety boundary BEFORE anything is written or
    rendered. Raises ``ValueError`` with every violation; returns the
    validation report on success. Unknown fields fail at model construction
    (pydantic ``extra="forbid"``).

    `known_evidence_ids`: the evidence ids the shell actually issued. Every
    `evidence_refs` entry must be a MEMBER of that set — a string that only
    LOOKS like `ev.<kind>.<n>` is rejected. When no known-id set is
    provided, any non-empty evidence_refs fails closed.

    `labels`: the SHELL-OWNED presentation-label catalog. A `{{label:<id>}}`
    marker must name a catalog member (an unlisted id fails closed), and the
    direct fixed-visible-text channel is closed: after removing slot markers
    and label markers, no visible text node may carry an alphanumeric
    character. Tag/class/attribute names and CSS are not visible text and are
    unaffected."""
    from tests.experiments.c2_renderer import _html_text

    normalized_html = _normalize_slot_markers(candidate.html)
    payload = normalized_html + "\n" + candidate.css
    label_index = _label_index(labels)
    violations: list[str] = []
    unknown_markers = sorted(
        {
            marker
            for marker in _LABEL_MARKER_RE.findall(normalized_html)
            if marker not in label_index
        }
    )
    if unknown_markers:
        violations.append(
            "unknown presentation label marker (the shell issues label ids): "
            + ", ".join(repr(marker) for marker in unknown_markers[:6])
        )
    # Direct fixed-visible-text closure: strip the declared slot markers and
    # the presentation-label markers, then the remaining VISIBLE text must be
    # whitespace/punctuation only. A rejected word cannot be "authorized" by
    # the generic vocabulary — only a shell-issued label id can carry text.
    marker_free_html = _TOKEN_RE.sub(
        " ", _LABEL_MARKER_RE.sub(" ", normalized_html)
    )
    marker_free_html = re.sub(r"\{\{\s*/\s*each\s*\}\}", " ", marker_free_html)
    visible_text, _empty_sections = _html_text(marker_free_html)
    if any(character.isalnum() for character in visible_text):
        violations.append(
            "fixed visible text outside the presentation-label channel: "
            f"{visible_text[:120]!r} (use {{{{label:<label_id>}}}} with a "
            "shell-issued label id)"
        )
    for pattern, label in _FORBIDDEN_PATTERNS:
        if pattern.casefold() in payload.casefold():
            violations.append(f"forbidden content: {label} ({pattern!r})")
    if _EVENT_HANDLER_RE.search(payload):
        violations.append("forbidden content: inline event handler")
    if _REMOTE_URL_RE.search(payload):
        violations.append("forbidden content: remote/network URL")
    if _ATTR_URL_RE.search(payload):
        violations.append("forbidden content: src/href asset reference")
    if _EVAL_RE.search(payload):
        violations.append("forbidden content: executable/network API reference")
    if _CSS_CONTENT_RE.search(payload):
        violations.append(
            "forbidden content: css content property (fixed visible text "
            "outside the slot channel)"
        )

    # Slot declaration closure: tokens OUTSIDE each-regions must be the two
    # header slots (declared by category); tokens INSIDE a region must be
    # standard item tokens; every each-region names a declared collection.
    declared_categories = {slot.category for slot in candidate.slots}
    regions_html = "".join(
        match.group(2) for match in _EACH_RE.finditer(normalized_html)
    )
    outside_html = _EACH_RE.sub("", normalized_html)
    allowed_outside = {"candidate:name", "candidate:contact"}
    for token in sorted(set(_TOKEN_RE.findall(outside_html))):
        if token.replace("_", ":") not in allowed_outside and token not in allowed_outside:
            violations.append(f"undeclared slot token: {token!r}")
        elif token.replace("_", ":") == "candidate:name" and "candidate_name" not in declared_categories:
            violations.append(f"slot token {token!r} has no declared slot")
        elif token.replace("_", ":") == "candidate:contact" and "candidate_contact" not in declared_categories:
            violations.append(f"slot token {token!r} has no declared slot")
    inside_tokens = set(_TOKEN_RE.findall(regions_html))
    for token in sorted(inside_tokens - set(ITEM_TOKENS)):
        violations.append(f"non-standard item token inside a region: {token!r}")
    opened = re.findall(r"\{\{each:([a-z_]+)\}\}", normalized_html)
    closed = re.findall(r"\{\{/each\}\}", normalized_html)
    if len(opened) != len(closed):
        violations.append("unbalanced each-region markers")
    for name in opened:
        if name not in COLLECTIONS:
            violations.append(f"unknown collection region: {name!r}")
        elif name not in declared_categories:
            violations.append(f"each-region {name!r} has no declared slot")
    declared = {slot.category for slot in candidate.slots}

    # Evidence refs must cite SHELL-ISSUED evidence ids (membership, not
    # string shape). Without a known-id set, any ref fails closed.
    if candidate.evidence_refs:
        if not known_evidence_ids:
            violations.append(
                "evidence_refs provided but the shell issued no known evidence ids "
                f"(fail closed): {candidate.evidence_refs[:3]}"
            )
        else:
            unknown_refs = [
                ref for ref in candidate.evidence_refs if ref not in known_evidence_ids
            ]
            if unknown_refs:
                violations.append(
                    "evidence_refs cite ids the shell never issued: "
                    + ", ".join(unknown_refs[:6])
                )

    # Target-person literal check: DIAGNOSTIC ONLY (the fixed-visible-text
    # closure above already rejects every literal text node; this list only
    # names which target words a rejected template used).
    target_words = _target_person_words(target_pdf)
    metadata_text = " ".join(candidate.evidence_refs)
    static_words = {
        word.casefold()
        for word in re.findall(
            r"[A-Za-z]{5,}", candidate.html + " " + candidate.css + " " + metadata_text
        )
        if word.casefold() not in _TEMPLATE_GENERIC_VOCABULARY
    }
    contaminated = sorted(static_words & target_words)
    if contaminated:
        violations.append(
            "target-person literals in fixed template text: " + ", ".join(contaminated[:8])
        )

    # Candidate facts may not be hardcoded: no candidate leaf text may appear
    # in the pre-fill template (the same check covers the restricted metadata
    # fields — a typed ID cannot smuggle candidate values, but the gate stays
    # as a second layer).
    if render_candidate is not None:
        payload_norm = _norm_text(
            candidate.html + " " + candidate.css + " " + metadata_text
        )
        hardcoded = [
            leaf.leaf_id
            for leaf in render_candidate.leaves
            if leaf.text and len(_norm_text(leaf.text)) >= 8 and _norm_text(leaf.text) in payload_norm
        ]
        if hardcoded:
            violations.append(
                "candidate facts hardcoded into the template: " + ", ".join(hardcoded[:8])
            )

    if violations:
        raise ValueError("authored template rejected: " + " | ".join(violations))
    return {
        "passed": True,
        "declared_slots": sorted(declared),
        "collections_used": sorted(set(opened)),
        "presentation_labels": sorted(label_index),
        "checks": [
            "no_javascript", "no_event_handlers", "no_remote_urls", "no_css_imports",
            "no_data_urls", "no_unsafe_elements", "no_target_person_literals",
            "no_candidate_facts", "declared_slots_only",
            "no_fixed_visible_text", "known_presentation_labels_only",
        ],
    }


# ---------------------------------------------------------------------------
# Deterministic slot filling from the reviewed candidate render context
# ---------------------------------------------------------------------------


def _leaf_text_of(leaf: Any) -> str:
    return (leaf.text or "").strip()


def _esc(value: str | None) -> str:
    """HTML-escape one scalar candidate value (output encoding only; the
    CandidateDocument's original text is never rewritten)."""
    return _html_lib.escape(value or "", quote=True)


def render_context_values(candidate: CandidateDocument) -> dict[str, Any]:
    """Build the ONLY value source the fill ever reads: the reviewed
    CandidateDocument itself. The Agent never sees or edits this mapping's
    construction. Every scalar value is HTML-ESCAPED here (verbatim leaf
    text encoded for HTML output); the join markup (`<br>`, `<li>`) is
    generated by this shell, never by candidate text."""
    leaves = {leaf.leaf_id: leaf for leaf in candidate.leaves}
    children: dict[str, list[Any]] = {}
    for leaf in candidate.leaves:
        if leaf.parent_leaf_id:
            children.setdefault(leaf.parent_leaf_id, []).append(leaf)

    header = {leaf.slot: leaf.text for leaf in candidate.leaves if leaf.kind == "header_field"}
    values: dict[str, Any] = {
        "candidate_name": _esc(header.get("name", "")),
        "candidate_contact": " · ".join(
            _esc(text) for slot, text in sorted(header.items()) if slot != "name" and text
        ),
    }

    def _entry_items(parent_kind: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for leaf in candidate.leaves:
            if leaf.kind != parent_kind:
                continue
            kids = children.get(leaf.leaf_id, [])
            items.append(
                {
                    # Scalar candidate texts are escaped; `<br>`/`<li>` markup
                    # below is generated by this shell, never by the text.
                    "item": _esc(leaf.text),
                    "item_head": _esc(leaf.text),
                    "item_detail": "<br>".join(
                        _esc(kid.text) for kid in kids if kid.kind == "entry_detail"
                    ),
                    "item_meta": "<br>".join(
                        _esc(kid.text) for kid in kids if kid.kind == "entry_meta"
                    ),
                    "item_bullets": "".join(
                        f"<li>{_esc(kid.text)}</li>" for kid in kids if kid.kind == "work_bullet"
                    ),
                    "item_text": "<br>".join(
                        _esc(kid.text) for kid in kids if kid.kind in {"additional_item"}
                    ),
                    "_leaf_ids": [leaf.leaf_id, *(kid.leaf_id for kid in kids)],
                }
            )
        return items

    def _leaf_consumption(candidate: CandidateDocument) -> dict[str, int]:
        """WHICH leaf is consumed WHERE is decided here, once: every leaf is
        CONSUMED BY CONSTRUCTION once (recorded per leaf). Honest ceiling:
        this is a consumption ledger plus a text-presence check, NOT a DOM/
        leaf-ID render proof — the same value legitimately appearing in two
        leaves cannot be told apart in rendered text, so a double render of
        an identical value is not detectable here (no leaf-ID annotation
        exists in the authored path)."""
        consumed: dict[str, int] = {}
        for leaf in candidate.leaves:
            consumed.setdefault(leaf.leaf_id, 0)
        for leaf in candidate.leaves:
            if leaf.kind == "header_field":
                consumed[leaf.leaf_id] += 1  # name/contact slots consume every header field
            elif leaf.kind == "summary_paragraph":
                consumed[leaf.leaf_id] += 1
            elif leaf.kind in {"work_entry", "education_entry", "additional_section"}:
                consumed[leaf.leaf_id] += 1
                for kid in children.get(leaf.leaf_id, []):
                    consumed[kid.leaf_id] += 1
            elif leaf.kind == "skill_group":
                consumed[leaf.leaf_id] += 1
                for kid in children.get(leaf.leaf_id, []):
                    consumed[kid.leaf_id] += 1
            elif leaf.kind in {"language", "certification_item"}:
                consumed[leaf.leaf_id] += 1
        return consumed

    summary_items = [
        {"item": _esc(leaf.text)} for leaf in candidate.leaves if leaf.kind == "summary_paragraph"
    ]
    skill_items = [
        {
            "item": _esc(leaf.text),
            "item_head": _esc(leaf.text),
            "item_text": "<br>".join(
                _esc(kid.text) for kid in children.get(leaf.leaf_id, []) if kid.kind == "skill"
            ),
        }
        for leaf in candidate.leaves if leaf.kind == "skill_group"
    ]
    values.update(
        {
            "summary": summary_items,
            "experience": _entry_items("work_entry"),
            "education": _entry_items("education_entry"),
            "skills": skill_items,
            "languages": [
                {"item": _esc(leaf.text)} for leaf in candidate.leaves if leaf.kind == "language"
            ],
            "certifications": [
                {"item": _esc(leaf.text)}
                for leaf in candidate.leaves if leaf.kind == "certification_item"
            ],
            "additional": _entry_items("additional_section"),
        }
    )
    values["_consumed"] = _leaf_consumption(candidate)
    return values


class AuthoredFillResult(EvidenceModel):
    """One deterministic fill outcome with per-leaf accounting."""

    html_filled: str
    leaf_counts: dict[str, int] = Field(default_factory=dict)
    missing_leaves: list[str] = Field(default_factory=list)


def _normalize_slot_markers(html: str) -> str:
    """Canonical marker form: tolerate whitespace inside braces, an
    `each <name>` (space) variant, spaced closers, and spaced label markers.
    The fill and the safety validation share this one normalization."""
    html = re.sub(r"\{\{\s*([a-zA-Z_:]+)\s*\}\}", r"{{\1}}", html)
    html = re.sub(r"\{\{\s*each\s+([a-z_]+)\s*\}\}", r"{{each:\1}}", html)
    html = re.sub(r"\{\{\s*/\s*each\s*\}\}", "{{/each}}", html)
    html = re.sub(
        r"\{\{\s*label\s*:\s*([a-z0-9_.\-]+)\s*\}\}", r"{{label:\1}}", html
    )
    return html


def fill_authored_template(
    template: AuthoredTemplateCandidate,
    candidate: CandidateDocument,
    labels: list[PresentationLabel] | None = None,
) -> AuthoredFillResult:
    """Fill declared slot tokens with verbatim candidate values and
    presentation-label markers with the CATALOG's text. Candidate facts come
    only from the reviewed CandidateDocument; label text comes only from the
    shell-owned catalog — the two channels never mix. Returns per-leaf
    occurrence counts for the accounting gate."""
    label_index = _label_index(labels)
    values = render_context_values(candidate)
    html = _normalize_slot_markers(template.html)

    # Presentation labels resolve to opaque PLACEHOLDERS first and are
    # substituted LAST: a label's text therefore can never be re-parsed as a
    # candidate slot, another marker, or markup. An unlisted id fails closed.
    placeholders: dict[str, str] = {}

    def _sub_label(match: re.Match[str]) -> str:
        label = label_index.get(match.group(1))
        if label is None:
            raise ValueError(
                "unfilled presentation label marker (label id not issued by "
                f"the shell, fail closed): {match.group(1)!r}"
            )
        token = f"\x00label{len(placeholders)}\x00"
        placeholders[token] = _esc(label.text)
        return token

    html = _LABEL_MARKER_RE.sub(_sub_label, html)

    def _substitute_simple(match: re.Match[str]) -> str:
        token = match.group(1).replace(":", "_")
        if token == "candidate_name":
            return str(values.get("candidate_name", ""))
        if token == "candidate_contact":
            return str(values.get("candidate_contact", ""))
        return match.group(0)

    html = _TOKEN_RE.sub(_substitute_simple, html)

    def _fill_region(match: re.Match[str]) -> str:
        name, body = match.group(1), match.group(2)
        items = values.get(name, [])
        token_pattern = re.compile(
            "|".join(re.escape("{{" + token + "}}") for token in ITEM_TOKENS)
        )
        rendered = []
        for item in items:
            # ONE substitution pass: item tokens get their value (or "" for
            # an optional no-value field). Values are never re-scanned,
            # stripped or rewritten afterwards — a candidate value carrying
            # `{{...}}` survives the fill verbatim and fails closed at the
            # residual-token check below.
            filled = token_pattern.sub(
                lambda m: str(item.get(m.group(0)[2:-2], "")), body
            )
            rendered.append(filled)
        return "".join(rendered)

    html = _EACH_RE.sub(_fill_region, html)
    # Fail closed on ANY residual `{{...}}`: either an unfilled template
    # token, a malformed/unknown label marker, or a candidate's own literal
    # text containing brace syntax. The broad pattern (not just lowercase
    # slot tokens) catches values like `{{Foo}}`/`{{ x }}` that the slot
    # grammar would not match. Label placeholders carry no braces, so an
    # inserted label value can never reach this check.
    residual = re.findall(r"\{\{.*?\}\}", html, flags=re.DOTALL)
    if residual:
        raise ValueError(
            f"unfilled slot tokens remain after fill (fail closed; candidate "
            f"values are never stripped or rewritten): {residual[:6]}"
        )
    # Insert the (HTML-escaped) label texts LAST: nothing above can re-parse
    # them, and no marker can remain afterwards.
    for token, value in placeholders.items():
        html = html.replace(token, value)

    # Accounting: every candidate leaf is consumed by exactly one region
    # instance BY CONSTRUCTION (recorded per leaf); its verbatim value must
    # be present in the filled HTML.
    from tests.experiments.c2_renderer import _html_text

    html_text, _ = _html_text(html)
    html_norm = _norm_text(html_text)
    counts: dict[str, int] = dict(values.get("_consumed", {}))
    missing: list[str] = []
    for leaf in candidate.leaves:
        text = _norm_text(leaf.text or "")
        if counts.get(leaf.leaf_id, 0) != 1 or (text and _occurrences(html_norm, text) < 1):
            missing.append(leaf.leaf_id)
    return AuthoredFillResult(html_filled=html, leaf_counts=counts, missing_leaves=missing)


# ---------------------------------------------------------------------------
# Document assembly + gates
# ---------------------------------------------------------------------------


def build_authored_document(
    template: AuthoredTemplateCandidate, fill: AuthoredFillResult, page_size: tuple[float, float]
) -> str:
    """Assemble the final standalone HTML: target page geometry + authored
    CSS + filled body, then the SAME local-font injection the canonical
    render path uses. No external asset, no script, no network reference."""
    from tests.experiments.a_pipeline import _inject_local_fonts

    width_pt, height_pt = page_size
    page_css = (
        f"@page {{ size: {width_pt:.3f}pt {height_pt:.3f}pt; margin: 36pt; }}\n"
        "* { box-sizing: border-box; margin: 0; padding: 0; }"
    )
    html = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<title>E5 Lane B authored render</title>\n<style>\n"
        + page_css
        + "\n"
        + template.css
        + "\n</style>\n</head>\n<body>\n"
        + fill.html_filled
        + "\n</body>\n</html>"
    )
    return _inject_local_fonts(html)


def authored_network_disabled_environment() -> dict[str, Any]:
    """Pinned Chrome environment with network resolution DISABLED for
    authored-template rendering. Honest scope: the flag is enforced and
    tested by presence; per-render remote-fetch failure is NOT separately
    proven (no probe asserts a fetch attempt failed), so the claim recorded
    is "network resolution disabled at the Chrome host resolver", never
    "proven unable to load network assets"."""
    from tests.experiments.c_pipeline import pinned_export_environment

    environment = pinned_export_environment({})
    environment["flags"] = [
        *environment.get("flags", []),
        "--host-resolver-rules=MAP * ~NOTFOUND",
        "--disable-background-networking",
    ]
    return environment


def authored_privacy_gate(
    html: str, pdf: Path, target_pdf: Path, *, labels: set[str]
) -> dict[str, Any]:
    """No target-person line may enter the authored output (same target-text
    line granularity as the canonical privacy gate; rail/section labels are
    the template presentation the owner decision renders and are excluded)."""
    from app.ingestion.pdf_reader import read_pdf_text
    from tests.experiments.c2_renderer import _html_text, _norm

    html_text, _ = _html_text(html)
    normalized_html = _norm(html_text)
    normalized_pdf = _norm(read_pdf_text(pdf))
    excluded = {_norm(label) for label in labels}
    target_lines = [
        line.strip()
        for line in read_pdf_text(target_pdf).splitlines()
        if line.strip() and any(character.isalnum() for character in line)
    ]
    leaked: list[str] = []
    checked: list[dict[str, Any]] = []
    for line in target_lines:
        token = _norm(line)
        if token in excluded:
            continue
        hit = token in normalized_html or token in normalized_pdf
        checked.append({"line": line, "hit": hit})
        if hit:
            leaked.append(line)
    return {
        "passed": not leaked,
        "leaked_target_lines": leaked,
        "checked_target_lines": checked,
        "excluded_labels": sorted(excluded),
    }


def authored_pdf_presence_gate(
    fill: AuthoredFillResult, candidate: CandidateDocument, pdf: Path
) -> dict[str, Any]:
    """Every candidate leaf present in the ACTUAL final PDF text (hyphen
    artifacts tolerated like the canonical content gate)."""
    from app.ingestion.pdf_reader import read_pdf_text

    normalized_pdf = _norm_text(read_pdf_text(pdf)).replace("- ", "").replace("-", "")
    missing = []
    for leaf in candidate.leaves:
        text = _norm_text(leaf.text or "").replace("-", "")
        if text and _occurrences(normalized_pdf, text) < 1:
            missing.append(leaf.leaf_id)
    return {"passed": not missing, "missing_pdf_leaves": missing}


# ---------------------------------------------------------------------------
# Offline rehearsal template (scripted verification path; NOT production)
# ---------------------------------------------------------------------------

SCRIPTED_TEMPLATE_ID = "scripted-two-rail-e5"

SCRIPTED_AUTHORED_TEMPLATE = AuthoredTemplateCandidate(
    template_id=SCRIPTED_TEMPLATE_ID,
    html=(
        "<div class=\"rsv-name\">{{candidate:name}}</div>\n"
        "<div class=\"rsv-contact\">{{candidate:contact}}</div>\n"
        "{{each:summary}}<p class=\"rsv-summary\">{{item}}</p>{{/each}}\n"
        "{{each:skills}}<p class=\"rsv-line\">{{item}}</p><p class=\"rsv-line\">{{item_text}}</p>{{/each}}\n"
        "{{each:languages}}<p class=\"rsv-line\">{{item}}</p>{{/each}}\n"
        "{{each:experience}}\n"
        "  <div class=\"rsv-entry\">\n"
        "    <div class=\"rsv-entry-main\"><p class=\"rsv-head\">{{item_head}}</p>"
        "<p class=\"rsv-detail\">{{item_detail}}</p>"
        "<ul class=\"rsv-bullets\">{{item_bullets}}</ul></div>\n"
        "    <div class=\"rsv-entry-meta\"><p class=\"rsv-meta\">{{item_meta}}</p></div>\n"
        "  </div>\n"
        "{{/each}}\n"
        "{{each:education}}\n"
        "  <div class=\"rsv-entry\">\n"
        "    <div class=\"rsv-entry-main\"><p class=\"rsv-head\">{{item_head}}</p>"
        "<p class=\"rsv-detail\">{{item_detail}}</p></div>\n"
        "    <div class=\"rsv-entry-meta\"><p class=\"rsv-meta\">{{item_meta}}</p></div>\n"
        "  </div>\n"
        "{{/each}}\n"
        "{{each:certifications}}<p class=\"rsv-line\">{{item}}</p>{{/each}}\n"
        "{{each:additional}}\n"
        "  <p class=\"rsv-head\">{{item_head}}</p>\n"
        "  <p class=\"rsv-line\">{{item_text}}</p>\n"
        "{{/each}}\n"
    ),
    css=(
        "body { font-family: Arial, sans-serif; font-size: 10pt; color: #111; }\n"
        ".rsv-name { text-align: center; font-size: 16pt; }\n"
        ".rsv-contact { text-align: center; font-size: 9pt; }\n"
        ".rsv-entry { display: flex; justify-content: space-between; margin-top: 6pt; }\n"
        ".rsv-entry-main { min-width: 0; }\n"
        ".rsv-entry-meta { text-align: right; margin-left: 12pt; flex-shrink: 0; }\n"
        ".rsv-bullets { list-style: none; }\n"
    ),
    slots=[
        AuthoredSlot(category="candidate_name", required=True),
        AuthoredSlot(category="candidate_contact", required=True),
        AuthoredSlot(category="summary", repeating=True),
        AuthoredSlot(category="experience", repeating=True),
        AuthoredSlot(category="education", repeating=True),
        AuthoredSlot(category="skills", repeating=True),
        AuthoredSlot(category="languages", repeating=True),
        AuthoredSlot(category="certifications", repeating=True),
        AuthoredSlot(category="additional", repeating=True),
    ],
)
