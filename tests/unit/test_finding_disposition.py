"""One typed disposition per Reviewer claim, and the render's own inventory.

The six retained Lane A executions in issue #47 spent paid attribution and
Builder turns on differences that were expected: candidate-owned values, reflow
across pages, absent candidate content shapes and matching label geometry. These
tests pin the recorded cases and the routing boundary around them.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from reportlab.pdfgen import canvas

from app.pipeline import defect_ledger, finding_disposition, measurement, repair_policy, service
from app.pipeline.evidence import (
    FINDING_DISPOSITION_SCHEMA_VERSION,
    AttributionRecord,
    DefectFinding,
    FindingDisposition,
    MeasurementRequest,
    MeasurementResult,
)
from app.pipeline.review import E2_IMPROVEMENT_TOLERANCE_PT

#: Resume E's content-free scope, as `service._candidate_review_scope` publishes
#: it: which candidate roles exist, their content/leaf shapes and whether the
#: header contacts are structured. The disposition owner reads nothing else
#: about the candidate.
SCOPE: dict[str, object] = {
    "available_section_roles": ["work_experience", "skills", "education"],
    "content_kinds_by_role": {
        "work_experience": ["entries"], "skills": ["item_list"], "education": ["entries"],
    },
    "available_leaf_kinds_by_role": {
        "work_experience": ["work_entry", "work_bullet"],
        "skills": ["skill_group"],
        "education": ["education_entry"],
    },
    "target_labels_without_candidate_content": ["LANGUAGES"],
    "has_language_proficiency_entries": False,
    "has_structured_header_contacts": True,
    "skills_only_skill_group": True,
}

CATALOG: list[dict[str, object]] = [
    {"region_id": "header.01", "kind": "header_row", "source": ["contact"]},
    {"region_id": "header.02", "kind": "header_row", "source": ["contact"], "slots": ["name"]},
    {"region_id": "section.01", "kind": "section", "source": ["summary"]},
    {"region_id": "section.02", "kind": "section", "source": ["work_experience"]},
    {"region_id": "section.02.list", "kind": "list_row", "source": ["work_experience"]},
    {"region_id": "section.03", "kind": "section", "source": ["skills"]},
    # A target section the candidate has no content for at all.
    {"region_id": "section.04", "kind": "section", "source": ["additional_details"]},
    # A target block the pair maps to no candidate source: the target's own
    # content, structurally known, with no candidate role behind it.
    {"region_id": "section.05", "kind": "section", "source": []},
]


def finding(
    finding_id: str,
    region: str,
    dimension: str,
    *,
    claim: str = "presentation_property",
    page: int = 1,
    observation: str = "",
    confidence: float = 0.8,
    metric: str = "role_gap",
) -> DefectFinding:
    return DefectFinding(
        finding_id=finding_id,
        target_version="target-v1",
        render_version="render-v1",
        page=page,
        region=region,
        observation=observation or f"Observed {dimension} mismatch",
        suspected_dimension=dimension,
        claimed_difference=claim,
        requested_measurement=MeasurementRequest(
            request_id=f"measure-{finding_id}", metric=metric, page=page,
            intent="compare the recorded regions on the two final PDFs",
        ),
        severity="medium",
        confidence=confidence,
        reviewer="llm",
    )


def dispose(
    item: DefectFinding,
    pages: dict[str, list[int]],
    scope: dict[str, object] | None = None,
    result: MeasurementResult | None = None,
) -> FindingDisposition:
    return finding_disposition.dispose_finding(
        item,
        candidate_scope=finding_disposition.disposition_scope(scope or SCOPE, CATALOG),
        output_section_pages=pages,
        measurement=result,
    )


# Pages of a render that reflowed: the summary and the skills section are on
# page 1, work experience spills onto page 2.
PAGES = {"header.01": [1], "section.01": [1], "section.02": [1, 2], "section.03": [1]}


def test_recorded_content_value_claims_are_expected_variations() -> None:
    """Case 1/4: candidate-owned item text and language values differ from the
    target's by design; the disposition says so and stays off the paid path."""
    item_text = finding(
        "f1", "section.02.list", "list_item_text", claim="content_value",
        observation="The bullet after the second role reads differently from the target's.",
    )
    disposed = dispose(item_text, PAGES)
    assert disposed.kind == "expected_variation"
    assert disposed.reason == "candidate_content_owned"
    assert disposed.schema_version == FINDING_DISPOSITION_SCHEMA_VERSION
    assert "candidate_role:work_experience:content_kinds=entries:leaf_kinds=work_entry/work_bullet" in disposed.evidence
    assert "output_section_pages:section.02=[1, 2]" in disposed.evidence

    language_values = finding("f2", "section.04", "content", claim="content_value")
    disposed_languages = dispose(language_values, PAGES)
    assert disposed_languages.kind == "expected_variation"
    assert disposed_languages.reason == "target_section_without_candidate_content"

    # A header row whose candidate contacts are one flat joined string cannot
    # express per-field labels at all.
    flat_header = dispose(
        finding("f3", "header.01", "contact_detail", claim="content_value"),
        PAGES,
        scope={**SCOPE, "has_structured_header_contacts": False},
    )
    assert flat_header.kind == "expected_variation"
    assert flat_header.reason == "representation_limit:flat_candidate_contact"
    # ...and with structured contacts the same claim needs its own measurement.
    assert dispose(
        finding("f4", "header.01", "contact_detail", claim="content_value"), PAGES
    ).reason == "header_contact_values_need_the_declared_measurement"
    # The candidate name is one atomic string, so a name-row content claim is a
    # representation limit rather than a candidate content change.
    atomic_name = dispose(
        finding("f5", "header.02", "name_layout", claim="content_value"), PAGES
    )
    assert atomic_name.kind == "expected_variation"
    assert atomic_name.reason == "representation_limit:atomic_candidate_name"


