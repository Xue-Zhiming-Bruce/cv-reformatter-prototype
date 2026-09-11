"""Lightweight source-provenance analysis for candidate HTML."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from lxml import etree, html

_REDACTION = (re.compile(r"\[redacted[^]]*\]", re.I), re.compile(r"web\s*copy", re.I), re.compile(r"\(cid:\d+\)", re.I))
_HEADINGS = {f"h{level}" for level in range(1, 7)}
_PLACEHOLDER_ONLY = re.compile(r"^\s*(?:\[[^]]+\]\s*[-–—|/]?\s*)+$")
_SECTION_NAMES = {
    "summary", "profile", "objective", "highlights", "skills", "skills pool",
    "experience", "work experience", "employment", "education", "projects",
    "languages", "volunteer experience", "awards", "interests",
}
# ponytail: English heading aliases cover the current synthetic corpus; replace
# with CandidateProfile section types when this experiment joins the product pipeline.
_SECTION_SEMANTICS = {
    "summary": "summary", "profile": "summary", "objective": "summary",
    "highlights": "highlights",
    "skills": "skills", "skills pool": "skills", "key skills": "skills", "languages": "skills",
    "experience": "experience", "work experience": "experience", "employment": "experience",
    "volunteer experience": "volunteer",
    "education": "education", "education certifications": "education", "certifications": "education",
    "projects": "projects", "additional": "additional", "another section": "additional",
}
_ROLE_WORDS = re.compile(
    r"\b(?:senior|junior|engineer|manager|director|consultant|analyst|developer|designer|officer|specialist|lead|person)\b",
    re.I,
)
# Contact-kind names follow the Font Awesome class the template uses for the
# icon (fa-phone/fa-envelope/fa-github/fa-linkedin); shared by the fill-time
# slot-semantic gate and the B-pipeline template slot-inventory check.
CONTACT_KIND_CHECKS = {
    "phone": lambda value: len(re.findall(r"\d", value)) >= 7,
    "envelope": lambda value: "@" in value,
    "github": lambda value: "github" in value.casefold(),
    "linkedin": lambda value: "linkedin" in value.casefold(),
}


def source_lines(source_text: str) -> dict[str, str]:
    return {
        f"L{index:04d}": line.strip()
        for index, line in enumerate(source_text.splitlines(), 1)
        if line.strip() and any(character.isalnum() for character in line)
        and not any(pattern.search(line) for pattern in _REDACTION)
    }


def source_blocks(source_text: str) -> dict[str, str]:
    """Derive stable section ownership from source reading order."""
    blocks: dict[str, str] = {}
    current = "document"
    for line_id, line in source_lines(source_text).items():
        prefix = re.split(r"\s+[—–-]\s+", line, maxsplit=1)[0]
        normalized = re.sub(r"[^a-z0-9]+", " ", prefix.casefold()).strip()
        if normalized in _SECTION_NAMES or (line.isupper() and len(line.split()) <= 4):
            current = f"block:{line_id}"
        blocks[line_id] = current
    return blocks


def _tokens(value: str) -> Counter[str]:
    return Counter(re.findall(r"\w+", value.casefold()))


def _nearest(element: etree._Element, attribute: str) -> str | None:
    node: etree._Element | None = element
    while node is not None:
        if value := node.get(attribute):
            return value
        node = node.getparent()
    return None


def _node(element: etree._Element, tree: etree._ElementTree[Any]) -> dict[str, Any]:
    return {
        "node": tree.getpath(element),
        "tag": str(element.tag),
        "text": re.sub(r"\s+", " ", " ".join(element.itertext())).strip(),
        "ancestry": {
            "source_block": _nearest(element, "data-source-block"),
            "source_record": _nearest(element, "data-source-record"),
        },
        "heading": str(element.tag).casefold() in _HEADINGS,
    }


def _visible_text_nodes(root: etree._Element, tree: etree._ElementTree[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for element in root.iter():
        if not isinstance(element.tag, str) or element.tag.casefold() in {"style", "script", "title", "template"}:
            continue
        if element.text and element.text.strip():
            rows.append({**_node(element, tree), "text": element.text.strip()})
        for child in element:
            if child.tail and child.tail.strip():
                rows.append({**_node(element, tree), "text": child.tail.strip()})
    return rows


def _date_like_source_line(value: str) -> bool:
    return bool(re.search(r"\b(?:19|20)\d{2}\b", value)) and len(re.findall(r"\w+", value)) <= 8


def _section_semantic(value: str) -> str | None:
    prefix = re.split(r"\s+[—–-]\s+", value, maxsplit=1)[0]
    normalized = re.sub(r"[^a-z0-9]+", " ", prefix.casefold()).strip()
    return _SECTION_SEMANTICS.get(normalized)


def _placeholder_root(element: etree._Element, tree: etree._ElementTree[Any]) -> dict[str, Any]:
    node = element
    while node.getparent() is not None and str(node.getparent().tag).casefold() not in {"html", "body", "head"}:
        parent = node.getparent()
        if parent.xpath(".//*[@data-source-line]"):
            break
        visible = re.sub(r"\[[^]]+\]", "", " ".join(parent.itertext()))
        if any(character.isalnum() for character in visible):
            break
        node = parent
    return _node(node, tree)


def deduplicate_candidate_html(document: str, source_text: str) -> tuple[str, int]:
    """Owner decision 2026-09-08: content duplication is a mechanical defect,
    not a repair task — keep the copy of each over-rendered source line that
    sits inside its authoritative source block (document order within it),
    delete surplus copies, and let the gates re-verify coverage. Deterministic;
    no model call. Returns (html, removed_fragment_count)."""
    tree = html.document_fromstring(document)
    source = source_lines(source_text)
    blocks = source_blocks(source_text)
    removed = 0
    for line_id, line in source.items():
        nodes = tree.xpath(f"//*[@data-source-line='{line_id}'][not(.//*[@data-source-line])]")
        if len(nodes) < 2:
            continue
        expected = _tokens(line)
        counts = [(node, _tokens(" ".join(node.itertext()))) for node in nodes]
        complete_nodes = [node for node, count in counts if count == expected]
        complete = next((
            node for node in complete_nodes
            if any(ancestor.get("data-source-block") == blocks[line_id] for ancestor in node.iterancestors())
        ), complete_nodes[0] if complete_nodes else None)
        keep = {complete} if complete is not None else set()
        if complete is None:
            remaining = expected.copy()
            for node, count in counts:
                if count and not (count - remaining):
                    keep.add(node)
                    remaining -= count
            if remaining:
                continue
        for node in nodes:
            if node not in keep and node.getparent() is not None:
                node.getparent().remove(node)
                removed += 1
    if not removed:
        return document, 0
    return etree.tostring(tree, encoding="unicode", method="html"), removed


def normalize_header_element(document: str) -> str:
    """Owner decision 2026-09-09 (E->D matrix postmortem, re-applied after the
    repo slimming): the Builder may render the contact area as <div
    class="header">, but the provenance gate requires document-block lines
    (name/contact) to live inside a real <header> element — the Filler imitates
    the template's div and burns every repair round on header_semantic_mismatch
    (13/13 rounds failed before this fix). Renaming the tag is deterministic
    bookkeeping; class selectors keep matching so template CSS is untouched.
    Applies to the template (so the Filler sees the right structure) and to
    Filler output (in case it reverts to a div)."""
    try:
        tree = html.document_fromstring(document)
    except Exception:
        return document
    if tree.xpath("//header"):
        return document
    headers = tree.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' header ')]")
    if not headers:
        return document
    headers[0].tag = "header"
    return etree.tostring(tree, encoding="unicode", method="html")


def normalize_section_headings(candidate_html: str, base_template_html: str, source_text: str) -> str:
    """Owner decision 2026-09-09 (F->D matrix postmortem): the Filler keeps
    rendering a section's body but dropping the source HEADING line (e.g.
    "TECHNICAL SKILLS" never rendered), so the verbatim gates report it missing
    forever. Deterministic fix, minimal form of the pre-slimming normalizer
    (case rewriting and token-diff section removal deliberately NOT re-added
    per codex review): for every section claiming a data-source-heading line,
    if no descendant carries that annotation, write the source heading text
    VERBATIM into the section's unannotated heading slot, or clone the
    template section's own heading prototype when the Filler dropped the
    heading element entirely. The template's all-caps look is presentation:
    slots whose prototype was all-caps are marked and the template carries an
    inert text-transform rule (see append_heading_case_rule)."""
    lines = source_lines(source_text)
    try:
        tree = html.document_fromstring(candidate_html)
        template_tree = html.document_fromstring(base_template_html)
    except Exception:
        return candidate_html
    prototypes: dict[str, tuple[str, str, bool]] = {}
    for element in template_tree.xpath("//*[@data-section]"):
        if element.get("data-section") in prototypes:
            continue
        for candidate in element.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//h6"):
            text = " ".join(candidate.itertext()).strip()
            prototypes[element.get("data-section")] = (
                str(candidate.tag), candidate.get("class") or "", bool(text) and text == text.upper(),
            )
            break

    changed = False
    for section in tree.xpath("//*[@data-source-heading]"):
        heading_id = section.get("data-source-heading") or ""
        line = lines.get(heading_id)
        if not line or section.xpath(f".//*[@data-source-line='{heading_id}']"):
            continue
        upper = False
        # Owner ruling 2026-09-11 (E→F final rerun): the Filler may echo the
        # section's annotations onto the heading element itself; that element
        # is then the claiming node, not a section container — its own text is
        # the heading line verbatim (exactly one DOM concatenation), so the
        # uniquely unambiguous normalization annotates it in place. Cloning a
        # heading INSIDE it would duplicate the source line (caught by the
        # verbatim gates as SKILLS-class doubling).
        if section.tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or section.get("data-slot") == "heading":
            slot = section
            current = " ".join(slot.itertext()).strip()
            upper = bool(current) and current == current.upper()
            slot.text = line  # verbatim source text — never re-cased
            for child in list(slot):
                slot.remove(child)
            slot.set("data-source-line", heading_id)
            if upper:
                slot.set("data-text-transform", "uppercase")
            changed = True
            continue
        slots = section.xpath(
            ".//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6][not(@data-source-line)]"
        ) + section.xpath(".//*[@data-slot='heading'][not(@data-source-line)]")
        if slots:
            slot = slots[0]
            current = " ".join(slot.itertext()).strip()
            upper = bool(current) and current == current.upper()
        else:
            # The Filler dropped the heading element entirely: clone the
            # template section's own prototype so the heading line renders.
            tag, css_class, upper = prototypes.get(section.get("data-section") or "") or ("h2", "", False)
            slot = etree.SubElement(section, tag)
            if css_class:
                slot.set("class", css_class)
            section.insert(0, slot)
        slot.text = line  # verbatim source text — never re-cased (codex review)
        for child in list(slot):
            slot.remove(child)
        slot.set("data-source-line", heading_id)
        if upper:
            slot.set("data-text-transform", "uppercase")
        changed = True
    if not changed:
        return candidate_html
    return etree.tostring(tree, encoding="unicode", method="html")


def append_heading_case_rule(template_html: str) -> str:
    """Owner decision 2026-09-09 (codex review): visual heading case is CSS
    presentation (text-transform), never rewritten source text. The template
    style blocks carry this one inert rule (nothing in the template sets the
    attribute, so the template renders unchanged); normalize_section_headings
    marks the slots whose target convention was all-caps. Idempotent; runs
    once at template finalization so candidate style blocks stay identical to
    the template's (filler conformance)."""
    rule = "[data-text-transform='uppercase'] { text-transform: uppercase; }"
    if rule in template_html:
        return template_html
    match = re.search(r"</style>", template_html, re.I)
    if not match:
        return template_html
    index = match.start()
    return template_html[:index] + rule + "\n" + template_html[index:]


