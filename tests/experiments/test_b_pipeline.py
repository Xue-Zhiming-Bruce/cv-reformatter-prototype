from __future__ import annotations

from tests.experiments.b_pipeline import (
    FinalReview,
    TemplateReview,
    _ensure_icon_font_family,
    run_reviewer_loop,
    source_header_units,
    template_contamination,
    template_slot_gaps,
    validate_filler_header_structure,
)
from tests.experiments.refinement import HardGateResult

TEMPLATE = (
    "<html><head><style>.name { font-size: 12pt; }</style></head><body>"
    "<header><span data-slot=\"name\">[NAME]</span></header>"
    "<p class=\"name\">[NAME]</p></body></html>"
)
FILLED = TEMPLATE.replace("[NAME]", "Jane Candidate")


def _pass(_round: int, _template: str, _candidate: str) -> HardGateResult:
    return HardGateResult(passed=True)


def _fill(template: str, _context: dict) -> tuple[str, dict[str, int]]:
    return template.replace("[NAME]", "Jane Candidate"), {"total_tokens": 1}


def test_state_carries_forward_between_template_reviews(tmp_path) -> None:
    contexts = []

    def review(context: dict):
        contexts.append(context)
        decision = "revise" if len(contexts) == 1 else "ready_for_audit"
        return TemplateReview(decision=decision, diagnosis=["gap"], changes=["css"], template_html=TEMPLATE), {}

    result = run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Experience", review, _fill, _pass,
        lambda _context: (FinalReview(accepted=True), {}), artifact_root=tmp_path,
    )

    assert result.accepted
    assert contexts[1]["current_template_version"] == 1
    assert contexts[1]["rounds"][0]["decision"] == "revise"
    assert tmp_path.joinpath("context.json").exists()


def test_template_contamination_is_rejected_before_fill() -> None:
    fills = 0

    def review(context: dict):
        contaminated = TEMPLATE.replace("[NAME]", "Target Person")
        template = contaminated if not context["rounds"] else TEMPLATE
        return TemplateReview(decision="ready_for_audit", template_html=template), {}

    def fill(template: str, context: dict):
        nonlocal fills
        fills += 1
        return _fill(template, context)

    result = run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Target Person\nExperience", review, fill, _pass,
        lambda _context: (FinalReview(accepted=True), {}),
    )

    assert result.accepted
    assert fills == 1
    assert result.context["rounds"][0]["gate_failures"][0]["code"] == "template_contamination"


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


def test_template_missing_slot_blocks_filling() -> None:
    fills = 0

    def fill(template: str, context: dict):
        nonlocal fills
        fills += 1
        return _fill(template, context)

    def review(context: dict):
        template = TEMPLATE if context["rounds"] else TEMPLATE.replace(
            '<span data-slot="name">', '<span data-slot="wrong">'
        )
        return TemplateReview(decision="ready_for_audit", template_html=template), {}

    result = run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Experience", review, fill, _pass,
        lambda _context: (FinalReview(accepted=True), {}),
    )

    assert result.accepted
    assert fills == 1  # round 1 never reached the Filler
    first_failure = result.context["rounds"][0]["gate_failures"][0]
    assert first_failure["code"] == "template_missing_slot"
    assert first_failure["details"][0]["detail"] == "no data-slot='name' element in the header"


def test_slot_violations_surface_in_reviewer_context() -> None:
    """Owner finding 2026-09-09 (run 20260909T143203Z): the Reviewer ignored
    gate failures buried in rounds history; the contract must be a top-level
    reviewer context field."""
    contexts = []

    def review(context: dict):
        contexts.append(context)
        template = TEMPLATE if context["rounds"] else TEMPLATE.replace(
            '<span data-slot="name">', '<span data-slot="wrong">'
        )
        return TemplateReview(decision="ready_for_audit", template_html=template), {}

    run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Experience", review, _fill, _pass,
        lambda _context: (FinalReview(accepted=True), {}),
    )

    assert contexts[0]["slot_contract_violations"] == []
    assert contexts[1]["slot_contract_violations"][0]["kind"] == "name"


def test_filler_presentation_change_is_rejected() -> None:
    calls = 0

    def fill(template: str, context: dict):
        nonlocal calls
        calls += 1
        if calls == 1:
            return template.replace("12pt", "13pt").replace("[NAME]", "Jane Candidate"), {}
        return _fill(template, context)

    result = run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Experience",
        lambda _context: (TemplateReview(decision="ready_for_audit", template_html=TEMPLATE), {}),
        fill, _pass, lambda _context: (FinalReview(accepted=True), {}),
    )

    assert result.accepted
    assert result.context["rounds"][0]["filler_attempts"][0]["gate_failures"][0]["code"] == "filler_presentation_changed"


def test_gate_failures_are_available_to_the_next_filler() -> None:
    filler_failures = []

    def fill(template: str, context: dict):
        filler_failures.append(context.get("filler_gate_failures", []))
        return _fill(template, context)

    gate_calls = 0

    def gate(round_number: int, template: str, candidate: str) -> HardGateResult:
        nonlocal gate_calls
        gate_calls += 1
        if gate_calls == 1:
            from tests.experiments.refinement import GateFailure

            return HardGateResult(passed=False, failures=[GateFailure(code="missing_source_content", details=["x"])])
        return _pass(round_number, template, candidate)

    result = run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Experience",
        lambda _context: (TemplateReview(decision="ready_for_audit", template_html=TEMPLATE), {}),
        fill, gate, lambda _context: (FinalReview(accepted=True), {}),
    )

    assert result.accepted
    assert filler_failures[1][0]["code"] == "missing_source_content"
    assert len(result.context["rounds"]) == 1