def test_recorded_page_placement_is_reflow_not_missing_content() -> None:
    """Case 2: the language row carries onto page 3 of the render while the
    target holds it on page 2 — a page-local absence claim is reflow."""
    reflowed = {"section.03": [1], "section.05": [3]}
    page_local = finding(
        "f1", "section.05", "pagination", claim="page_placement", page=2,
        observation="The languages line is missing from page 2.",
    )
    disposed = dispose(page_local, reflowed)
    assert disposed.kind == "expected_variation"
    assert disposed.reason == "page_local_global_missing_section_assertion"
    assert "output_section_pages:section.05=[3]" in disposed.evidence
    # A claim the inventory disproves is still expected, for its own reason.
    assert dispose(
        finding("f2", "section.05", "pagination", claim="page_placement", page=3), reflowed
    ).reason == "page_placement_disproved_by_output_inventory"
    # A section carried NOWHERE in the output is never disposed as reflow.
    missing = dispose(
        finding("f3", "section.03", "pagination", claim="page_placement", page=2),
        {"section.05": [1]},
    )
    assert missing.kind == "unresolved"
    assert missing.reason == "section_absent_from_whole_output"


def test_only_the_measurement_confirms_a_matching_claim() -> None:
    """Case 3/4: a measured zero delta closes the claim it measured. A claim
    LABEL is never evidence on its own, so `matching` stays payable until a
    measurement of that property agrees."""
    label_presence = finding(
        "f1", "header.01", "contact_label_presence", metric="header_contact_label_geometry"
    )
    measured_equal = MeasurementResult(
        request_id="measure-f1", status="confirmed", target_value_pt=0.0,
        current_value_pt=0.0, delta_pt=0.0,
    )
    disposed = dispose(label_presence, PAGES, result=measured_equal)
    assert disposed.kind == "confirmed_matching"
    assert disposed.reason == "measured_property_matches_within_tolerance"
    assert "measurement:measure-f1:delta_pt=0.0" in disposed.evidence
    # A real difference beyond tolerance is the one measurement verdict that
    # still reaches its checks.
    measured_off = measured_equal.model_copy(update={"delta_pt": E2_IMPROVEMENT_TOLERANCE_PT * 4})
    assert dispose(label_presence, PAGES, result=measured_off).kind == "suspected_defect"
    assert find_disposition_reason(label_presence, PAGES, measured_off) == (
        "measured_property_differs_beyond_tolerance"
    )
    # Same claim, same region, with nothing but the label to go on.
    unmeasured = finding("f2", "header.01", "contact_row_layout", claim="matching")
    matching = dispose(unmeasured, PAGES)
    assert matching.kind == "unresolved"
    assert matching.reason == "matching_claim_needs_its_measurement"
    assert [finding_disposition.payable([unmeasured], {"f2": matching})] == [[unmeasured]]
    # The measurement it needed is what confirms it.
    confirmed = dispose(unmeasured, PAGES, result=measured_equal.model_copy(
        update={"request_id": "measure-f2"}
    ))
    assert confirmed.kind == "confirmed_matching"
    assert confirmed.reason == "measured_property_matches_within_tolerance"