def analyze_candidate_provenance(document: str, source_text: str, *, allowed_text: str = "") -> dict[str, Any]:
    """Return the single structured content/provenance truth for all gates."""
    source = source_lines(source_text)
    expected_blocks = source_blocks(source_text)
    findings: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    ownership: dict[str, str] = {}
    observed = {line_id: Counter() for line_id in source}
    ordered = {line_id: [] for line_id in source}
    fragments: dict[str, list[dict[str, Any]]] = {line_id: [] for line_id in source}
    try:
        root = html.document_fromstring(document)
    except (etree.ParserError, ValueError) as error:
        return {"parsed": False, "findings": [{"code": "invalid_html", "error": str(error)}], "warnings": [], "ownership": {}}

    tree = root.getroottree()
    block_roots = [
        element for element in root.xpath("//*[@data-source-block]")
        if element.get("data-source-line") is None
        if not any(
            ancestor.get("data-source-block") == element.get("data-source-block")
            for ancestor in element.iterancestors()
        )
    ]
    for block_root in block_roots:
        block_id = block_root.get("data-source-block") or ""
        heading_id = block_root.get("data-source-heading")
        if block_id == "document" and heading_id:
            warnings.append({"code": "document_has_no_section_heading", "node": _node(block_root, tree)})
        elif block_id != "document" and (
            not re.fullmatch(r"block:L\d{4}", block_id) or heading_id is None or block_id != f"block:{heading_id}"
        ):
            warnings.append({"code": "invalid_source_block_identity", "node": _node(block_root, tree), "source_heading": heading_id})
        elif heading_id in source and block_root.get("data-section"):
            source_semantic = _section_semantic(source[heading_id])
            section_semantic = _section_semantic(block_root.get("data-section") or "")
            if source_semantic and section_semantic and source_semantic != section_semantic:
                findings.append({
                    "code": "section_semantic_mismatch",
                    "source_line_id": heading_id,
                    "source_text": source[heading_id],
                    "data_section": block_root.get("data-section"),
                    "source_semantic": source_semantic,
                    "section_semantic": section_semantic,
                    "offending_nodes": [_node(block_root, tree)],
                })

    for element in root.xpath("//*[@data-slot='location'][@data-source-line]"):
        line_id = element.get("data-source-line") or ""
        if line_id in source and _ROLE_WORDS.search(source[line_id]):
            findings.append({
                "code": "slot_semantic_mismatch", "source_line_id": line_id,
                "source_text": source[line_id], "data_slot": "location",
                "offending_nodes": [_node(element, tree)],
            })

    for item in root.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' contact-item ')]"):
        classes = " ".join(item.xpath(".//i/@class"))
        kind = next((name for name in CONTACT_KIND_CHECKS if f"fa-{name}" in classes), None)
        value_nodes = item.xpath(".//*[@data-source-line]")
        if not kind and value_nodes:
            for value_node in value_nodes:
                line_id = value_node.get("data-source-line") or ""
                findings.append({
                    "code": "slot_semantic_mismatch", "source_line_id": line_id,
                    "source_text": source.get(line_id), "data_slot": "contact_without_icon",
                    "offending_nodes": [_node(value_node, tree)],
                })
            continue
        for value_node in value_nodes:
            line_id = value_node.get("data-source-line") or ""
            if line_id in source and not CONTACT_KIND_CHECKS[kind](source[line_id]):
                findings.append({
                    "code": "slot_semantic_mismatch", "source_line_id": line_id,
                    "source_text": source[line_id], "data_slot": kind,
                    "offending_nodes": [_node(value_node, tree)],
                })

    for element in root.xpath("//*[@data-source-line]"):
        line_id = element.get("data-source-line") or ""
        fragment = _node(element, tree)
        if list(element.xpath(".//*[@data-source-line]")):
            warnings.append({"code": "nested_source_annotations", "source_line_id": line_id, "offending_nodes": [fragment]})
            continue
        if line_id not in source:
            row = {"code": "unknown_source_line", "source_line_id": line_id, "offending_nodes": [fragment]}
            # Owner rule 2026-09-08: a fabricated line ID on a punctuation-only
            # decorative node (e.g. an em-dash separator) is harmless — it carries
            # no tokens and takes part in no coverage check. Warn; do not fail.
            (warnings if not re.findall(r"\w+", fragment["text"]) else findings).append(row)
            continue
        block_id = fragment["ancestry"]["source_block"]
        record_id = fragment["ancestry"]["source_record"]
        if expected_blocks[line_id] == "document" and not any(
            str(node.tag).casefold() == "header" for node in (element, *element.iterancestors())
        ):
            findings.append({
                "code": "header_semantic_mismatch", "source_line_id": line_id,
                "source_text": source[line_id], "offending_nodes": [fragment],
            })
        record_line = record_id.removeprefix("record:") if record_id and re.fullmatch(r"record:L\d{4}", record_id) else None
        expected_block = expected_blocks.get(record_line, expected_blocks[line_id])
        fragments[line_id].append(fragment)
        destinations = [_node(item, tree) for item in root.xpath(f"//*[@data-source-block='{expected_block}']")]
        if block_id != expected_block:
            # Owner decision 2026-09-08: section placement is a semantic call —
            # the model may move a source section's lines into a better-matching
            # template section; unmapped sections are appended in source order.
            # Coverage stays hard; ownership is advisory and audited in warnings.
            # Content outside every declared block owner stays a hard finding.
            row = {
                "code": "source_block_assignment", "source_line_id": line_id, "source_text": source[line_id],
                "expected_block": expected_block, "actual_block": block_id,
                "offending_nodes": [fragment], "allowed_destination_nodes": destinations,
            }
            (findings if block_id is None else warnings).append(row)
        previous_block = ownership.setdefault(line_id, block_id or "")
        if previous_block != (block_id or ""):
            findings.append({
                "code": "source_block_assignment", "source_line_id": line_id, "source_text": source[line_id],
                "expected_block": previous_block, "actual_block": block_id,
                "offending_nodes": list(fragments[line_id]), "allowed_destination_nodes": [
                    _node(item, tree) for item in root.xpath(f"//*[@data-source-block='{previous_block}']")
                ],
            })
        if record_id and not re.fullmatch(r"record:L\d{4}", record_id):
            findings.append({
                "code": "source_record_assignment", "source_line_id": line_id, "source_text": source[line_id],
                "expected_record": None, "actual_record": record_id, "offending_nodes": [fragment],
                "allowed_destination_nodes": [],
            })
        tokens = re.findall(r"\w+", fragment["text"].casefold())
        observed[line_id].update(tokens)
        ordered[line_id].extend(tokens)

    for block_id, count in Counter(item.get("data-source-block") for item in block_roots).items():
        if count > 1:
            warnings.append({"code": "duplicate_source_block", "source_block": block_id, "count": count})
    for block_root in block_roots:
        heading_id = block_root.get("data-source-heading")
        if heading_id and ownership.get(heading_id) != block_root.get("data-source-block"):
            warnings.append({"code": "missing_source_heading", "source_line_id": heading_id, "node": _node(block_root, tree)})

    for record_root in root.xpath("//*[@data-source-record]"):
        line_ids = [item.get("data-source-line") for item in record_root.xpath(".//*[@data-source-line]") if item.get("data-source-line") in source]
        if line_ids:
            expected_record = f"record:{min(line_ids, key=lambda item: int(item[1:]))}"
            actual_record = record_root.get("data-source-record")
            if actual_record != expected_record:
                # Owner decision 2026-09-08: record bookkeeping (the ID is the
                # min member line) is advisory once semantic placement is allowed.
                warnings.append({
                    "code": "source_record_assignment", "source_line_id": line_ids[0], "source_text": source[line_ids[0]],
                    "expected_record": expected_record, "actual_record": actual_record,
                    "offending_nodes": [_node(record_root, tree)], "allowed_destination_nodes": [],
                })

    # Owner policy 2026-09-08 (block cohesion): a source section is placed
    # WHOLE — the model may map it into any single template section by meaning,
    # but its lines must never be scattered across two rendered sections.
    # Date-like lines are exempt: their cross-section placement is already an
    # accepted extraction-order warning.
    block_actual_blocks: dict[str, set[str]] = {}
    for line_id, actual_block in ownership.items():
        if _date_like_source_line(source[line_id]):
            continue
        block_actual_blocks.setdefault(expected_blocks[line_id], set()).add(actual_block)
    for expected_block, actual_blocks in block_actual_blocks.items():
        if len({block for block in actual_blocks if block}) > 1:
            line_ids = sorted(line for line, block in expected_blocks.items() if block == expected_block)
            findings.append({
                "code": "source_block_split", "expected_block": expected_block,
                "actual_blocks": sorted(actual_blocks),
                "source_line_ids": line_ids,
                "source_texts": {line_id: source[line_id] for line_id in line_ids},
            })

    for line_id, line in source.items():
        expected = _tokens(line)
        missing = list((expected - observed[line_id]).elements())
        excess = list((observed[line_id] - expected).elements())
        destinations = [_node(item, tree) for item in root.xpath(f"//*[@data-source-block='{expected_blocks[line_id]}']")]
        if missing:
            row = {
                "code": "missing_source_content", "source_line_id": line_id, "source_text": line,
                "missing_tokens": missing, "relevant_fragments": fragments[line_id],
                "allowed_destination_nodes": destinations,
            }
            findings.append(row)
        if excess:
            findings.append({
                "code": "duplicated_source_content", "source_line_id": line_id, "source_text": line,
                "excess_tokens": excess, "offending_nodes": fragments[line_id],
                "allowed_destination_nodes": destinations,
            })
        expected_order = re.findall(r"\w+", line.casefold())
        if not missing and not excess and ordered[line_id] != expected_order:
            warnings.append({
                "code": "source_reading_order", "source_line_id": line_id, "source_text": line,
                "expected_tokens": expected_order, "actual_tokens": ordered[line_id],
                "offending_nodes": fragments[line_id], "allowed_destination_nodes": destinations,
            })
        # NOTE (owner decision 2026-09-08): a source line may legitimately be
        # split across several heading elements (e.g. "SUMMARY —" as the section
        # heading, the rest as an entry title); exact_token_coverage already
        # rejects any actual duplication, so no heading-count invariant.
        body_records = [fragment["ancestry"]["source_record"] for fragment in fragments[line_id] if not fragment["heading"]]
        if len(set(body_records)) > 1:
            expected_record = body_records[0]
            for fragment in fragments[line_id]:
                if not fragment["heading"] and fragment["ancestry"]["source_record"] != expected_record:
                    findings.append({
                        "code": "source_record_assignment", "source_line_id": line_id, "source_text": line,
                        "expected_record": expected_record, "actual_record": fragment["ancestry"]["source_record"],
                        "offending_nodes": [fragment], "allowed_destination_nodes": [
                            _node(item, tree) for item in root.xpath(f"//*[@data-source-record='{expected_record}']")
                        ] if expected_record else [],
                    })

    allowed_vocab = set(_tokens(f"{source_text} {allowed_text}"))
    invented: dict[str, list[dict[str, Any]]] = {}
    placeholder_tokens: set[str] = set()
    placeholder_nodes: dict[str, dict[str, Any]] = {}
    for row in _visible_text_nodes(root, tree):
        for token in re.findall(r"\w+", row["text"].casefold()):
            if token not in allowed_vocab:
                if _PLACEHOLDER_ONLY.fullmatch(row["text"]):
                    placeholder_tokens.add(token)
                    placeholder = _placeholder_root(tree.xpath(row["node"])[0], tree)
                    placeholder_nodes[placeholder["node"]] = placeholder
                else:
                    invented.setdefault(token, []).append(row)
    if placeholder_tokens:
        findings.append({
            "code": "invented_content", "invented_tokens": sorted(placeholder_tokens),
            "offending_nodes": list(placeholder_nodes.values()), "placeholder_derived": True,
        })
    findings.extend(
        {"code": "invented_content", "invented_tokens": [token], "offending_nodes": rows}
        for token, rows in sorted(invented.items())
    )
    return {"parsed": True, "findings": findings, "warnings": warnings, "ownership": ownership}


