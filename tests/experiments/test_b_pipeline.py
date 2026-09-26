from __future__ import annotations

from tests.experiments.b_pipeline import (
    _ensure_icon_font_family,
    source_header_units,
    template_contamination,
    template_slot_gaps,
    validate_filler_header_structure,
)

TEMPLATE = (
    "<html><head><style>.name { font-size: 12pt; }</style></head><body>"
    "<header><span data-slot=\"name\">[NAME]</span></header>"
    "<p class=\"name\">[NAME]</p></body></html>"
)


def test_template_contamination_allows_structural_section_labels() -> None:
    template = TEMPLATE.replace("[NAME]", "CERTIFICATIONS")

    assert template_contamination(template, "Certifications:", "Experience") == []


def test_source_header_units_type_document_lines() -> None:
    source = "Jane Candidate\nSenior Business Person\nBusiness | Hobbies | Awesomeness\ngithub.com/USER\nlinkedin.com/in/USER\nExperience\nBody text"

    units = source_header_units(source)

    assert {unit["kind"]: unit["source_text"] for unit in units} == {
        "name": "Jane Candidate",
        "title": "Senior Business Person",
        "tagline": "Business | Hobbies | Awesomeness",
        "github": "github.com/USER",
        "linkedin": "linkedin.com/in/USER",
    }
    # a section heading is not a header unit
    assert "Experience" not in {unit["source_text"] for unit in units}


def test_template_slot_gaps_report_missing_landings() -> None:
    source = "Jane Candidate\nSenior Business Person\nBusiness | Hobbies | Awesomeness\ngithub.com/USER"
    # the measured D→E failure: header has only name+contact, no title/tagline
    partial = (
        "<html><body><header>"
        "<div data-slot=\"name\">[NAME]</div>"
        "<div data-slot=\"contact\"><span class=\"contact-item\"><i class=\"fa-solid fa-github\"></i>"
        "<span data-slot=\"body\">[GITHUB]</span></span></div>"
        "</header></body></html>"
    )

    gaps = template_slot_gaps(partial, source)

    assert {gap["kind"] for gap in gaps} == {"title", "tagline"}
    assert all(gap["detail"] for gap in gaps)

    complete = partial.replace(
        '<div data-slot="contact">',
        '<div data-slot="title">[TITLE]</div><div data-slot="tagline">[TAGLINE]</div><div data-slot="contact">',
    )
    assert template_slot_gaps(complete, source) == []


def test_filler_cannot_add_header_root_children() -> None:
    template = (
        "<html><body><header class=\"header-section\">"
        "<div class=\"header-name\">[NAME]</div>"
        "<div class=\"header-contact-group\">[CONTACTS]</div>"
        "</header></body></html>"
    )
    legal_fill = template.replace("[NAME]", "Jane Candidate")
    illegal_fill = template.replace(
        "</header>", '<p class="body-text">Invented layout node</p></header>'
    )

    assert validate_filler_header_structure(template, legal_fill) == []
    errors = validate_filler_header_structure(template, illegal_fill)
    assert errors and "header" in errors[0]

    # div.header (the normalize_header_element rename) is deterministic
    # bookkeeping, not a structure change
    div_header = legal_fill.replace("<header class=\"header-section\"", "<div class=\"header\"").replace("</header>", "</div>")
    assert validate_filler_header_structure(template, div_header) == []