def test_final_review_may_reopen_once() -> None:
    final_calls = 0

    def final(_context: dict):
        nonlocal final_calls
        final_calls += 1
        return FinalReview(accepted=final_calls == 2, diagnosis=[] if final_calls == 2 else ["header alignment"]), {}

    result = run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Experience",
        lambda _context: (TemplateReview(decision="ready_for_audit", template_html=TEMPLATE), {}),
        _fill, _pass, final,
    )

    assert result.accepted
    assert final_calls == 2
    assert result.context["failed_approaches"][0]["final_review"] == ["header alignment"]


def test_budget_exhaustion_still_runs_a_read_only_final_review() -> None:
    """Owner rule 2026-09-09: even when the Template Reviewer never returns
    ready_for_audit, the last valid render gets one read-only final review;
    budget exhaustion is not quality acceptance."""
    final_calls: list[dict] = []

    def review(_context: dict):
        return TemplateReview(decision="revise", template_html=TEMPLATE), {}

    def final(context: dict):
        final_calls.append(context)
        return FinalReview(accepted=True), {}

    result = run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Experience", review, _fill, _pass, final,
        max_revisions=2,
    )

    assert result.accepted
    assert result.stop_reason == "final_reviewer_accepted"
    assert len(final_calls) == 1
    assert final_calls[0]["phase"] == "budget_exhausted"
    assert final_calls[0]["reviewed_render_version"] == 2
    assert result.context["terminal_final_review"]["phase"] == "budget_exhausted"


def test_budget_exhaustion_rejection_keeps_diagnosis_and_stop_reason() -> None:
    def review(_context: dict):
        return TemplateReview(decision="revise", template_html=TEMPLATE), {}

    result = run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Experience", review, _fill, _pass,
        lambda _context: (FinalReview(accepted=False, diagnosis=["header still one flex row"]), {}),
        max_revisions=1,
    )

    assert not result.accepted
    assert result.stop_reason == "final_reviewer_rejected_after_budget_exhausted"
    assert result.context["unresolved_issues"] == ["header still one flex row"]


def test_rounds_record_reviewed_and_produced_versions(tmp_path) -> None:
    """Owner finding 2026-09-09: round N's diagnosis describes round N-1's
    render while round N's template is the NEXT version; artifacts must make
    the pairing explicit."""

    def review(_context: dict):
        decision = "revise" if not _context["rounds"] else "ready_for_audit"
        return TemplateReview(decision=decision, template_html=TEMPLATE), {}

    rendered: set[int] = set()

    def gate(round_number: int, _template: str, _candidate: str) -> HardGateResult:
        rendered.add(round_number)
        return HardGateResult(passed=True)

    result = run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Experience", review, _fill, gate,
        lambda _context: (FinalReview(accepted=True), {}), artifact_root=tmp_path,
        rendered_rounds=rendered,
    )

    round_1, round_2 = result.context["rounds"][0], result.context["rounds"][1]
    assert round_1["reviewed_render_version"] is None
    assert round_1["reviewed_template_version"] == 0
    assert round_1["produced_template_version"] == 1
    assert round_2["reviewed_render_version"] == 1
    assert round_2["reviewed_template_version"] == 1
    assert round_2["produced_template_version"] == 2
    assert round_1["reviewed_template_sha256"] and round_1["produced_template_sha256"]
    alignment = tmp_path.joinpath("round_2", "review_alignment.json").read_text()
    assert '"reviewed_render_version": 1' in alignment
    assert "produced_template_version" in alignment


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


def test_owner_feedback_reaches_every_review_round() -> None:
    contexts = []

    def review(context: dict):
        contexts.append(context)
        return TemplateReview(decision="ready_for_audit", template_html=TEMPLATE), {}

    run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Experience", review, _fill, _pass,
        lambda _context: (FinalReview(accepted=True), {}),
        owner_feedback=["name must be centered", "icons render italic"],
    )

    assert all(
        context["owner_feedback"] == ["name must be centered", "icons render italic"]
        for context in contexts
    )


def test_budget_exhaustion_without_valid_render_stops_without_final_review() -> None:
    from tests.experiments.refinement import GateFailure

    def review(_context: dict):
        return TemplateReview(decision="revise", template_html=TEMPLATE), {}

    def gate(_round: int, _template: str, _candidate: str) -> HardGateResult:
        return HardGateResult(passed=False, failures=[GateFailure(code="missing_source_content", details="x")])

    result = run_reviewer_loop(
        TEMPLATE, "Jane Candidate", "Experience", review, _fill, gate,
        lambda _context: (_ for _ in ()).throw(AssertionError("no render to audit")),
        max_revisions=1,
    )

    assert not result.accepted
    assert result.stop_reason == "max_template_reviewer_revisions"


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