def find_disposition_reason(
    item: DefectFinding, pages: dict[str, list[int]], result: MeasurementResult
) -> str:
    return dispose(item, pages, result=result).reason


def test_content_value_claim_is_never_upgraded_to_a_defect_by_a_measurement() -> None:
    """A delta proves two values differ, never that the candidate's own value is
    wrong: the content decision owns a content claim end to end."""
    values = finding("f1", "section.02.list", "list_item_text", claim="content_value")
    measured_off = MeasurementResult(
        request_id="measure-f1", status="confirmed",
        target_value_pt=0.0, current_value_pt=42.0, delta_pt=42.0,
    )
    disposed = dispose(values, PAGES, result=measured_off)
    assert disposed.kind == "expected_variation"
    assert disposed.reason == "candidate_content_owned"
    assert "measurement:measure-f1:status=confirmed" in disposed.evidence


def test_disposition_reads_structured_evidence_not_confidence_or_prose() -> None:
    """Two claims that differ only in model confidence and observation prose get
    the same disposition: nothing here parses the Reviewer's wording."""
    claim = "The target's second bullet looks 2pt tighter than the render's."
    paraphrase = (
        "Bullet two of the experience section is DRASTICALLY compressed; "
        "please compare the leading and the hanging indent."
    )
    first = finding(
        "f1", "section.02.list", "line_spacing",
        observation=claim, confidence=0.05,
    )
    second = finding(
        "f2", "section.02.list", "line_spacing",
        observation=paraphrase, confidence=0.99,
    )
    assert dispose(first, PAGES) == dispose(second, PAGES)
    # An unverifiable presentation claim stays payable, never silently dropped.
    assert dispose(first, PAGES).kind == "unresolved"
    assert dispose(first, PAGES).reason == "presentation_property_needs_its_measurement"


def inventory_state(*labels: str) -> SimpleNamespace:
    """One section node plus its measured heading label per label, in order."""
    nodes = []
    for index, label in enumerate(labels, start=1):
        section = f"section.{index:02d}"
        nodes.extend([
            SimpleNamespace(
                node_id=section, kind="section", parent_id=None,
                reading_order=2 * index - 1, label=None,
            ),
            SimpleNamespace(
                node_id=f"{section}.heading", kind="heading", parent_id=section,
                reading_order=2 * index, label=label,
            ),
        ])
    return SimpleNamespace(nodes=nodes)


def inventory_pdf(path: Path, pages: list[list[str]]) -> Path:
    document = canvas.Canvas(str(path), pagesize=(612, 792))
    for lines in pages:
        document.setFont("Helvetica", 10)
        for offset, text in enumerate(lines):
            document.drawString(72, 720 - 60 * offset, text)
        document.showPage()
    document.save()
    return path


def test_output_inventory_locates_each_section_on_the_renders_own_pages(tmp_path: Path) -> None:
    """The render's section/page map comes from the render's own PDF. A later
    section's heading does not end the earlier one on the page they share: that
    page still carries the earlier section above the new heading."""
    pdf = inventory_pdf(tmp_path / "render.pdf", [
        ["SUMMARY", "EXPERIENCE", "body line of page 1"],
        ["body line of page 2", "EDUCATION", "body line of page 2b"],
        ["body line of page 3"],
    ])
    # EXPERIENCE runs onto page 2, where EDUCATION starts partway down, and
    # EDUCATION runs through page 3; the LANGUAGES heading starts no line at
    # all, so section.04 is absent rather than invented.
    assert measurement.section_page_locations(
        pdf, inventory_state("Summary", "Experience", "Education", "Languages")
    ) == {"section.01": [1], "section.02": [1, 2], "section.03": [2, 3]}