def test_filler_may_drop_unfilled_placeholder_slots() -> None:
    """Owner finding 2026-09-09 (run 20260909T160348Z): the Reviewer mirrors the
    target layout with a location slot, but the source has no location line;
    dropping the placeholder-only slot is content behavior, not vandalism."""
    template = (
        "<html><body><header>"
        "<div class=\"header-name\">[NAME]</div>"
        "<div class=\"header-location\">[CITY, STATE]</div>"
        "<div class=\"header-contact-group\">[CONTACTS]</div>"
        "</header></body></html>"
    )
    dropped = (
        "<html><body><header>"
        "<div class=\"header-name\">Jane Candidate</div>"
        "<div class=\"header-contact-group\">github.com/x</div>"
        "</header></body></html>"
    )
    emptied = dropped.replace(
        "<div class=\"header-contact-group\">github.com/x</div>",
        "<div class=\"header-location\"></div><div class=\"header-contact-group\">github.com/x</div>",
    )
    added_node = (
        "<html><body><header>"
        "<div class=\"header-name\">Jane Candidate</div>"
        "<p class=\"body-text\">invented</p>"
        "<div class=\"header-contact-group\">github.com/x</div>"
        "</header></body></html>"
    )

    assert validate_filler_header_structure(template, dropped) == []
    assert validate_filler_header_structure(template, emptied) == []
    # dropping a FILLED slot is a content loss the coverage gates reject, not
    # this structure check
    errors = validate_filler_header_structure(template, added_node)
    assert errors and "header" in errors[0]


def test_icon_family_rule_applies_when_fa_classes_are_used() -> None:
    html = '<html><head><style>.a { color: red; }</style></head><body><i class="fa-brands fa-linkedin"></i></body></html>'
    patched = _ensure_icon_font_family(html)
    assert "Font Awesome 6 Brands" in patched
    # owner finding 2026-09-09: the <i> default italic must be reset
    assert "font-style: normal" in patched
    assert patched.index(".fa-brands") > patched.index("color: red")
    assert _ensure_icon_font_family(patched) == patched  # idempotent
    # no fa classes -> untouched
    plain = "<html><head><style>.a { color: red; }</style></head><body><p>x</p></body></html>"
    assert _ensure_icon_font_family(plain) == plain


def test_source_header_units_collect_every_kind_on_a_multi_kind_line() -> None:
    """E→F generalization freeze (owner ruling 2026-09-10): a single header
    line carrying email + github + linkedin must yield every kind, not just
    the first match. E source: one line, three kinds."""
    source = "Alex Webb\n(000) 000-0000\nexample@example.com github.com/example linkedin.com/in/example"

    units = source_header_units(source)

    kinds = [unit["kind"] for unit in units]
    assert kinds == ["phone", "envelope", "github", "linkedin", "name"]
    contact_units = [unit for unit in units if unit["kind"] in {"envelope", "github", "linkedin"}]
    assert {unit["source_line_id"] for unit in contact_units} == {"L0003"}


def test_template_contamination_exempts_heading_slots_structurally() -> None:
    """E→F third freeze (owner ruling 2026-09-10): heading-slot placeholders
    carry target-measured heading text by design; the exemption is structural
    (data-slot="heading" removed from the target scan), not vocabulary-based.
    Non-heading target facts are still rejected."""
    template = (
        "<html><body><header><div data-slot=\"name\">[NAME]</div></header>"
        "<h2 data-slot=\"heading\">TECHNICAL SKILLS</h2>"
        "<p>Alex Webb</p>"
        "</body></html>"
    )
    target = "Alex Webb\nSUMMARY\nTECHNICAL SKILLS\nEXPERIENCE"

    findings = template_contamination(template, "", target)

    assert findings == [{"origin": "target", "text": "Alex Webb"}], \
        "heading-slot text exempt, non-heading target fact still flagged"


def test_source_coverage_gate_intercepts_missed_heading_fill() -> None:
    """A Filler output that drops a source heading (leaves the slot unfilled)
    is still intercepted by the deterministic coverage gate, independently of
    the contamination exemption."""
    from tests.experiments.fill_plan import analyze_candidate_provenance

    source = "SUMMARY\nProfile text with details"
    missed_heading = "<html><body><p data-source-line=\"L0002\">Profile text with details</p></body></html>"

    analysis = analyze_candidate_provenance(missed_heading, source)

    codes = [finding.get("code") for finding in analysis["findings"]]
    assert "missing_source_content" in codes
    missing = next(finding for finding in analysis["findings"] if finding.get("code") == "missing_source_content")
    assert missing["source_line_id"] == "L0001" and missing["missing_tokens"] == ["summary"]