def _compatibility_error(finding: dict[str, Any]) -> str:
    code, line_id = finding["code"], finding.get("source_line_id")
    if code == "invalid_html":
        return f"invalid HTML: {finding.get('error')}"
    if code in {"missing_source_content", "duplicated_source_content"}:
        return (
            f"{line_id}: invariant=exact_token_coverage; source={finding.get('source_text')!r}; "
            f"missing={finding.get('missing_tokens', [])!r}; duplicated={finding.get('excess_tokens', [])!r}; "
            f"fragments={finding.get('relevant_fragments', finding.get('offending_nodes', []))!r}"
        )
    if code == "source_block_assignment":
        if finding.get("actual_block") is None:
            return f"{line_id}: source content has no block owner"
        return f"{line_id}: invariant=source_block_assignment; source={finding.get('source_text')!r}; expected={finding.get('expected_block')!r}; actual={finding.get('actual_block')!r}; fragments={finding.get('offending_nodes', [])!r}"
    if code == "source_record_assignment":
        return f"{line_id}: invariant=one_content_record; source={finding.get('source_text')!r}; expected={finding.get('expected_record')!r}; actual={finding.get('actual_record')!r}; fragments={finding.get('offending_nodes', [])!r}"
    if code == "source_reading_order":
        return f"{line_id}: invariant=source_reading_order; evidence={finding!r}"
    if code == "invented_content":
        return f"invariant=invented_content; evidence={finding!r}"
    if code == "unknown_source_line":
        return f"unknown or excluded source line {line_id}"
    if code == "nested_source_annotations":
        return f"{line_id}: nested source annotations would double-count content"
    if code == "missing_source_heading":
        return f"source block: heading {line_id} is missing or misplaced"
    return f"invariant={code}; evidence={finding!r}"


def validate_candidate_html(
    document: str,
    source_text: str,
    *,
    allowed_text: str = "",
    analysis: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Compatibility report derived from :func:`analyze_candidate_provenance`."""
    analysis = analysis or analyze_candidate_provenance(document, source_text, allowed_text=allowed_text)
    return [_compatibility_error(finding) for finding in analysis["findings"]], analysis["ownership"]


def provenance_report(ownership: dict[str, str], source_text: str) -> dict[str, Any]:
    lines = source_lines(source_text)
    return {
        "schema_version": "a-pipeline-source-provenance/1",
        "lines": [
            {"line_id": line_id, "source_block_id": ownership[line_id], "text": text}
            for line_id, text in lines.items() if line_id in ownership
        ],
    }