def test_output_inventory_spans_two_sections_sharing_one_page(tmp_path: Path) -> None:
    """Both sections of a one-page render cover that page, and nothing else."""
    pdf = inventory_pdf(tmp_path / "render.pdf", [["SUMMARY", "SKILLS"]])
    assert measurement.section_page_locations(
        pdf, inventory_state("Summary", "Skills")
    ) == {"section.01": [1], "section.02": [1]}


def test_output_inventory_never_pads_the_last_section_past_the_last_page(
    tmp_path: Path,
) -> None:
    """The earlier section runs through the page the next one starts on, and the
    last section stops at the last page that carries content, never one page
    past it."""
    pdf = inventory_pdf(tmp_path / "render.pdf", [
        ["SUMMARY", "body line of page 1"], ["EDUCATION"],
    ])
    assert measurement.section_page_locations(
        pdf, inventory_state("Summary", "Education")
    ) == {"section.01": [1, 2], "section.02": [2]}


def test_region_catalog_reports_each_sides_own_pages() -> None:
    """The catalog carries the target's measured page AND the render's pages, so
    a render that reflowed onto more pages than the target never looks like a
    page the target had."""
    state = SimpleNamespace(nodes=[
        SimpleNamespace(
            node_id="header.01", kind="header_row", parent_id=None, label=None,
            reading_order=1, slots=["name"], fields=[], binding=None,
        ),
        SimpleNamespace(
            node_id="section.01", kind="section", parent_id=None, label=None,
            reading_order=2, slots=[], fields=[],
            binding=SimpleNamespace(sources=["work_experience"]),
        ),
        SimpleNamespace(
            node_id="section.01.heading", kind="heading", parent_id="section.01",
            label="EXPERIENCE", reading_order=3, slots=[], fields=[], binding=None,
        ),
    ])
    catalog = service._e5_region_catalog(
        state,
        {"sidebar_labels": [{"text": "Experience", "page": 1}]},
        {"section.01": [1, 2, 3]},
    )
    by_id = {entry["region_id"]: entry for entry in catalog}
    assert by_id["header.01"]["target_page"] == 1
    assert by_id["header.01"]["render_pages"] == [1]
    assert by_id["section.01"]["target_page"] == 1
    # Page 2 and 3 exist only in the render: no target-derived page is invented.
    assert by_id["section.01"]["render_pages"] == [1, 2, 3]
    # Without the render side the catalog claims no render page at all.
    without_render = service._e5_region_catalog(
        state, {"sidebar_labels": [{"text": "Experience", "page": 1}]}
    )
    assert {entry["region_id"]: entry["render_pages"] for entry in without_render}[
        "section.01"
    ] == []


def test_pagination_note_presents_a_past_the_last_target_page_as_style_reference() -> None:
    """A render page beyond the target's last page is paired with that page as a
    style reference only; the message never calls it the render's own page."""
    message = service._e5_reviewer_message(
        target_version="target-v1",
        render_version="render-v1",
        page=3,
        region_catalog=[],
        review_scope={"max_pages": 3},
        candidate_review_scope=SCOPE,
        lane="a",
        deterministic_presentation_facts={},
        image_pages={"target": None, "render": 3, "target_style_reference": 2},
    )
    assert "STYLE reference only" in message
    assert "page-count differences alone are not defects" in message.casefold()
    assert "reflow, not missing content" in message


def test_disposed_findings_cost_no_attribution_batch_and_no_builder_turn() -> None:
    """The routing boundary: only a payable disposition reaches the paid
    attribution batch and the repair selection."""
    expected = finding("f1", "section.02.list", "list_item_text", claim="content_value")
    real = finding("f2", "section.02.list", "line_spacing")
    dispositions = {
        expected.finding_id: dispose(expected, PAGES),
        real.finding_id: dispose(real, PAGES),
    }
    assert finding_disposition.payable([expected, real], dispositions) == [real]

    measured = {
        expected.finding_id: (
            MeasurementResult(request_id="measure-f1", status="not_measurable"),
            expected.requested_measurement,
        ),
        real.finding_id: (
            MeasurementResult(request_id="measure-f2", status="confirmed", delta_pt=6.0),
            real.requested_measurement,
        ),
    }
    assert repair_policy._e5_attribution_batches([real], measured) == [[real]]
    attribution = AttributionRecord(
        finding_id=real.finding_id, render_version="render-v1",
        measurement_request_id="measure-f2", attribution="render_plan",
        hypothesis_status="confirmed", repair_owner="builder",
        evidence=["measure-f2"], reason="measured spacing differs",
    )
    selected, _ = repair_policy._next_e5_repair_finding(
        [real], measured, [attribution], []
    )
    assert selected is not None and selected[0].finding_id == real.finding_id
    # The disposed finding is not even a candidate: no attribution exists for it,
    # so a Builder turn can never be selected for it.
    assert repair_policy._next_e5_repair_finding(
        [expected], measured, [attribution], []
    )[0] is None


def test_unsupported_but_uncertain_layout_observations_stay_eligible() -> None:
    """An unknown metric is not a disposition: the observation keeps its normal
    checks instead of being dropped."""
    unknown = finding(
        "f1", "section.02.list", "bullet_hanging_indent",
        metric="unsupported", claim="presentation_property",
    )
    disposed = dispose(unknown, PAGES)
    assert disposed.kind == "unresolved"
    assert finding_disposition.payable([unknown], {disposed_finding_id(unknown): disposed}) == [unknown]


def disposed_finding_id(item: DefectFinding) -> str:
    return item.finding_id


def test_real_defects_still_reach_their_checks() -> None:
    """Adversarial negatives: none of them is disposed as expected variation.
    Target-person leakage and hidden identifiers are decided by the privacy and
    content gates before a Reviewer finding exists, so this owner has no path
    for them; what it does decide stays conservative."""
    # G5 label-presence (#48's loop): a real label_mode/label-width mismatch.
    label_geometry = finding(
        "f1", "header.01", "contact_label_presence", metric="header_contact_label_geometry"
    )
    assert dispose(
        label_geometry, PAGES,
        result=MeasurementResult(
            request_id="measure-f1", status="confirmed",
            target_value_pt=0.0, current_value_pt=2.0, delta_pt=2.0,
        ),
    ).kind == "suspected_defect"
    # Style drift in a region whose text matches the candidate's own content.
    style_drift = finding("f2", "section.02.list", "bullet_glyph")
    assert dispose(style_drift, PAGES).kind == "unresolved"
    # Candidate content the render carries NOWHERE is never "candidate owned":
    # the content gate owns the refusal, so the claim stays payable.
    omitted = finding("f3", "section.03", "content_presence", claim="content_value")
    assert dispose(omitted, {"section.01": [1]}).reason == "section_absent_from_whole_output"
    # A content claim about a region nothing in the pair identifies is never
    # read as candidate-owned: the absence of structure is not evidence.
    unknown_region = finding("f4", "section.99", "content", claim="content_value")
    assert dispose(unknown_region, PAGES).kind == "unresolved"
    assert dispose(unknown_region, PAGES).reason == "region_not_in_the_reviewed_pair"
    # The target's own content block, structurally known (a catalogued region
    # the candidate has no role for), IS the retired keyword verdict's home.
    target_block = finding("f4b", "section.05", "content", claim="content_value")
    assert dispose(target_block, PAGES).kind == "expected_variation"
    assert dispose(target_block, PAGES).reason == "target_person_content_presence"
    # A placement claim about a region that is not a section at all.
    not_a_section = finding("f5", "section.99.list", "pagination", claim="page_placement")
    assert dispose(not_a_section, PAGES).reason == "region_is_not_a_render_section"


def test_repeated_expected_variation_keeps_one_ledger_row_without_a_reopen() -> None:
    """A re-observed or paraphrased expected variation is one ledger entry whose
    disposition stays recorded; it never re-enters the paid path."""
    first = finding(
        "f1", "section.02.list", "list_item_text", claim="content_value",
        observation="The first bullet names a different employer.",
    )
    ledger = defect_ledger.DefectLedger()
    entry, action = ledger.observe(first, disposition=dispose(first, PAGES))
    assert action == "new" and entry.disposition is not None
    repeated = first.model_copy(update={
        "finding_id": "f1-repeat",
        "render_version": "render-v2",
        "observation": "Bullet one's employer and the dates under it are not the target's.",
    })
    repeated_disposition = dispose(repeated, PAGES)
    entry, action = ledger.observe(repeated, disposition=repeated_disposition)
    assert action in {"reopen", "reopened"}
    assert len(ledger.entries) == 1
    assert entry.disposition == repeated_disposition
    assert finding_disposition.payable([repeated], {"f1-repeat": repeated_disposition}) == []


def test_ledger_and_residual_keep_the_disposition_decision(tmp_path: Path) -> None:
    """The ledger stores the disposition and the residual line reports it, so a
    disposed observation stays visible and is never read as repaired. A measured
    match (`no_defect`) is readable apart from an unmeasured one (`unresolved`)."""
    expected = finding("f1", "section.02.list", "list_item_text", claim="content_value")
    measured = finding(
        "f2", "header.01", "contact_label_presence", metric="header_contact_label_geometry"
    )
    unmeasured = finding("f3", "section.02.list", "bullet_glyph")
    ledger = defect_ledger.DefectLedger()
    dispositions = {
        expected.finding_id: dispose(expected, PAGES),
        measured.finding_id: dispose(
            measured, PAGES,
            result=MeasurementResult(
                request_id="measure-f2", status="confirmed",
                target_value_pt=0.0, current_value_pt=0.0, delta_pt=0.0,
            ),
        ),
        unmeasured.finding_id: dispose(unmeasured, PAGES),
    }
    for item in (expected, measured, unmeasured):
        ledger.observe(item, disposition=dispositions[item.finding_id])
    lane = SimpleNamespace(
        lane="a",
        lane_dir=tmp_path,
        versions=[],
        findings=[expected, measured, unmeasured],
        ledger=ledger,
        builder_candidates=[],
        lane_budget=SimpleNamespace(to_json=lambda: {}),
    )
    service._e5_checkpoint(lane, "review")
    residuals = {
        item["finding_id"]: item
        for item in (
            json.loads(line)
            for line in (tmp_path / "residuals.jsonl").read_text().splitlines()
        )
    }
    assert residuals["f1"]["disposition_kind"] == "expected_variation"
    assert residuals["f1"]["claimed_difference"] == "content_value"
    assert residuals["f1"]["disposition_reason"] == "candidate_content_owned"
    assert residuals["f1"]["repair_attempted"] is False
    # no_defect vs not measured stays machine-readable.
    assert residuals["f2"]["disposition_kind"] == "confirmed_matching"
    assert residuals["f2"]["disposition_reason"] == "measured_property_matches_within_tolerance"
    assert residuals["f3"]["disposition_kind"] == "unresolved"
    assert residuals["f3"]["disposition_reason"] == "presentation_property_needs_its_measurement"
    # The stored ledger entry keeps every value the disposition carried.
    stored = json.loads(ledger.entries[defect_ledger._ledger_key(expected)].model_dump_json())
    assert stored["disposition"]["kind"] == "expected_variation"
    assert stored["disposition"]["evidence"] == dispositions["f1"].evidence


def test_agentic_identity_names_the_disposition_policy() -> None:
    """A changed disposition rule never inherits an approval made under the old
    one: the policy version is part of the agentic proof identity."""
    from app.generation import agentic

    assert finding_disposition.DISPOSITION_POLICY_VERSION == FINDING_DISPOSITION_SCHEMA_VERSION
    assert (
        agentic.renderer_contract()["finding_disposition_policy"]
        == finding_disposition.DISPOSITION_POLICY_VERSION
    )
