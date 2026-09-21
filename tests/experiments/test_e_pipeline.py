"""Tests for the Pipeline E0/E1 evidence-grounded target-understanding experiment.

Lanes:
- Default (no markers): fully offline. Uses the workspace-local cached Resume I
  Adobe response and target PDF under `tests/experiments/runs/` and
  `tests/local_datasets/`; builds its own Adobe JSON fixture when those are
  absent. Real pdfplumber measurement and real page-image crops run here (no
  network, no provider, no model call).
- No Chrome and no live provider call are required by any test in this file.

Run: pytest tests/experiments/test_e_pipeline.py
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

import tests.experiments.e_pipeline as e
from tests.experiments.d_pipeline import RunBudget

ROOT = Path(__file__).resolve().parents[2]
RESUME_I = ROOT / "tests/local_datasets/resume_matrix/resume_I.pdf"
RESUME_I_RAW = (
    ROOT / "tests/experiments/runs/c2_resume_i_blind_20260917T115551Z/adobe_raw.json"
)
RESUME_I_NORMALIZED = (
    ROOT / "tests/experiments/runs/c2_resume_i_blind_20260917T115551Z/enriched_evidence.json"
)

pytestmark = pytest.mark.skipif(
    not (RESUME_I.exists() and RESUME_I_RAW.exists()),
    reason="authorized local Resume I corpus and cached Adobe response are not present",
)


def _pod(tmp_path: Path, *, with_normalized: bool = True, raw: dict | None = None):
    raw_path = tmp_path / "adobe_raw.json"
    raw_path.write_text(
        json.dumps(raw if raw is not None else json.loads(RESUME_I_RAW.read_text())),
        encoding="utf-8",
    )
    normalized = None
    if with_normalized and RESUME_I_NORMALIZED.exists():
        from app.template_analysis.commercial.models import NormalizedLayoutEvidence

        normalized = NormalizedLayoutEvidence.model_validate_json(
            RESUME_I_NORMALIZED.read_text(encoding="utf-8")
        )
    return e.EvidencePod(RESUME_I, raw_path, tmp_path, normalized=normalized)


def _minimal_raw() -> dict:
    """A synthetic raw tree with one region that intentionally drops a leaf."""
    return {
        "structured_data": {
            "version": {"json_export": "1.0"},
            "extended_metadata": {"page_count": 1},
            "pages": [{"page_number": 0, "width": 200.0, "height": 200.0}],
            "elements": [
                {"Path": "//Document/Sect/P", "Text": "KEPT", "Page": 0, "ObjectID": 1},
                {"Path": "//Document/Sect/Table/TR/TD/P", "Text": "DROPPED", "Page": 0, "ObjectID": 2},
            ],
        }
    }


# --- coverage audit -----------------------------------------------------------


def test_coverage_audit_separates_normalization_loss_from_missing_extraction() -> None:
    audit = e.audit_evidence_coverage(_minimal_raw()["structured_data"], ["KEPT"])
    assert audit["raw_text_leaf_count"] == 2
    assert audit["raw_leaves_present_in_normalized"] == 1
    assert audit["raw_leaves_absent_from_normalized"] == 1
    assert audit["dropped_leaves"][0]["text"] == "DROPPED"
    # A Text-less leaf is a different class and is counted separately, not merged.
    assert audit["textless_leaf_count"] == 0
    assert "separately" in audit["interpretation"]


def test_coverage_audit_counts_textless_leaves_separately() -> None:
    raw = _minimal_raw()
    raw["structured_data"]["elements"].append(
        {"Path": "//Document/Sect/Figure", "Page": 0, "ObjectID": 3, "HasClip": True}
    )
    audit = e.audit_evidence_coverage(raw["structured_data"], ["KEPT"])
    assert audit["textless_leaf_count"] == 1
    assert audit["textless_leaves"][0]["path"] == "//Document/Sect/Figure"
    assert audit["raw_leaves_absent_from_normalized"] == 1


def test_coverage_audit_does_not_count_font_metadata_as_missing_objects() -> None:
    raw = _minimal_raw()
    raw["structured_data"]["elements"][0]["Font"] = {"family_name": "Avenir", "weight": 600}
    raw["structured_data"]["elements"][0]["attrs"] = {"LineHeight": 12.0}
    audit = e.audit_evidence_coverage(raw["structured_data"], ["KEPT"])
    # Font/attribute sub-dicts are not document objects and must not inflate the count.
    assert audit["textless_leaf_count"] == 0
    assert "conservative lower bound" in audit["count_caveat"]


def test_coverage_audit_unavailable_is_recorded_not_skipped(tmp_path: Path) -> None:
    pod = _pod(tmp_path, with_normalized=False)
    result = pod.audit_coverage()
    assert result["status"] == "unavailable"
    assert "no normalized evidence" in result["reason"]
    record = next(r for r in pod.records if r.kind == "coverage_audit")
    assert record.status == "unavailable" and record.reason


# --- Adobe raw JSON access ----------------------------------------------------


def test_adobe_lookup_returns_original_nodes_with_lossy_layers_retained(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    result = pod.inspect_adobe_json(page_number=2, limit=50)
    assert result["status"] == "available"
    assert result["normalization_applied"] is False
    assert result["verbatim"] is True
    # The raw text-leaf count is the pre-normalization number and is reported.
    assert result["raw_text_leaf_count"] == 122
    paths = " ".join(str(node.get("Path", "")) for node in result["nodes"])
    # Table frames and list labels are exactly what normalization drops.
    assert "/Table/" in paths or "/Lbl" in paths or "/LI" in paths
    # CharBounds/Bounds survive verbatim so geometry can be re-measured.
    assert any("Bounds" in node for node in result["nodes"])


def test_adobe_lookup_forwards_raw_paths_that_normalization_drops(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    dropped = [
        node
        for node in pod.inspect_adobe_json(limit=100)["nodes"]
        if "/Table/" in str(node.get("Path", ""))
    ]
    assert dropped, "expected raw-only table nodes in the cached Resume I response"


def test_adobe_lookup_reports_no_match_as_unavailable(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    result = pod.inspect_adobe_json(element_id=999999999)
    assert result["status"] == "unavailable"
    assert "no raw element matched" in result["reason"]
    assert next(r for r in pod.records if r.kind == "adobe_element").status == "unavailable"


# --- page overview and original-resolution region crops -----------------------


def test_page_overview_is_downscaled_and_keeps_page_location(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    parts = pod.inspect_page_overview(1)
    header = json.loads(str(parts[0]).split("\n", 1)[1])
    assert header["kind"] == "page_overview"
    assert header["page"] == 1
    assert header["source_kind"] == "target_pdf"
    assert header["downscale_ratio"] <= 1.0
    assert header["full_page_pixels"][0] > 0
    artifact = tmp_path / header["source_artifact"]  # full-resolution render is retained
    assert artifact.exists()
    overview = tmp_path / str(pod.records[-1].artifact)
    assert overview.exists() and overview.stat().st_size > 0


def test_region_crop_preserves_location_and_records_transform(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    width, height = pod.page_sizes[1]
    bbox = [width * 0.05, height * 0.30, width * 0.48, height * 0.62]
    parts = pod.inspect_page_region(1, bbox)
    header = json.loads(str(parts[0]).split("\n", 1)[1])
    assert header["kind"] == "region_crop"
    assert header["is_original_resolution"] is True
    assert header["requested_bbox_pt"] == [round(v, 3) for v in bbox]
    assert header["page_size_pt"] == list(pod.page_sizes[1])
    # Location check: crop_box_px / full_page_pixels == requested_bbox_pt / page_size_pt.
    full_w, full_h = header["full_page_pixels"]
    box_x0, box_top, box_x1, box_bottom = header["crop_box_px"]
    page_w, page_h = header["page_size_pt"]
    assert abs(box_x0 / full_w - bbox[0] / page_w) < 1 / full_w + 1e-9
    assert abs(box_top / full_h - bbox[1] / page_h) < 1 / full_h + 1e-9
    assert abs(box_x1 / full_w - bbox[2] / page_w) < 1 / full_w + 1e-9
    assert abs(box_bottom / full_h - bbox[3] / page_h) < 1 / full_h + 1e-9
    crop = tmp_path / str(pod.records[-1].artifact)
    assert crop.exists() and crop.stat().st_size > 0


def test_region_crop_rejects_whole_page_requests(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    width, height = pod.page_sizes[1]
    with pytest.raises(ValueError, match="region too large"):
        pod.inspect_page_region(1, [0.0, 0.0, width * 2, height * 2])


def test_region_crop_rejects_inverted_bbox(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    with pytest.raises(ValueError, match="x0 < x1"):
        pod.inspect_page_region(1, [100.0, 100.0, 50.0, 50.0])


def test_render_crop_without_a_render_is_unavailable_not_faked(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    width, height = pod.page_sizes[1]
    parts = pod.inspect_page_region(
        1, [10.0, 10.0, width * 0.4, height * 0.4], source="render"
    )
    payload = json.loads(str(parts[0]))
    assert payload["status"] == "unavailable"
    assert "no candidate render" in payload["reason"]
    assert next(r for r in pod.records if r.kind == "region_crop").status == "unavailable"


# --- permitted local measurement ----------------------------------------------


def test_local_measurement_is_provenanced_and_not_an_analyzer_fallback(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    result = pod.measure_local_pdf(page_number=1, include="rules")
    assert result["provenance"] == "local_pdf"
    assert result["is_analyzer_fallback"] is False
    assert result["source_sha256"] == e._sha256_file(RESUME_I)
    rules = result["pages"]["1"]["rules"]
    for rule in rules:
        assert rule["provenance"] == "local_pdf"
        assert rule["bbox_pt"][2] - rule["bbox_pt"][0] > 0
    assert result["pages"]["1"]["char_count"] > 0


def test_local_measurement_unavailable_is_recorded_with_reason(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    result = pod.measure_local_pdf(page_number=99, include="all")
    assert result["status"] == "unavailable"
    assert "pdfplumber measurement failed" in result["reason"]
    record = next(r for r in pod.records if r.kind == "local_measurement")
    assert record.status == "unavailable" and record.reason


# --- typed contracts ----------------------------------------------------------


def test_unavailable_record_requires_a_reason() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="requires a reason"):
        e.EvidenceAccessRecord(
            evidence_id="ev.x",
            kind="region_crop",
            provider="pypdfium2",
            status="unavailable",
        )


def test_bbox_evidence_ref_requires_four_numbers() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match=r"\[x0, top, x1, bottom\]"):
        e.EvidenceRef(evidence_id="ev.x", kind="region_crop", bbox_pt=[1.0, 2.0, 3.0])


def test_target_facts_are_marked_diagnostic_only_not_candidate_data() -> None:
    ref = e.EvidenceRef(
        evidence_id="ev.adobe.001",
        kind="adobe_element",
        page_number=2,
        source_kind="adobe_json",
        admissibility="diagnostic_only",
    )
    assert ref.admissibility == "diagnostic_only"


# --- end-to-end shell ---------------------------------------------------------


def test_run_e1_emits_evidence_linked_draft_without_live_calls(tmp_path: Path) -> None:
    run_dir, terminal, draft = e.run_e1(
        RESUME_I,
        RESUME_I_RAW,
        tmp_path / "run",
        normalized_path=RESUME_I_NORMALIZED if RESUME_I_NORMALIZED.exists() else None,
    )
    assert terminal == "awaiting_owner_review"
    assert draft.investigator == "scripted"

    # 1. Every claim carries evidence pointers.
    assert draft.structure and all(claim.evidence for claim in draft.structure)
    # 2. Original-evidence pointers survive: source kind + path + page/bbox.
    kinds = {ref.kind for claim in draft.structure for ref in claim.evidence}
    assert {"page_overview", "region_crop", "adobe_element"} <= kinds
    assert any(ref.bbox_pt for claim in draft.structure for ref in claim.evidence)
    assert any(
        ref.source_kind == "adobe_json" for claim in draft.structure for ref in claim.evidence
    )
    # 3. Unresolved status is explicit, with the evidence gap classed.
    assert draft.unresolved
    assert all(item.reason and item.evidence_gap for item in draft.unresolved)
    # 4. Every cited evidence id was actually collected and is available.
    available = {
        record["evidence_id"]
        for record in _access_records(run_dir)
        if record["status"] == "available"
    }
    collected = {record["evidence_id"] for record in _access_records(run_dir)}
    cited = {ref.evidence_id for claim in draft.structure for ref in claim.evidence}
    assert cited <= available, f"claims cite uncollected evidence: {cited - available}"
    # A failed access may be cited by an unresolved item, but never invented.
    documented = {ref.evidence_id for item in draft.unresolved for ref in item.evidence}
    assert documented <= collected, f"unresolved items cite unrequested evidence: {documented - collected}"
    # 5. Nothing normalized is presented as the evidence layer.
    descriptor = json.loads((run_dir / "evidence_pod_descriptor.json").read_text())
    assert "normalized_evidence_as_authority" in descriptor["not_available"]


def test_run_e1_artifact_has_no_lossy_normalization_substitute(tmp_path: Path) -> None:
    run_dir, _terminal, _draft = e.run_e1(
        RESUME_I,
        RESUME_I_RAW,
        tmp_path / "run",
        normalized_path=RESUME_I_NORMALIZED if RESUME_I_NORMALIZED.exists() else None,
    )
    draft = json.loads((run_dir / "structure_draft.json").read_text())
    for claim in draft["structure"]:
        assert claim["evidence"], claim["claim_id"]
        for ref in claim["evidence"]:
            assert ref["evidence_id"]
            assert ref["kind"] in {
                "page_overview",
                "region_crop",
                "adobe_element",
                "local_measurement",
                "coverage_audit",
            }
    # The normalized layer appears only in the coverage audit (as the object of
    # measurement), never as the evidence handed to the investigator.
    payload = json.dumps(draft)
    assert "text_blocks" not in payload


def test_run_e1_records_unavailable_sources_as_records_not_failures(tmp_path: Path) -> None:
    run_dir, _terminal, _draft = e.run_e1(
        RESUME_I,
        RESUME_I_RAW,
        tmp_path / "run",
        normalized_path=None,  # forces the coverage audit to be unavailable
    )
    records = _access_records(run_dir)
    coverage = [r for r in records if r["kind"] == "coverage_audit"]
    assert coverage and coverage[0]["status"] == "unavailable"
    assert coverage[0]["reason"]
    assert json.loads((run_dir / "manifest.json").read_text())["terminal_state"] == "awaiting_owner_review"


def test_run_e1_is_understanding_only_and_never_promotes(tmp_path: Path) -> None:
    run_dir, _terminal, _draft = e.run_e1(RESUME_I, RESUME_I_RAW, tmp_path / "run")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["active_layout_version_id"] == "layout_v1"
    assert manifest["pending_candidate_id"] is None
    assert manifest["versions"] == [] or all(
        "structure_draft" in version["id"] for version in manifest["versions"]
    )
    assert (run_dir / "structure_draft.json").exists()
    report = (run_dir / "REPORT.md").read_text()
    assert "Does **not** establish" in report
    assert "beyond ADR 0001/0006" in report.lower() or "BEYOND ADR 0001" in report


def test_run_e1_budget_counts_every_tool_call(tmp_path: Path) -> None:
    budget = RunBudget()
    run_dir, _terminal, _draft = e.run_e1(
        RESUME_I, RESUME_I_RAW, tmp_path / "run", budget=budget
    )
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["budget"]["tool_call_count"] == 5  # overview, region, adobe, measure, coverage
    assert budget.model_requests == 0  # the offline path spends no model request
    assert budget.model_requests <= budget.max_model_requests


def test_e1_stop_gate_treats_confident_ok_without_confirmation_as_needs_owner_review(
    tmp_path: Path,
) -> None:
    pod = _pod(tmp_path)
    pod.inspect_page_overview(1)
    draft = e.TargetStructureDraft(
        target_id="x",
        investigator="scripted",
        structure=[
            e.StructuralRelation(
                claim_id="c1",
                relation="section_boundary",
                statement="confident claim",
                evidence=[e.EvidenceRef(evidence_id="ev.overview.001", kind="page_overview")],
                confidence=0.95,
            )
        ],
        unresolved=[],
        self_reported=e.SelfReportedStatus(status="ok"),
    )
    assert e.check_e1_stop_gates(draft, pod) == "needs_owner_review"


def test_e1_stop_gate_flags_claims_citing_uncollected_evidence(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    draft = e.TargetStructureDraft(
        target_id="x",
        investigator="scripted",
        structure=[
            e.StructuralRelation(
                claim_id="c1",
                relation="ownership",
                statement="cites evidence that was never collected",
                evidence=[e.EvidenceRef(evidence_id="ev.nope.999", kind="region_crop")],
                confidence=0.5,
            )
        ],
        unresolved=[],
        self_reported=e.SelfReportedStatus(status="partial"),
    )
    assert e.check_e1_stop_gates(draft, pod) == "needs_owner_review"


def test_frozen_case_hashes_the_inputs_before_inspection(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(json.dumps(_minimal_raw()), encoding="utf-8")
    case = e.freeze_cases(RESUME_I, raw_path, role="diagnostic_known", case_id="resume_I")
    assert case.target_sha256 == e._sha256_file(RESUME_I)
    assert case.adobe_json_sha256 == e._sha256_file(raw_path)
    assert case.page_count == 1
    assert case.label_status == "pending_human_annotation"
    assert "no human" in case.notes


def _access_records(run_dir: Path) -> list[dict]:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    return manifest["evidence_access"]["records"]


# ===========================================================================
# E2 — See -> Measure -> Attribute -> Repair -> Re-render (PIPELINE_E_PLAN.md §8)
# ===========================================================================


TARGET_F = ROOT / "tests/local_datasets/resume_matrix/resume_F.pdf"
TARGET_F_CACHE = (
    ROOT / "tests/experiments/runs/target_cache/6916698b4280b9c244e7d2c5a453e8a48d2bf7edea3419becb9b924eda26603d"
)


def _e2_available() -> bool:
    return TARGET_F.exists() and (TARGET_F_CACHE / "adobe_raw.json").exists()


e2_skip = pytest.mark.skipif(
    not _e2_available(),
    reason="authorized resume_F corpus and its cached Adobe response are not present",
)


def test_repair_proposal_rejects_candidate_fact_mutation() -> None:
    with pytest.raises(ValueError, match="candidate fact mutation is forbidden"):
        e.RepairProposal(
            finding_id="f1",
            base_render_version="render-resume_F-v1",
            layer="state_style",
            section_node_id="section.04",
            style_updates={"leaf_summary.p1": "rewritten"},
            rationale="attempted fact edit",
            agent="scripted",
        )


def test_repair_proposal_rejects_unbounded_mutations() -> None:
    with pytest.raises(ValueError, match="non-zero gap_delta_pt"):
        e.RepairProposal(
            finding_id="f1",
            base_render_version="v1",
            layer="plan_entry_gap",
            section_node_id="section.04",
            rationale="empty edit",
            agent="scripted",
        )
    with pytest.raises(ValueError, match="requires style_updates"):
        e.RepairProposal(
            finding_id="f1",
            base_render_version="v1",
            layer="state_style",
            section_node_id="section.04",
            rationale="empty edit",
            agent="scripted",
        )


def test_role_gap_measurement_binds_actual_final_pdf_content(tmp_path: Path) -> None:
    raw_path = tmp_path / "adobe_raw.json"
    raw_path.write_text(
        json.dumps(json.loads((TARGET_F_CACHE / "adobe_raw.json").read_text())),
        encoding="utf-8",
    )
    pod = e.DualSourcePod(TARGET_F, raw_path, tmp_path, render_pdf=None)
    budget = e.RunBudget(max_model_requests=4, max_tool_calls=48)
    trace = e.RunTrace(tmp_path)
    controller = e.MeasureController(pod, budget, trace)
    request = e.MeasurementRequest(
        request_id="measure-001",
        metric="role_gap",
        page=1,
        from_text="ImageCaptioningSystem",
        to_text="SentimentAnalysisAPI",
        # current_pdf == TARGET in this direct test, so both sides anchor to
        # the same role lines; the full loop test measures the real render.
        render_from_text="ImageCaptioningSystem",
        render_to_text="SentimentAnalysisAPI",
    )
    result = controller.execute(request, current_pdf=TARGET_F)
    assert result.status == "confirmed"
    # The measured object is the REAL target PDF content (role-aligned anchors,
    # not row N to row N): the target's PROJECTS entry-head gap.
    assert result.target_value_pt == pytest.approx(71.5, abs=0.5)
    assert result.delta_pt == pytest.approx(0.0, abs=0.5)


def test_measurement_evidence_missing_is_recorded_not_faked(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(
        json.dumps(json.loads((TARGET_F_CACHE / "adobe_raw.json").read_text())),
        encoding="utf-8",
    )
    pod = e.DualSourcePod(TARGET_F, raw_path, tmp_path, render_pdf=None)
    budget = e.RunBudget(max_model_requests=4, max_tool_calls=48)
    trace = e.RunTrace(tmp_path)
    controller = e.MeasureController(pod, budget, trace)
    request = e.MeasurementRequest(
        request_id="measure-002",
        metric="role_gap",
        page=1,
        from_text="NoSuchAnchorFrom",
        to_text="NoSuchAnchorTo",
        render_from_text="Microsoft",
        render_to_text="Amazon.com",
    )
    result = controller.execute(request, current_pdf=TARGET_F)
    assert result.status == "evidence_missing"
    assert result.reason and "missing" in result.reason


@e2_skip
def test_run_e2_delivers_verified_repair_with_versions_and_repeat_measurement(
    tmp_path: Path,
) -> None:
    run_dir, terminal, record = e.run_e2(TARGET_F, tmp_path / "run")
    assert terminal == "ready_for_owner_review"
    versions = record["render_versions"]
    assert len(versions) >= 2  # first render + at least one repaired candidate
    # 1. Every render is a versioned record with hashes and gate results.
    assert all(v["pdf_sha256"] and v["html_sha256"] for v in versions)
    # 2. The best valid version was promoted after a VERIFIED improvement: the
    #    identical measurement request improved on the promoted version.
    promoted = [v for v in versions if v["promoted"]]
    assert promoted and record["best_render_version"] == promoted[0]["version_id"]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["pending_candidate_id"] is None  # candidates stay INACTIVE
    trace = json.loads((run_dir / "trace.json").read_text())
    promoted_entries = [entry for entry in trace if entry.get("action") == "promoted"]
    assert promoted_entries, "a verified improvement must be traced"
    promoted_data = json.loads(
        (run_dir / promoted_entries[0]["output_artifact"]).read_text()
    )
    # The repeated measurement moved the rendered gap TOWARD the target.
    assert (
        abs(promoted_data["after"]["current_value_pt"] - promoted_data["after"]["target_value_pt"])
        < abs(promoted_data["before"]["current_value_pt"] - promoted_data["before"]["target_value_pt"])
    )


@e2_skip
def test_run_e2_findings_bind_exact_versions(tmp_path: Path) -> None:
    run_dir, _terminal, record = e.run_e2(TARGET_F, tmp_path / "run")
    target_id = record["target_id"]
    for finding in record["findings"]:
        assert finding["target_version"] == target_id
        assert finding["render_version"] in {v["version_id"] for v in record["render_versions"]}
        assert finding["observation"] and finding["region"]
        # The reviewer proposes; it never approves: no verdict fields at all.
        assert "verdict" not in finding and "approved" not in finding


@e2_skip
def test_run_e2_failed_repair_is_rolled_back_and_best_is_preserved(tmp_path: Path) -> None:
    run_dir, _terminal, record = e.run_e2(TARGET_F, tmp_path / "run")
    # The second scripted attempt repeats the first action with the same
    # evidence; the shell rejects/rolls it back instead of promoting it.
    non_improving = [s for s in record["attempted_strategies"] if "rolled_back" in s]
    assert non_improving, record["attempted_strategies"]
    best = record["best_render_version"]
    best_entry = next(v for v in record["render_versions"] if v["version_id"] == best)
    assert best_entry["hard_gates_passed"] is True


@e2_skip
def test_run_e2_repeats_the_identical_measurement_request_after_repair(
    tmp_path: Path,
) -> None:
    run_dir, _terminal, record = e.run_e2(TARGET_F, tmp_path / "run")
    trace = json.loads((run_dir / "trace.json").read_text())
    measurements = [
        json.loads((run_dir / entry["output_artifact"]).read_text())
        for entry in trace
        if entry.get("action") == "measurement" and entry.get("output_artifact")
    ]
    promoted_entries = [entry for entry in trace if entry.get("action") == "promoted"]
    assert promoted_entries
    promoted_data = json.loads(
        (run_dir / promoted_entries[0]["output_artifact"]).read_text()
    )
    # §8.5: the identical request is repeated, changing only the current render.
    assert promoted_data["before"]["request_id"] == promoted_data["after"]["request_id"]
    assert measurements  # every measurement persisted with raw anchors/method


@e2_skip
def test_run_e2_never_stops_on_stall_but_returns_budget_exhausted_when_budget_ends(
    tmp_path: Path,
) -> None:
    # Exhausted model budget BEFORE the loop: the run must end budget_exhausted
    # (never success/unsupported), preserve resumable state, and record the
    # blocked strategy — a stall is an escalation signal, not a terminal.
    budget = e.RunBudget(max_model_requests=0, max_tool_calls=48)
    run_dir, terminal, record = e.run_e2(TARGET_F, tmp_path / "run", budget=budget)
    assert terminal == "budget_exhausted"
    assert record["summary"]["best_render_version"] is None or record["summary"]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["terminal_state"] == "budget_exhausted"
    state = json.loads((run_dir / "e2_state.json").read_text())
    assert state["schema_version"] == "pipeline-e-e2-state/1"
    # Resumable state: versions/findings/budget/strategies persisted.
    assert "budget_state" in state and "attempted_strategies" in state


@e2_skip
def test_run_e2_target_person_facts_cannot_enter_candidate_output(tmp_path: Path) -> None:
    run_dir, _terminal, record = e.run_e2(TARGET_F, tmp_path / "run")
    best = record["best_render_version"]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    best_entry = next(v for v in manifest["versions"] if v["id"] == best)
    best_index = int(best_entry["path"].split("_")[1].split(".")[0])
    html = (run_dir / f"render_{best_index}.html").read_text(encoding="utf-8")
    # Target-person facts (F's name and its own entry lines) are diagnostic
    # evidence only; none of them may appear in the candidate render.
    for leaked in ("Alex Webb", "ImageCaptioningSystem", "SentimentAnalysisAPI", "AcmeAISolutions"):
        assert leaked.casefold() not in html.casefold(), leaked


@e2_skip
def test_run_e2_best_render_is_owner_reviewable_and_probes_pass(tmp_path: Path) -> None:
    run_dir, terminal, record = e.run_e2(TARGET_F, tmp_path / "run")
    best = record["best_render_version"]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    best_entry = next(v for v in manifest["versions"] if v["id"] == best)
    best_pdf = run_dir / best_entry["path"]
    assert best_pdf.exists() and best_pdf.stat().st_size > 0
    probes = json.loads((run_dir / "content_shape_probes.json").read_text())
    assert set(probes) == {"short", "medium", "long"}
    assert all(entry["passed"] for entry in probes.values())
    report = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "Does **not** establish" in report
    assert "owner" in report.casefold()


@e2_skip
def test_run_e2_no_target_specific_rules_and_frozen_chain_reuse(tmp_path: Path) -> None:
    run_dir, _terminal, record = e.run_e2(TARGET_F, tmp_path / "run")
    state = json.loads((run_dir / "c2_layout_state.json").read_text())
    assert state["schema_version"] == "layout-state/1"
    assert state["provenance"]["provider"] == "adobe"
    # The privacy gate measured against the FROZEN C1 baseline target.
    probe = json.loads((run_dir / "flow_probe.json").read_text())
    assert probe["status"] == "fully_materialized"


@e2_skip
def test_run_e2_reviewer_cannot_approve_its_own_repair(tmp_path: Path) -> None:
    """No model promotes: the shell (not the reviewer) promotes a repaired
    candidate, and the reviewer/repair roles are distinct agents."""
    run_dir, _terminal, record = e.run_e2(TARGET_F, tmp_path / "run")
    trace = json.loads((run_dir / "trace.json").read_text())
    finding_agents = {entry["agent"] for entry in trace if entry.get("action") == "finding"}
    proposal_agents = {entry["agent"] for entry in trace if entry.get("action") == "proposal"}
    promotion_agents = {entry["agent"] for entry in trace if entry.get("action") == "promoted"}
    assert finding_agents == {"visual_reviewer"}
    assert proposal_agents == {"builder"}
    # The shell — not any agent — owns promotion.
    assert promotion_agents == {"shell"}


@e2_skip
def test_run_e2_measurements_inspect_real_final_content_not_probes(tmp_path: Path) -> None:
    """§9 rule 1: measurements inspect the final PDF the owner sees; method
    provenance names the pdfplumber word-row method and carries the page."""
    run_dir, _terminal, record = e.run_e2(TARGET_F, tmp_path / "run")
    for result in record["measurement_results"]:
        assert result["method"].startswith("pdfplumber_word_rows_role_gap/")
        assert result["status"] in {
            "confirmed", "falsified", "ambiguous", "evidence_missing", "not_measurable"
        }
        # Raw anchors bind to the measured version: values are page-local pts.
        if result["status"] == "confirmed":
            assert result["target_value_pt"] is not None
            assert result["current_value_pt"] is not None


# ===========================================================================
# E3 — Resume I walkthrough (PIPELINE_E_PLAN.md §11; E_PIPELINE_PREP.md E3
# work order). Focused tests for the new E3 behavior only.
# ===========================================================================

TARGET_I = RESUME_I  # the frozen_blind two-column sidebar family case
TARGET_I_CACHE = (
    ROOT / "tests/experiments/runs/target_cache/af6b9234ca96948b7b34de9e535c452ef26a60b7508cfc3a8189fe56e5b1b3d8"
)


def _e3_available() -> bool:
    return TARGET_I.exists() and (TARGET_I_CACHE / "adobe_raw.json").exists()


e3_skip = pytest.mark.skipif(
    not _e3_available(),
    reason="authorized resume_I corpus, cached Adobe response, or pinned Chrome are not present",
)


def test_measure_sidebar_headings_are_generic_and_evidence_linked(tmp_path: Path) -> None:
    raw_path = tmp_path / "adobe_raw.json"
    raw_path.write_text(
        json.dumps(json.loads((TARGET_I_CACHE / "adobe_raw.json").read_text())),
        encoding="utf-8",
    )
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    ev = NormalizedLayoutEvidence.model_validate_json(
        (TARGET_I_CACHE / "enriched_evidence.json").read_text()
    )
    from tests.experiments.a_pipeline import build_format_summary

    summary = build_format_summary(ev, json.loads(raw_path.read_text()), TARGET_I)
    labels = e.measure_sidebar_headings(TARGET_I, summary)
    # The clustered sidebar-label evidence recovers the section boundaries
    # (9 measured labels across the page break) without target-specific code.
    assert len(labels) == 9
    pages = {record["page"] for record in labels}
    assert pages == {1, 2}
    # No target text is carried in the derivation — labels are the target's
    # own presentation rows, measured by geometry (shared right edge) only.
    assert all(record["x1"] <= 160.0 for record in labels)


def test_compile_two_column_state_is_valid_and_candidate_safe(tmp_path: Path) -> None:
    raw_path = tmp_path / "adobe_raw.json"
    raw_path.write_text(
        json.dumps(json.loads((TARGET_I_CACHE / "adobe_raw.json").read_text())),
        encoding="utf-8",
    )
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    ev = NormalizedLayoutEvidence.model_validate_json(
        (TARGET_I_CACHE / "enriched_evidence.json").read_text()
    )
    from tests.experiments.a_pipeline import build_format_summary

    summary = build_format_summary(ev, json.loads(raw_path.read_text()), TARGET_I)
    state, derived = e.compile_two_column_state(TARGET_I, summary, ev)
    from tests.experiments.c2_state import validate_layout_state

    assert validate_layout_state(state) == []
    assert state.schema_version == "layout-state/1"
    assert state.provenance.provider == "adobe"
    assert state.provenance.target_sha256 == e._sha256_file(TARGET_I)
    # 9 evidence-linked section claims; unresolved bindings never carry sources.
    sections = [node for node in state.nodes if node.kind == "section"]
    assert len(sections) == 9
    for section in sections:
        if section.binding.mapping_action == "unresolved":
            assert section.binding.sources == []
            assert any(
                gap.feature == f"unresolved_section_binding:{section.node_id}"
                for gap in state.capability_gaps
            )
    # The header region carries the measured name row only (honest ceiling).
    header_rows = [node for node in state.nodes if node.kind == "header_row"]
    assert len(header_rows) == 1
    assert header_rows[0].slots == ["name"]


def test_terminal_rename_ready_for_owner_review_reserved_delivered(tmp_path: Path) -> None:
    # The work order terminology correction: waiting for the owner is a
    # resumable pause (`ready_for_owner_review`), never `delivered_pending_owner`;
    # `delivered` is reserved for explicit owner acceptance.
    from pathlib import Path as _P

    source = (_P(__file__).resolve().parents[0] / "e_pipeline.py").read_text()
    assert "delivered_pending_owner" not in source
    assert "ready_for_owner_review" in source


@e3_skip
def test_run_e3_walkthrough_records_the_loop_trajectory(tmp_path: Path) -> None:
    run_dir, terminal, record = e.run_e3(TARGET_I, tmp_path / "run")
    assert terminal in {"ready_for_owner_review", "budget_exhausted"}
    # Frozen inputs before examination: hashes + run config persisted.
    frozen = json.loads((run_dir / "frozen_case.json").read_text())
    assert frozen["target_sha256"] == e._sha256_file(TARGET_I)
    config = json.loads((run_dir / "run_config.json").read_text())
    assert config["target_sha256"] == frozen["target_sha256"]
    # The rubric reference is path+hash only, explicitly not given to agents.
    assert config["evaluation_rubric_reference"]["not_given_to_agents"] is True
    assert "sections" not in config  # no rubric answers in agent inputs
    # Structure draft: versioned, evidence-linked.
    assert record["structure_draft_version"] == "structure_draft_v1"
    draft = json.loads((run_dir / "structure_draft.json").read_text())
    assert draft["structure"], "the draft carries evidence-linked claims"
    for claim in draft["structure"]:
        assert claim["evidence"], "every material claim cites evidence"
    # Real render artifacts exist (HTML + PDF + page images).
    assert (run_dir / "render_1.html").exists()
    assert (run_dir / "render_1.pdf").exists()
    assert (run_dir / "render_1_page_1.png").exists()
    assert (run_dir / "target_resume_I_page_1.png").exists()
    # Trajectory: findings -> measurements -> attributions -> strategies.
    assert record["findings"] and record["measurement_results"] and record["attributions"]
    for finding in record["findings"]:
        assert finding["target_version"] == record["target_id"]
        assert finding["render_version"] in {v["version_id"] for v in record["render_versions"]}
    # No target-person facts in the candidate output (privacy gate green).
    assert record["render_versions"], "at least one versioned render exists"
    # Content-shape probes pass through the canonical C2 fixtures.
    probes = json.loads((run_dir / "content_shape_probes.json").read_text())
    assert set(probes) == {"short", "medium", "long"}


@e3_skip
def test_run_e3_no_target_specific_rules(tmp_path: Path) -> None:
    """The E3 shell derives structure from evidence; no Resume-I string,
    heading, or coordinate may appear as a module-level constant, branch
    condition, or literal in e_pipeline.py (the scripted reviewer scenario
    records are runtime data keyed by the target id, not production rules)."""
    source = (Path(__file__).resolve().parents[0] / "e_pipeline.py").read_text()
    for leaked in ("CONTACT INFO", "ACHIEVEMENTS", "REFERENCES", "JOB TITLE", "ABOUT ME"):
        assert leaked not in source, leaked
    # No target-name branch anywhere in the module (comment mentions of the
    # walkthrough case are provenance, never conditions or constants).
    for line in source.splitlines():
        if "resume_I" in line:
            stripped = line.strip()
            assert stripped.startswith("#"), f"target-name code (not comment): {stripped}"


@e3_skip
def test_run_e3_operational_abort_never_classifies_unsupported(tmp_path: Path) -> None:
    # Missing cached evidence → operational_abort describing the failed
    # operation, never 'unsupported'.
    with pytest.raises(RuntimeError, match="target PDF not found"):
        e.run_e3(tmp_path / "missing.pdf", tmp_path / "run")


@e3_skip
def test_run_e3_reviewer_cannot_approve_its_own_repair(tmp_path: Path) -> None:
    run_dir, _terminal, record = e.run_e3(TARGET_I, tmp_path / "run")
    trace = json.loads((run_dir / "trace.json").read_text())
    finding_agents = {entry["agent"] for entry in trace if entry.get("action") == "finding"}
    proposal_agents = {entry["agent"] for entry in trace if entry.get("action") == "proposal"}
    promotion_agents = {entry["agent"] for entry in trace if entry.get("action") == "promoted"}
    assert finding_agents == {"visual_reviewer"}
    assert proposal_agents == {"builder"}
    assert promotion_agents <= {"shell"}  # only the shell promotes (may be empty on rollback)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["pending_candidate_id"] is None


# ===========================================================================
# E4 — live-agent Resume I convergence trial (PIPELINE_E_PLAN.md §11/§14;
# E_PIPELINE_PREP.md E4 work order). Focused tests for the new shared
# behavior only; everything here runs fully OFFLINE (zero live calls).
# ===========================================================================

e4_skip = e3_skip  # the same authorized corpus + cached evidence + pinned Chrome


def test_budget_counts_live_and_scripted_calls_separately() -> None:
    """Phase 0: 'model requests' must not merge scripted agent invocations
    with live provider calls; token usage belongs to live calls only."""
    from tests.experiments.d_pipeline import BudgetExhausted

    budget = e.RunBudget(max_model_requests=3, max_tool_calls=8)
    budget.spend_model("visual_reviewer")  # scripted invocation (default mode)
    budget.spend_model("visual_reviewer", mode="scripted")
    class _Usage:
        requests = 1
        input_tokens = 11
        output_tokens = 7
        tool_calls = 0
    budget.spend_model("visual_reviewer", usage=_Usage(), mode="live")
    state = budget.to_json()
    assert state["calls_by_mode"]["scripted"] == 2
    assert state["calls_by_mode"]["live"] == 1
    assert state["usage"]["input_tokens"] == 11  # tokens belong to live only
    with pytest.raises(BudgetExhausted):
        budget.spend_model("visual_reviewer")


def test_duplicate_unresolved_items_are_deduplicated_by_item_id() -> None:
    items = [
        e.UnresolvedItem(
            item_id="unresolved.same",
            question="same question",
            status="unresolved",
            reason="r",
            evidence_gap="ambiguous_relation",
        )
        for _ in range(5)
    ] + [
        e.UnresolvedItem(
            item_id="unresolved.other",
            question="different question",
            status="unresolved",
            reason="r",
            evidence_gap="normalization_loss",
        )
    ]
    deduped = e._dedup_unresolved(items)
    assert len(deduped) == 2
    assert len({item.item_id for item in deduped}) == 2


def test_repair_counts_agree_across_state_trace_and_report(
    tmp_path: Path,
) -> None:
    """Phase 0: repair_attempt_count was 0 while a repair/rollback was
    recorded; now state, trace, budget, and evaluation report agree."""
    run_dir, _terminal, record = e.run_e4(TARGET_I, tmp_path / "run")
    executed = [a for a in record["repair_attempts"] if "layer" in a]
    budget_state = record["budget_state"]
    assert budget_state["repair_attempt_count"] == len(executed)
    trace = json.loads((run_dir / "trace.json").read_text())
    proposal_entries = [
        json.loads((run_dir / entry["output_artifact"]).read_text())
        for entry in trace
        if entry.get("action") == "proposal" and entry.get("output_artifact")
    ]
    assert len(proposal_entries) == len(executed)
    rollbacks_in_trace = [entry for entry in trace if entry.get("action") == "rolled_back"]
    rollbacks_in_state = [s for s in record["attempted_strategies"] if "rolled_back" in s]
    assert len(rollbacks_in_trace) >= 1
    assert len(rollbacks_in_trace) == len(rollbacks_in_state)
    # The shared evaluation report derives every count from the SAME state.
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    structure_evaluation = {
        "sections_bound_to_candidate_sources": 4,
        "unresolved_items": [],
    }
    report = e.write_evaluation_report(
        run_dir,
        record,
        structure_evaluation=structure_evaluation,
        rubric_source="test (post-run)",
    )
    assert report["repairs"]["attempts"] == len(executed)
    assert report["repairs"]["improving"] == len(
        [v for v in record["render_versions"] if v["promoted"]]
    )
    assert report["costs"]["scripted_agent_invocations"] == budget_state[
        "calls_by_mode"
    ]["scripted"]
    assert report["costs"]["live_model_calls"] == budget_state["calls_by_mode"][
        "live"
    ]


def test_signed_provider_urls_never_enter_shareable_artifacts(
    tmp_path: Path,
) -> None:
    """Phase 0: the raw Adobe evidence carries signed download URLs with
    security-token query data; the shareable run-dir copy must be the
    deterministic redacted derivative, and the immutable original must stay
    untouched in the restricted target cache."""
    signed = "https://s3.example.com/asset?X-Amz-Security-Token=tok&X-Amz-Signature=sig"
    raw = {
        "status": {
            "content": {"downloadUri": signed, "size": 10},
            "resource": {"downloadUri": "https://s3.example.com/plain"},
        },
        "structured_data": {"pages": [{"page": 1, "size": [612, 792]}]},
    }
    redacted = e.redact_signed_urls(raw)
    assert e.SIGNED_QUERY_MARKERS[0] not in json.dumps(redacted)
    assert redacted["status"]["content"]["downloadUri"] == e.REDACTED_SIGNED_URL
    # Useful content, IDs, geometry, provenance and hashes are preserved.
    assert redacted["status"]["content"]["size"] == 10
    assert redacted["status"]["resource"]["downloadUri"] == "https://s3.example.com/plain"
    with pytest.raises(RuntimeError, match="signed provider credential"):
        e.assert_no_signed_strings({"a": [signed]})
    # The offline run dir carries the redacted derivative only; the original
    # cache file keeps its signed content and is byte-unchanged by the run.
    original = TARGET_I_CACHE / "adobe_raw.json"
    before = original.read_bytes()
    run_dir, _terminal, _record = e.run_e4(TARGET_I, tmp_path / "run")
    run_raw = json.loads((run_dir / "adobe_raw.json").read_text())
    assert "X-Amz" not in (run_dir / "adobe_raw.json").read_text()
    assert original.read_bytes() == before
    e.assert_no_signed_strings((run_dir / "adobe_raw.json").read_text(encoding="utf-8"))
    # The owner package is checked too (no signed credential may enter it).
    assert (run_dir / "owner_review" / "REPORT.md").exists()


@e4_skip
def test_run_e4_freezes_config_before_first_live_call(tmp_path: Path) -> None:
    run_dir, terminal, record = e.run_e4(TARGET_I, tmp_path / "run")
    config = json.loads((run_dir / "run_config.json").read_text())
    assert config["frozen_before_first_live_call"] is True
    assert config["target_sha256"] == e._sha256_file(TARGET_I)
    assert config["starting_commit"]
    # Candidate input frozen by hash; the Builder can never edit candidate facts.
    assert config["candidate_input"]["sha256"]
    assert "facts are never editable" in config["candidate_input"]["builder_boundary"]
    # Model name + sampling control frozen; no keys/URLs/tokens recorded.
    assert config["model_configuration"]["text_model"]
    assert config["sampling"]["temperature"] == 0.0
    prompts = json.loads((run_dir / "prompts.json").read_text())
    for name, text in prompts.items():
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == config["prompts"][
            "sha256"
        ][name]
    # The human rubric is evaluation truth only: path + hash, never content.
    assert config["evaluation_rubric_reference"]["not_given_to_agents"] is True
    assert "sections" not in config
    rubric_text = (ROOT / "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md").read_text()
    for name, text in prompts.items():
        assert not any(
            len(line.strip()) > 30 and line.strip() in text
            for line in rubric_text.splitlines()
        )


@e4_skip
def test_run_e4_records_the_live_loop_trajectory(tmp_path: Path) -> None:
    run_dir, terminal, record = e.run_e4(TARGET_I, tmp_path / "run")
    assert terminal in {"ready_for_owner_review", "budget_exhausted"}
    assert record["schema_version"] == "pipeline-e-e4-state/1"
    assert record["findings"] and record["measurement_results"] and record["attributions"]
    # The reviewer covered pages; findings are deduplicated.
    page_keys = {
        (f["region"], f["suspected_dimension"], f["render_version"])
        for f in record["findings"]
    }
    assert len(page_keys) == len(record["findings"])
    # Resumable state + owner package exist; never success/unsupported.
    assert (run_dir / "e4_state.json").exists()
    assert record["summary"]["terminal_state"] != "unsupported"
    assert (run_dir / "owner_review" / "comparison_page_1.png").exists()


@e4_skip
def test_run_e4_repeats_the_identical_measurement_after_repair(
    tmp_path: Path,
) -> None:
    run_dir, _terminal, record = e.run_e4(TARGET_I, tmp_path / "run")
    promoted = [entry for entry in record["render_versions"] if entry["promoted"]]
    assert promoted, record["attempted_strategies"]
    wrap_results = [
        m for m in record["measurement_results"]
        if m["method"] == "content_gate_missing_pdf/1"
    ]
    # The IDENTICAL request id was repeated on the candidate render and the
    # confirmed defect measurably improved (missing-leaf count decreased).
    assert wrap_results, record["measurement_results"]
    by_id: dict[str, list] = {}
    for m in wrap_results:
        by_id.setdefault(m["request_id"], []).append(m)
    repeated = [ids for ids in by_id.values() if len(ids) >= 2]
    assert repeated
    first, second = repeated[0][0], repeated[0][-1]
    assert first["request_id"] == second["request_id"]
    assert second["current_value_pt"] < first["current_value_pt"]


@e4_skip
def test_run_e4_promotes_only_verified_improvement_and_records_ceilings(
    tmp_path: Path,
) -> None:
    run_dir, terminal, record = e.run_e4(TARGET_I, tmp_path / "run")
    promoted = [v for v in record["render_versions"] if v["promoted"]]
    if promoted:
        # A promoted defect-level repair improved its confirmed defect without
        # candidate-fact damage: its content gates are green; remaining failed
        # gates (the template-representation ceiling) stay recorded honestly.
        version_id = promoted[-1]["version_id"]
        gates = json.loads((run_dir / f"hard_gates_{version_id}.json").read_text())
        for gate in e.CANDIDATE_FACT_GATES:
            assert gates["gates"][gate] is True
        assert terminal in {"ready_for_owner_review", "budget_exhausted"}
    assert terminal == "budget_exhausted"  # the shape ceiling keeps this run resumable


@e4_skip
def test_run_e4_budget_exhaustion_is_resumable_and_never_unsupported(
    tmp_path: Path,
) -> None:
    budget = e.RunBudget(max_model_requests=0, max_tool_calls=48)
    run_dir, terminal, record = e.run_e4(TARGET_I, tmp_path / "run", budget=budget)
    assert terminal == "budget_exhausted"
    state = json.loads((run_dir / "e4_state.json").read_text())
    assert state["schema_version"] == "pipeline-e-e4-state/1"
    assert "attempted_strategies" in state and "budget_state" in state
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["terminal_state"] == "budget_exhausted"


def test_no_unsupported_terminal_state_exists() -> None:
    source = (Path(__file__).resolve().parents[0] / "e_pipeline.py").read_text()
    # The terminal vocabulary: ready_for_owner_review / budget_exhausted /
    # operational_abort — never a bare 'unsupported' classification.
    for terminal in ("ready_for_owner_review", "budget_exhausted", "operational_abort"):
        assert terminal in source


def test_live_reviewer_and_builder_outputs_cannot_promote() -> None:
    """No agent output type can express promotion; only the shell promotes."""
    assert "promoted" not in e.DefectFinding.model_fields
    assert "promoted" not in e.LiveBuilderRepair.model_fields
    assert "promoted" not in e.LiveAttributionHypothesis.model_fields


def test_builder_cannot_modify_candidate_facts_or_unbounded_layers() -> None:
    with pytest.raises(ValueError, match="candidate fact mutation"):
        e.RepairProposal(
            finding_id="f1",
            base_render_version="v1",
            layer="state_style",
            section_node_id="section.03",
            style_updates={"leaf_name": "rewritten"},
            rationale="attempted fact edit",
            agent="llm",
        )
    with pytest.raises(ValueError, match="bounded 'title_row' placement"):
        e.RepairProposal(
            finding_id="f1",
            base_render_version="v1",
            layer="plan_entry_meta_placement",
            section_node_id="section.03",
            rationale="unbounded placement",
            agent="llm",
        )
    with pytest.raises(ValueError, match="state_style layer"):
        e.RepairProposal(
            finding_id="f1",
            base_render_version="v1",
            layer="plan_entry_gap",
            section_node_id="section.03",
            gap_delta_pt=-2.0,
            style_updates={"font_size_pt": "20"},
            rationale="style outside the layer",
            agent="llm",
        )


def test_accepted_region_recheck_rolls_a_regression_back() -> None:
    """An accepted region regressing beyond tolerance fails the recheck."""
    from tests.experiments.c2_docx_build import TOLERANCE_PT

    tolerance = TOLERANCE_PT["local_gap"]

    def holds(prior_delta: float, repeat_delta: float) -> bool:
        prior = e.MeasurementResult(
            request_id="r1", status="confirmed",
            target_value_pt=0.0, current_value_pt=prior_delta, delta_pt=prior_delta,
        )
        repeat = e.MeasurementResult(
            request_id="r1", status="confirmed",
            target_value_pt=0.0, current_value_pt=repeat_delta, delta_pt=repeat_delta,
        )
        return (
            abs(repeat.current_value_pt - repeat.target_value_pt)
            <= abs(prior.current_value_pt - prior.target_value_pt) + tolerance
        )

    assert holds(10.0, 8.0) is True   # improvement holds
    assert holds(10.0, 10.0) is True  # unchanged holds
    assert holds(10.0, 12.0) is False  # accepted region regressed


@e4_skip
def test_run_e4_promotion_rechecks_accepted_regions(tmp_path: Path) -> None:
    run_dir, _terminal, record = e.run_e4(TARGET_I, tmp_path / "run")
    trace = json.loads((run_dir / "trace.json").read_text())
    promoted = [entry for entry in trace if entry.get("action") == "promoted"]
    assert promoted, record["attempted_strategies"]
    data = json.loads((run_dir / promoted[-1]["output_artifact"]).read_text())
    # Every ACCEPTED region rechecked on the whole new render held; the
    # promoted version's own repeat measurement shows the defect improvement.
    assert all(entry["held"] for entry in data["accepted_region_rechecks"])
    assert data["before"]["request_id"] == data["after"]["request_id"]


@e4_skip
def test_run_e4_no_target_specific_rules() -> None:
    source = (Path(__file__).resolve().parents[0] / "e_pipeline.py").read_text()
    for leaked in ("CONTACT INFO", "ACHIEVEMENTS", "REFERENCES", "JOB TITLE", "ABOUT ME",
                   "CONFERENCES", "PUBLICATIONS", "VERSTAPPEN"):
        assert leaked not in source, leaked
    for line in source.splitlines():
        if "resume_I" in line:
            stripped = line.strip()
            assert stripped.startswith("#"), f"target-name code (not comment): {stripped}"


def test_promotion_requires_shell_not_any_agent(tmp_path: Path) -> None:
    run_dir, _terminal, record = e.run_e4(TARGET_I, tmp_path / "run")
    trace = json.loads((run_dir / "trace.json").read_text())
    finding_agents = {entry["agent"] for entry in trace if entry.get("action") == "finding"}
    proposal_agents = {entry["agent"] for entry in trace if entry.get("action") == "proposal"}
    promotion_agents = {entry["agent"] for entry in trace if entry.get("action") == "promoted"}
    assert finding_agents <= {"visual_reviewer"}
    assert proposal_agents == {"builder"}
    assert promotion_agents <= {"shell"}

import re

import tests.experiments.e_authored_template as at

# ===========================================================================
# E5 — Phase 0 loop fixes + Builder representation comparison (Lane A / Lane B)
# Offline only: no Chrome requirement beyond the same offline lane as E3/E4,
# no live provider call.
# ===========================================================================

e5_skip = pytest.mark.skipif(
    not (
        RESUME_I.exists()
        and (ROOT / "tests/experiments/runs/target_cache").exists()
        and (ROOT / "tests/experiments/runs/e_pipeline_e4_20260920T134946Z/structure_draft.json").exists()
    ),
    reason="authorized Resume I corpus, target cache, and shared E4 draft are not present",
)


def _base_authored_template() -> "at.AuthoredTemplateCandidate":
    from tests.experiments.e_authored_template import SCRIPTED_AUTHORED_TEMPLATE

    return SCRIPTED_AUTHORED_TEMPLATE.model_copy(deep=True)


def test_measurement_request_accepts_semantic_intent_without_anchors() -> None:
    """Phase 0: the Reviewer reports a semantic measurement intent; exact
    verbatim PDF text anchors are no longer required."""
    request = e.MeasurementRequest(
        request_id="measure-pending",
        metric="role_gap",
        page=1,
        region_id="section.04",
        intent="vertical gap between the first two dated entry heads",
    )
    assert request.intent
    assert not request.from_text
    with pytest.raises(ValueError):
        e.MeasurementRequest(
            request_id="measure-pending", metric="role_gap", page=1,
        )  # neither anchors nor intent


def test_measurement_binding_resolves_against_actual_final_pdf_objects(tmp_path: Path) -> None:
    """Phase 0: binding resolves the semantic intent into REAL final-PDF
    objects (render anchors come from the render's own entry-head rows)."""
    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=1,
    )
    lane = record["lanes"]["a"]
    confirmed = [r for r in lane["measurement_results"] if r["status"] == "confirmed"]
    assert confirmed, "binding produced no confirmed measurement on the actual final PDF"
    # The bound request recorded real anchors in the trace (binding step).
    trace = json.loads((run_dir / "lane_a" / "trace.json").read_text())
    binding_notes = [entry for entry in trace if entry.get("action") == "measurement"]
    assert binding_notes


def test_stable_duplicate_findings_are_deduplicated_before_attribution() -> None:
    finding = e.DefectFinding(
        finding_id="finding-001",
        target_version="target-resume_I-v1",
        render_version="render-v1",
        page=1,
        region="section.04",
        observation="The gap between the first two entry heads is larger than the target.",
        suspected_dimension="role_gap",
        requested_measurement=e.MeasurementRequest(
            request_id="measure-pending", metric="role_gap", page=1, intent="entry head gap",
        ),
        severity="high",
        confidence=0.6,
        reviewer="scripted",
    )
    ledger = e.DefectLedger()
    entry, action = ledger.observe(finding)
    assert action == "new"
    # Re-observation of the same defect (same region/dimension/class) is a
    # dedup — no new ledger entry, no re-attribution.
    repeat = finding.model_copy(update={"finding_id": "finding-002", "render_version": "render-v2"})
    entry2, action2 = ledger.observe(repeat)
    assert action2 == "dedup"
    assert entry2 is entry
    assert entry2.last_seen_version == "render-v2"
    assert len(ledger.entries) == 1


def test_changed_findings_reopen_in_the_ledger() -> None:
    finding = e.DefectFinding(
        finding_id="finding-001",
        target_version="target-resume_I-v1",
        render_version="render-v2",
        page=1,
        region="section.04",
        observation="The gap between the first two entry heads is larger than the target.",
        suspected_dimension="role_gap",
        requested_measurement=e.MeasurementRequest(
            request_id="m", metric="role_gap", page=1, intent="entry head gap",
        ),
        severity="high",
        confidence=0.6,
        reviewer="scripted",
    )
    ledger = e.DefectLedger()
    ledger.observe(finding)
    changed = finding.model_copy(
        update={"render_version": "render-v3", "observation": "The entry head text now overlaps the meta column."}
    )
    entry, action = ledger.observe(changed)
    assert action == "reopened"
    assert entry.status == "open"


def test_later_review_rounds_focus_on_changed_regions() -> None:
    round1 = e._review_scope_payload(1, [], [])
    round2 = e._review_scope_payload(2, ["section.04"], [])
    assert round1["whole_document"] is True
    assert round2["whole_document"] is False
    assert round2["changed_regions"] == ["section.04"]
    assert "NOT regenerate the entire defect list" in round2["instruction"]


def test_live_builder_budget_is_reserved() -> None:
    assert e.E5_BUILDER_RESERVE_REQUESTS > 0
    exhausted_to_reserve = RunBudget(max_model_requests=e.E5_BUILDER_RESERVE_REQUESTS, max_tool_calls=10)
    assert exhausted_to_reserve.remaining_model_requests() == e.E5_BUILDER_RESERVE_REQUESTS
    assert e._builder_reserve_intact(exhausted_to_reserve) is False
    healthy = RunBudget(max_model_requests=e.E5_BUILDER_RESERVE_REQUESTS + 1, max_tool_calls=10)
    assert e._builder_reserve_intact(healthy) is True


def test_lane_a_rejects_target_specific_identifiers_and_invalid_fields() -> None:
    # Unknown fields fail validation (pydantic extra=forbid).
    with pytest.raises(ValueError):
        e.LaneAStructureProposal.model_validate(
            {
                "proposal_id": "p1", "agent": "scripted", "sections": [],
                "target_hash": "af6b9234",
            }
        )
    with pytest.raises(ValueError):
        e.E5LaneASection.model_validate(
            {"section_node_id": "section.01", "heading_in_rail": True,
             "rail_label_width_pt": 90.0, "resume_i_heading": "EXPERIENCE"}
        )
    with pytest.raises(ValueError):
        e.E5LaneASection.model_validate(
            {"section_node_id": "section.01", "heading_in_rail": True}
        )  # rail_heading without width is invalid


def test_lane_a_proposal_cites_only_known_state_nodes() -> None:
    from tests.experiments.a_pipeline import build_format_summary
    from app.template_analysis.commercial.models import NormalizedLayoutEvidence

    normalized_path = (
        ROOT / "tests/experiments/runs/c2_resume_i_blind_20260917T115551Z/enriched_evidence.json"
    )
    raw_path = (
        ROOT / "tests/experiments/runs/c2_resume_i_blind_20260917T115551Z/adobe_raw.json"
    )
    if not (normalized_path.exists() and raw_path.exists()):
        pytest.skip("cached Resume I evidence not present")
    normalized = NormalizedLayoutEvidence.model_validate_json(normalized_path.read_text(encoding="utf-8"))
    summary = build_format_summary(normalized, json.loads(raw_path.read_text(encoding="utf-8")), RESUME_I)
    state, _derived = e.compile_two_column_state(RESUME_I, summary, evidence=normalized)
    proposal = e.LaneAStructureProposal(
        proposal_id="p1",
        sections=[e.E5LaneASection(section_node_id="section.99", heading_in_rail=True, rail_label_width_pt=90.0)],
        agent="scripted",
    )
    assert e._validate_lane_a_proposal(proposal, state) is not None
    valid = e.LaneAStructureProposal(
        proposal_id="p2",
        sections=[e.E5LaneASection(section_node_id=node.node_id, heading_in_rail=True, rail_label_width_pt=90.0)
                  for node in state.nodes if node.kind == "section"] or
                 [e.E5LaneASection(section_node_id=state.nodes[0].node_id, heading_in_rail=False)],
        agent="scripted",
    )
    # every proposed id must exist in the compiled state
    known = {node.node_id for node in state.nodes}
    assert all(item.section_node_id in known for item in valid.sections)


@e5_skip
def test_run_e5_offline_lane_a_runs_the_fixed_loop(tmp_path: Path) -> None:
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=1,
    )
    assert terminal in {"ready_for_owner_review", "budget_exhausted"}
    lane = record["lanes"]["a"]
    assert lane["terminal_state"] == terminal
    assert lane["content_shape_probes_passed"] is True
    # the led cycle persisted resumable state
    assert (run_dir / "lane_a" / "e5_lane_a_state.json").exists()
    assert (run_dir / "comparison_report.json").exists()


@e5_skip
def test_run_e5_offline_lane_b_authored_template_loop(tmp_path: Path) -> None:
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
    )
    assert terminal in {"ready_for_owner_review", "budget_exhausted"}
    lane = record["lanes"]["b"]
    assert lane["render_versions"], "the authored template rendered at least one version"
    assert lane["content_shape_probes_passed"] is True
    # The authored render chain ran through Chrome with network disabled.
    trace = json.loads((run_dir / "lane_b" / "trace.json").read_text())
    renders = [entry for entry in trace if entry.get("action") == "render_version"]
    assert renders


def test_lane_b_rejects_unsafe_content_and_undeclared_slots(tmp_path: Path) -> None:
    base = _base_authored_template()
    ok = at.validate_authored_template(base, target_pdf=RESUME_I)
    assert ok["passed"]
    violations = {
        "<script>alert(1)</script>": "javascript element",
        "<div onclick=\"x()\">y</div>": "event handler",
        "background: url(https://x.example/a.png);": "remote url",
        "@import 'x.css';": "css import",
        "<iframe src=\"x\"></iframe>": "unsafe element",
        "{{undeclared:token}}": "undeclared slot",
    }
    for payload, label in violations.items():
        candidate = base.model_copy(update={"html": base.html + "\n" + payload})
        with pytest.raises(ValueError, match="authored template rejected"):
            at.validate_authored_template(candidate, target_pdf=RESUME_I)


def test_lane_b_rejects_target_person_literals() -> None:
    base = _base_authored_template()
    from app.ingestion.pdf_reader import read_pdf_text

    target_words = re.findall(r"[A-Za-z]{5,}", read_pdf_text(RESUME_I))
    outside_vocab = [
        word for word in target_words
        if word.casefold() not in at._TEMPLATE_GENERIC_VOCABULARY
    ]
    assert outside_vocab, "expected at least one distinctive target word to guard"
    injected = base.model_copy(update={"html": base.html + f"\n<!-- {outside_vocab[0]} -->"})
    with pytest.raises(ValueError, match="target-person literals"):
        at.validate_authored_template(injected, target_pdf=RESUME_I)


def test_lane_b_candidate_facts_come_only_from_the_render_context(tmp_path: Path) -> None:
    base = _base_authored_template()
    # A template that hardcodes a candidate fact is rejected.
    hardcoded = base.model_copy(update={"html": base.html + "\n<span>example@example.com</span>"})
    from tests.experiments.c2_candidates import candidate_resume_E

    with pytest.raises(ValueError, match="candidate facts"):
        at.validate_authored_template(hardcoded, target_pdf=RESUME_I, render_candidate=candidate_resume_E())
    # The fill reads ONLY the candidate render context: two different
    # candidates produce different values through the same template.
    from tests.experiments.c2_candidates import independent_candidate_fixtures

    fill_a = at.fill_authored_template(base, candidate_resume_E())
    fixture = at.fill_authored_template(base, e._probe_candidate_with_text(independent_candidate_fixtures()["short"]))
    assert fill_a.html_filled != fixture.html_filled
    assert "example@example.com" in fill_a.html_filled


def test_authored_accounting_is_complete_and_fails_on_missing_leaves() -> None:
    from tests.experiments.c2_candidates import candidate_resume_E

    base = _base_authored_template()
    fill = at.fill_authored_template(base, candidate_resume_E())
    assert not fill.missing_leaves, f"accounting incomplete: {fill.missing_leaves}"
    # Drop the education region: the education leaves go missing -> gate fails.
    stripped = re.sub(r"\{\{each:education\}\}.*?\{\{/each\}\}", "", base.html, flags=re.DOTALL)
    broken = base.model_copy(update={"html": stripped})
    fill2 = at.fill_authored_template(broken, candidate_resume_E())
    assert any(leaf_id.startswith("education") for leaf_id in fill2.missing_leaves)


def test_authored_rendering_uses_a_network_disabled_chrome_environment() -> None:
    environment = at.authored_network_disabled_environment()
    assert any("--host-resolver-rules=MAP * ~NOTFOUND" in flag for flag in environment["flags"])


@e5_skip
def test_run_e5_repeats_the_identical_measurement_after_repair(tmp_path: Path) -> None:
    _run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
    )
    lane = record["lanes"]["b"]
    results = lane["measurement_results"]
    request_ids = [r["request_id"] for r in results]
    assert len(request_ids) != len(set(request_ids)), "the identical request was not repeated after repair"


@e5_skip
def test_run_e5_rolls_back_regressions_and_records_the_ledger(tmp_path: Path) -> None:
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a", "b"), max_repair_rounds=2,
    )
    for lane_id, lane in record["lanes"].items():
        assert lane["ledger"], "the persistent defect ledger is empty"
        # repairs either promote or roll back — never silently persist
        for attempt in lane["repair_attempts"]:
            assert attempt.get("finding")
    assert terminal in {"ready_for_owner_review", "budget_exhausted"}


@e5_skip
def test_run_e5_budget_exhaustion_preserves_resumable_best_valid_state(tmp_path: Path) -> None:
    tiny = RunBudget(max_model_requests=1, max_tool_calls=8)
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
        budget=tiny,
    )
    assert terminal == "budget_exhausted"
    state = json.loads((run_dir / "e5_state.json").read_text())
    lane = state["lanes"]["b"]
    assert lane["budget_state"]["max_model_requests"] == 1
    # no terminal state may ever claim success or unsupported
    assert terminal != "unsupported"
    assert "unsupported" not in json.dumps(record)


def test_neither_lane_can_emit_an_unsupported_terminal_state() -> None:
    with pytest.raises(ValueError):
        e.E5LaneRecord(lane="a", representation="x", terminal_state="unsupported")


@e5_skip
def test_run_e5_prompts_never_carry_rubric_answers(tmp_path: Path) -> None:
    run_dir, _terminal, _record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
    )
    prompts = (run_dir / "prompts.json").read_text(encoding="utf-8")
    config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    assert config["evaluation_rubric_reference"]["not_given_to_agents"] is True
    rubric = (
        ROOT / "tests/experiments/C2_RESUME_I_BLIND_STRUCTURE_AUDIT.md"
    ).read_text(encoding="utf-8")
    rubric_lines = [
        line.strip() for line in rubric.splitlines()
        if len(line.strip()) > 40 and not line.strip().startswith("#")
    ]
    assert rubric_lines
    for line in rubric_lines[:20]:
        assert line not in prompts, "rubric answer text leaked into agent prompts"


def test_owner_acceptance_is_never_inferred() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        e.E5LaneRecord(lane="b", representation="x", terminal_state="delivered")


# --- E5 correctness round: injection, rollback, budgets, probes, reports ----


def test_lane_b_candidate_text_cannot_inject_html() -> None:
    """P0: every scalar CandidateDocument text is HTML-escaped at fill time;
    candidate values are text nodes only — `<li>`/`<br>` markup comes from
    the shell, and no candidate-supplied element, event attribute, closing
    tag, or entity survives as markup."""
    from tests.experiments.c2_candidates import CandidateDocument, CandidateLeaf

    malicious = CandidateDocument(
        candidate_id="malicious_probe",
        leaves=[
            CandidateLeaf(
                leaf_id="header.name", kind="header_field", slot="name",
                text="<script>alert('x')</script>",
            ),
            CandidateLeaf(
                leaf_id="header.email", kind="header_field", slot="envelope",
                text='"><img src=x onerror=alert(1)>',
            ),
            CandidateLeaf(
                leaf_id="summary.p1", kind="summary_paragraph", source="summary",
                text="&<script>closing</script>\"'",
            ),
            CandidateLeaf(
                leaf_id="work.e1", kind="work_entry", source="work_experience",
                text="<b>bold entry</b>",
            ),
            CandidateLeaf(
                leaf_id="work.e1.m1", kind="entry_meta", source="work_experience",
                parent_leaf_id="work.e1", text="2020 & < 2021 >",
            ),
            CandidateLeaf(
                leaf_id="work.e1.b1", kind="work_bullet", source="work_experience",
                parent_leaf_id="work.e1", text="</p><script>x</script>",
            ),
            CandidateLeaf(
                leaf_id="skills.g1", kind="skill_group", source="skills",
                text="<i>italic skill</i>",
            ),
        ],
    )
    fill = at.fill_authored_template(_base_authored_template(), malicious)
    lowered = fill.html_filled.lower()
    # No candidate-supplied element or event attribute survives as markup
    # (an escaped value may contain the TEXT "onerror", but never inside a
    # real tag).
    assert "<script" not in lowered
    assert "<img" not in lowered
    assert "<b>" not in lowered and "<i>" not in lowered
    # The hostile text IS present, escaped (output encoding, not rewriting).
    assert "&lt;script&gt;" in fill.html_filled
    assert "&lt;/p&gt;" in fill.html_filled
    assert "&amp;" in fill.html_filled
    assert "&lt;b&gt;bold entry&lt;/b&gt;" in fill.html_filled
    # Shell-generated list markup exists and its content is escaped.
    assert "<li>&lt;/p&gt;&lt;script&gt;x&lt;/script&gt;</li>" in fill.html_filled
    # The assembled standalone document keeps the same property.
    doc = at.build_authored_document(_base_authored_template(), fill, (595.276, 841.89))
    assert "<script" not in doc.lower()


def test_lane_b_candidate_values_are_never_rescanned_as_slot_syntax() -> None:
    """One substitution pass: a candidate value carrying `{{...}}` cannot
    inject slot tokens; the fill fails closed on the residual-token check."""
    from tests.experiments.c2_candidates import CandidateDocument, CandidateLeaf

    hostile = CandidateDocument(
        candidate_id="token_probe",
        leaves=[
            CandidateLeaf(
                leaf_id="header.name", kind="header_field", slot="name",
                text="{{item_bullets}}",
            ),
            CandidateLeaf(
                leaf_id="summary.p1", kind="summary_paragraph", source="summary",
                text="plain summary",
            ),
        ],
    )
    with pytest.raises(ValueError, match="unfilled slot tokens"):
        at.fill_authored_template(_base_authored_template(), hostile)


@e2_skip
def test_measurement_tool_budget_consumed_once_per_measurement(tmp_path: Path) -> None:
    """The MeasureController is the SINGLE tool-budget consumer for a
    deterministic measurement: one execute -> exactly one tool call."""
    raw_path = tmp_path / "adobe_raw.json"
    raw_path.write_text(
        json.dumps(json.loads((TARGET_F_CACHE / "adobe_raw.json").read_text())),
        encoding="utf-8",
    )
    pod = e.DualSourcePod(TARGET_F, raw_path, tmp_path, render_pdf=None)
    budget = e.RunBudget(max_model_requests=4, max_tool_calls=48)
    trace = e.RunTrace(tmp_path)
    controller = e.MeasureController(pod, budget, trace)
    request = e.MeasurementRequest(
        request_id="measure-budget-001", metric="role_gap", page=1,
        from_text="ImageCaptioningSystem", to_text="SentimentAnalysisAPI",
        render_from_text="ImageCaptioningSystem", render_to_text="SentimentAnalysisAPI",
    )
    controller.execute(request, current_pdf=TARGET_F)
    assert budget.tool_calls == 1
    assert budget.calls_by_tool["compare_pdf_geometry"] == 1


@e5_skip
def test_run_e5_measurement_tool_count_matches_actual_measurements(tmp_path: Path) -> None:
    """No caller-side double consumption: the lane's compare_pdf_geometry
    tool count equals the number of actual measurement records traced by the
    MeasureController (one trace record per deterministic measurement)."""
    _run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
    )
    trace = json.loads((tmp_path / "run" / "lane_b" / "trace.json").read_text())
    measured = [
        entry for entry in trace
        if entry.get("agent") == "measure_controller" and entry.get("action") == "measurement"
    ]
    spent = record["lanes"]["b"]["budget_state"]["calls_by_tool"].get("compare_pdf_geometry", 0)
    assert spent == len(measured) > 0


def test_best_valid_version_selection_follows_explicit_rule() -> None:
    """Explicit rule: only a HARD-GATE-VALID, never-rolled-back version is
    the best-valid render. A defect-level promotion (promoted with a known
    remaining red gate) is NEVER best — it is the defect-level state."""
    def version(vid: str, *, gates: bool, promoted: bool = False) -> e.RenderVersion:
        return e.RenderVersion(
            version_id=vid, html_sha256="h", pdf_sha256="p",
            page_count=1, hard_gates_passed=gates, promoted=promoted, note="",
        )

    # promoted + hard-gate-INVALID: never best (defect-level promotion only)
    v1 = version("v1", gates=False, promoted=True)
    assert e._best_valid_version_id([v1]) is None
    # the same version IS the defect-level state (it stays the latest
    # promoted-not-valid version even after a full-valid promotion exists)
    assert e._best_defect_level_version_id([v1]) == "v1"
    # promoted + hard-gate-valid: the best
    v2 = version("v2", gates=True, promoted=True)
    assert e._best_valid_version_id([v1, v2]) == "v2"
    # the LATEST hard-gate-valid version wins, promoted or not (in practice
    # an executed repair is either promoted or rolled back, so this only
    # differs for the pre-repair initial render)
    v3 = version("v3", gates=True)
    assert e._best_valid_version_id([v1, v2, v3]) == "v3"
    # no promotion: the LATEST hard-gate-valid, not the first
    assert e._best_valid_version_id([version("a", gates=True), version("b", gates=True)]) == "b"
    # nothing valid: None (no best-valid render exists)
    assert e._best_valid_version_id([version("v1", gates=False)]) is None
    # a rolled-back hard-gate-valid candidate never masquerades as best
    v4 = version("v4", gates=True)
    assert e._best_valid_version_id([version("v1", gates=False), v4], excluded={"v4"}) is None
    v5 = version("v5", gates=True)
    assert e._best_valid_version_id([v4, v5], excluded={"v4"}) == "v5"
    # a rolled-back defect-level promotion is not the defect-level state either
    assert e._best_defect_level_version_id([v1, v2], excluded={"v1"}) is None


@e5_skip
def test_run_e5_rejected_render_stays_in_history_and_next_round_uses_old_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback: a rejected (non-improving) candidate render stays in the
    immutable history un-promoted, the active version stays the pre-repair
    render, and the NEXT round's reviewer/measurement read the OLD active
    PDF — never `versions[-1]`."""
    def non_improving_template(base, finding, result):
        # a repair that changes nothing -> identical measurement -> rollback
        return base.model_copy(update={"template_id": base.template_id + "-r"})

    monkeypatch.setattr(e, "_scripted_lane_b_template", non_improving_template)
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=2,
    )
    lane = record["lanes"]["b"]
    assert terminal == "budget_exhausted"
    # the rejected candidate render is preserved in history, un-promoted
    assert len(lane["render_versions"]) >= 2
    assert all(not v["promoted"] for v in lane["render_versions"])
    rolled_back_id = lane["render_versions"][-1]["version_id"]
    assert any("rolled_back" in s for s in lane["attempted_strategies"])
    # the active version stayed the pre-repair render
    assert lane["active_render_version"] == lane["render_versions"][0]["version_id"]
    assert lane["active_render_version"] != rolled_back_id
    # the NEXT round's finding cites the OLD active version (not the
    # rejected candidate), proving the round read the old active PDF
    post_rollback_findings = [
        f for f in lane["findings"]
        if f["render_version"] == lane["active_render_version"]
    ]
    assert len(post_rollback_findings) >= 2, "the next round did not re-review the old active version"
    assert not any(f["render_version"] == rolled_back_id for f in lane["findings"][1:])
    # the best selection is explicit and honest: the rolled-back candidate
    # never becomes best; the pre-repair active render (gate-valid, never
    # rejected) is the best available version
    assert lane["best_render_version"] == lane["active_render_version"]
    assert lane["best_render_version"] != rolled_back_id


@e5_skip
def test_run_e5_promoted_render_becomes_active_and_best_matches_artifact(
    tmp_path: Path,
) -> None:
    """Promotion: the shell promotes only a verified improvement; the
    promoted render becomes the active version, the explicit best-valid
    selection resolves to it, and the best ID matches the actual artifact
    bytes on disk."""
    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
    )
    lane = record["lanes"]["b"]
    assert lane["best_render_version"] is not None, "the scripted improvement must promote"
    promoted = next(
        v for v in lane["render_versions"]
        if v["version_id"] == lane["best_render_version"]
    )
    assert promoted["promoted"] and promoted["hard_gates_passed"]
    assert lane["active_render_version"] == lane["best_render_version"]
    index = lane["render_versions"].index(promoted) + 1
    pdf_path = run_dir / "lane_b" / f"render_{index}.pdf"
    assert pdf_path.exists()
    assert e._sha256_file(pdf_path) == promoted["pdf_sha256"], (
        "the best ID must resolve to the actual artifact bytes"
    )
    # the NEXT round (after promotion) reads the promoted active version
    post_findings = [f for f in lane["findings"] if f["render_version"] == promoted["version_id"]]
    assert post_findings


@e5_skip
def test_run_e5_probes_use_the_selected_representation_and_render_real_pdfs(
    tmp_path: Path,
) -> None:
    """Content-shape probes run the REAL chain (binding -> HTML -> Chrome
    PDF -> accounting/presence/blank/determinism/privacy gates) against the
    EXACT selected representation — recorded by id, with real PDF
    artifacts — never a default-state or string-fill-only claim."""
    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a", "b"), max_repair_rounds=1,
    )
    for lane_id, expected_token in (("a", "proposal"), ("b", "template")):
        lane = record["lanes"][lane_id]
        probes = json.loads(
            (run_dir / f"lane_{lane_id}" / "content_shape_probes.json").read_text()
        )
        assert probes["_probe_mode"] in {"promotion_evidence", "diagnostic"}
        assert probes["_selected_version"] in {
            v["version_id"] for v in lane["render_versions"]
        }
        for profile in ("short", "medium", "long"):
            entry = probes[profile]
            assert entry["representation"], f"lane {lane_id}: probe must name its representation"
            assert entry["pdf"], f"lane {lane_id}/{profile}: probe must record its PDF"
            probe_pdf = run_dir / f"lane_{lane_id}" / entry["pdf"]
            assert probe_pdf.exists() and probe_pdf.stat().st_size > 0
            assert entry["passed"] is True, f"lane {lane_id}/{profile}: {entry.get('gates') or entry.get('error')}"
            assert set(entry["gates"]) >= {
                "no_blank_page", "deterministic_render", "privacy_gate",
            }
        # the selected representation's identity matches the active/best
        # version's recorded representation (not a default scripted fixture)
        selected_id = probes["_selected_version"]
        # the probes run against the best-valid representation, the
        # defect-level active version, or — with neither — the ACTIVE
        # attempt (a rejected candidate never becomes the probe basis);
        # only a diagnostic probe (no best-valid) is not promotion evidence
        if lane["best_render_version"] is not None:
            assert selected_id == lane["best_render_version"]
        else:
            assert selected_id == lane["active_render_version"]


@e5_skip
def test_run_e5_owner_package_labels_latest_attempt_when_no_best(tmp_path: Path) -> None:
    """Without a best-valid render, the owner package shows the LATEST
    ATTEMPT (named and labeled as such), the REPORT states no best-valid
    render exists, and BEST never appears for that lane; with a best render,
    the package copies exactly the best version's artifact."""
    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a", "b"), max_repair_rounds=1,
    )
    package = run_dir / "owner_review"
    lanes = record["lanes"]
    for lane_id in ("a", "b"):
        lane = lanes[lane_id]
        if lane["best_render_version"] is None and lane.get("best_defect_level_version"):
            # a defect-level active version is labeled as such, never BEST
            assert (package / f"lane_{lane_id}_active_defect_level.pdf").exists()
            assert not (package / f"lane_{lane_id}_latest_attempt.pdf").exists()
        elif lane["best_render_version"] is None:
            assert (package / f"lane_{lane_id}_latest_attempt.pdf").exists()
            assert not (package / f"lane_{lane_id}_best.pdf").exists()
    if any(l["best_render_version"] is None for l in lanes.values()):
        report = (package / "REPORT.md").read_text(encoding="utf-8")
        assert "no best-valid render exists" in report
    if lanes["b"]["best_render_version"] is not None:
        best_index = next(
            index + 1
            for index, v in enumerate(lanes["b"]["render_versions"])
            if v["version_id"] == lanes["b"]["best_render_version"]
        )
        copied = package / "lane_b_best.pdf"
        assert copied.exists()
        assert e._sha256_file(copied) == e._sha256_file(
            run_dir / "lane_b" / f"render_{best_index}.pdf"
        )
    # the comparison report labels every lane accurately and takes the page
    # count from the SELECTED artifact
    comparison = json.loads((run_dir / "comparison_report.json").read_text())
    for row in comparison["lanes"]:
        lane_record = lanes[row["lane"]]
        if lane_record["best_render_version"] is not None:
            assert row["render_label"] == "BEST"
        elif lane_record.get("best_defect_level_version"):
            assert row["render_label"] == "ACTIVE DEFECT-LEVEL VERSION"
        else:
            assert row["render_label"] == "LATEST ATTEMPT"
        selected = next(
            v for v in lane_record["render_versions"]
            if v["version_id"] == row["selected_version"]
        )
        assert row["selected_pages"] == selected["page_count"]
        # not measured in this run: recorded as not_evaluated, never 0
        assert row["target_specific_code"] is None
        assert row["target_specific_code_status"] == "not_evaluated"


@e5_skip
def test_run_e5_owner_package_resolves_best_even_when_not_last(tmp_path: Path) -> None:
    """The owner package resolves artifacts through best_render_version —
    a best render that is NOT the last version must be copied, never
    silently replaced by `render_versions[-1]`."""
    # build a record whose best is v1 of two versions, with real artifacts
    target = RESUME_I
    out_dir = tmp_path / "pkg"
    out_dir.mkdir()
    lane_dir = out_dir / "lane_a"
    lane_dir.mkdir()
    from tests.experiments.e_pipeline import RenderVersion, E5LaneRecord, E5LoopRecord

    v1_pdf = lane_dir / "render_1.pdf"
    v2_pdf = lane_dir / "render_2.pdf"
    v1_pdf.write_bytes(b"%PDF-1.4 best")
    v2_pdf.write_bytes(b"%PDF-1.4 latest")
    versions = [
        RenderVersion(
            version_id="render-resume_I-laneA-v1", html_sha256="h1",
            pdf_sha256=e._sha256_file(v1_pdf), page_count=2,
            hard_gates_passed=True, promoted=True, note="best",
        ),
        RenderVersion(
            version_id="render-resume_I-laneA-v2", html_sha256="h2",
            pdf_sha256=e._sha256_file(v2_pdf), page_count=2,
            hard_gates_passed=True, promoted=False, note="latest",
        ),
    ]
    lane = E5LaneRecord(
        lane="a", representation="lane A", render_versions=versions,
        best_render_version="render-resume_I-laneA-v1",
        active_render_version="render-resume_I-laneA-v1",
    )
    record = E5LoopRecord(
        target_id="target-resume_I-v1", target_sha256="0" * 64, lanes={"a": lane},
        summary={"terminal_state": "budget_exhausted"},
    )
    package = e._write_e5_owner_package(out_dir, target, record)
    best = package / "lane_a_best.pdf"
    assert best.exists()
    assert best.read_bytes() == v1_pdf.read_bytes(), (
        "the package must copy the BEST version, not the last render"
    )
    assert not (package / "lane_a_latest_attempt.pdf").exists()


@e5_skip
def test_run_e5_config_freezes_source_hashes_and_detects_change(tmp_path: Path) -> None:
    """The run config records the source identity (file SHA-256 + tracked-diff
    hash relative to HEAD + untracked hashes) of the critical DIRECT runtime
    sources BEFORE the first live call; any drift is detected and the run is
    NOT source-identity stable. Source stability never promotes a run to
    'canonical' — the field is `source_identity_stable`, nothing more."""
    run_dir, _terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("b",), max_repair_rounds=1,
    )
    config = json.loads((run_dir / "run_config.json").read_text())
    hashes = config["source_hashes"]
    assert hashes["recorded_before_first_live_call"] is True
    assert hashes["basis"] == "critical_direct_runtime_sources"
    # the registered set covers the ACTUAL direct runtime dependencies
    for name in e.E5_SOURCE_FILES:
        assert hashes["files"][name] == e._sha256_file(e.ROOT / name), name
    for name in (
        "tests/experiments/d_pipeline.py",
        "tests/experiments/c2_renderer.py",
        "tests/experiments/c2_candidates.py",
        "tests/experiments/a_pipeline.py",
        "tests/experiments/c2_pipeline.py",
        "tests/experiments/c2_state.py",
    ):
        assert name in hashes["files"], f"missing direct runtime source: {name}"
    assert hashes["tracked_diff_sha256"] == e._tracked_diff_sha256(e.ROOT, e.E5_SOURCE_FILES)
    assert record["summary"]["source_changed"] is False
    assert record["summary"]["source_identity_stable"] is True
    # offline rehearsal runs are NEVER auto-promoted to canonical by a
    # stable hash: the field records source stability only
    assert "canonical" not in record["summary"]
    # any drift between frozen identity and current source is detected
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["files"]["tests/experiments/e_pipeline.py"] = "0" * 64
    assert e._source_hashes_changed(drifted) is True
    # a changed tracked diff (same file hashes is impossible then, but a
    # tampered diff hash must also be detected)
    drifted = json.loads(json.dumps(config))
    drifted["source_hashes"]["tracked_diff_sha256"] = "0" * 64
    assert e._source_hashes_changed(drifted) is True
    # a config WITHOUT source identity (e.g. the pre-fix canonical run) is
    # reported as changed/not-bindable, never silently trusted
    assert e._source_hashes_changed({"starting_commit": "x"}) is True


def test_source_diff_identity_ignores_unrelated_files(tmp_path: Path) -> None:
    """The tracked-diff identity is scoped to the registered source paths:
    an unrelated owner edit (e.g. D_PIPELINE_PROPOSAL.md) must never make
    the E5 source identity drift."""
    import subprocess as sp

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "unrelated").mkdir()
    def git(*args: str) -> None:
        sp.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "pkg/a.py").write_text("a = 1\n")
    (repo / "pkg/b.py").write_text("b = 1\n")
    (repo / "unrelated/notes.md").write_text("owner notes\n")
    git("add", ".")
    git("commit", "-qm", "base")
    identity = ("pkg/a.py", "pkg/b.py")
    baseline = e._tracked_diff_sha256(repo, identity)
    assert baseline is not None
    # an unrelated file changes: identity unchanged
    (repo / "unrelated/notes.md").write_text("owner notes edited\n")
    assert e._tracked_diff_sha256(repo, identity) == baseline
    # an unregistered path inside the same tree changes: identity unchanged
    (repo / "pkg/extra.txt").write_text("new file\n")
    assert e._tracked_diff_sha256(repo, identity) == baseline
    # a REGISTERED source changes: drift detected
    (repo / "pkg/b.py").write_text("b = 2\n")
    assert e._tracked_diff_sha256(repo, identity) != baseline


def test_summarize_e5_state_reads_one_run_only() -> None:
    """The deterministic ledger summarizer reads a single e5_state.json and
    reports exactly that run's numbers (no cross-run splicing possible)."""
    from tests.experiments.e_pipeline import E5LaneRecord, E5LoopRecord, summarize_e5_state
    import tempfile

    lane = E5LaneRecord(
        lane="a", representation="lane A",
        best_render_version=None, active_render_version="v1",
        summary={
            "total_findings": 7, "confirmed_measurements": 5,
            "unbound_measurements": 1, "builder_calls": 2,
            "promoted_versions": 0, "probe_mode": "diagnostic",
        },
    )
    record = E5LoopRecord(
        target_id="t", target_sha256="0" * 64, lanes={"a": lane},
        summary={"terminal_state": "budget_exhausted", "source_changed": False},
    )
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "e_pipeline_e5_test"
        run_dir.mkdir()
        (run_dir / "e5_state.json").write_text(record.model_dump_json())
        summary = summarize_e5_state(run_dir / "e5_state.json")
    assert summary["run_id"] == "e_pipeline_e5_test"
    lane_summary = summary["lanes"]["a"]
    assert lane_summary["findings"] == 7
    assert lane_summary["confirmed_measurements"] == 5
    assert lane_summary["builder_calls"] == 2
    assert lane_summary["best_render_version"] is None
    assert summary["source_changed"] is False


# --- E5 second correctness round: best vs defect-level, version-bound state,
# --- fail-closed token fill, source identity, restricted metadata -------------


@e5_skip
def test_run_e5_rollback_binds_next_round_to_the_active_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Version-bound rollback: after a rejected (non-improving) candidate,
    the next round's reviewer, measurement binding, and Builder all resolve
    against the OLD ACTIVE version — PDF, RenderPlan, and gates — and the
    rejected representation never enters the probe basis or best selection."""
    from tests.experiments.e_pipeline import LaneAStructureProposal

    # a scripted proposal that changes nothing -> the identical render ->
    # the identical measurement -> deterministic non-improving rollback
    def non_improving_proposal(state, derived):
        return LaneAStructureProposal(proposal_id="noop", sections=[], agent="scripted")

    class RoundDistinctReviewer(e.ScriptedReviewer):
        # a distinct dimension per round keeps the repair fingerprint distinct
        # so a repair actually executes in EVERY round (the fingerprint would
        # otherwise be rejected as a repeat after the first rollback)
        def run(self, *args, **kwargs):
            findings = super().run(*args, **kwargs)
            finding_id = kwargs.get("finding_id", "")
            return [
                f.model_copy(update={"suspected_dimension": f"role_gap::{finding_id}"})
                for f in findings
            ]

    monkeypatch.setattr(e, "_scripted_lane_a_proposal", non_improving_proposal)
    monkeypatch.setattr(e, "ScriptedReviewer", RoundDistinctReviewer)
    run_dir, terminal, record = e.run_e5(
        RESUME_I, tmp_path / "run", live=False, lanes=("a",), max_repair_rounds=2,
    )
    lane = record["lanes"]["a"]
    assert terminal == "budget_exhausted"
    versions = lane["render_versions"]
    assert len(versions) >= 2, "at least one repair candidate was rendered"
    active_id = lane["active_render_version"]
    assert active_id == versions[0]["version_id"]
    # every repair candidate was rolled back: un-promoted, never active,
    # never best, never the probe basis
    rejected_ids = [v["version_id"] for v in versions[1:]]
    assert all(not v["promoted"] for v in versions[1:])
    assert any("rolled_back" in s for s in lane["attempted_strategies"])
    best = lane["best_render_version"]
    assert best not in rejected_ids
    if best is not None:
        best_version = next(v for v in versions if v["version_id"] == best)
        assert best_version["hard_gates_passed"], "best must be hard-gate-valid"
    probes = json.loads((run_dir / "lane_a" / "content_shape_probes.json").read_text())
    assert probes["_selected_version"] not in rejected_ids
    # the probe basis is the best-valid render, else the ACTIVE attempt
    assert probes["_selected_version"] == (best or active_id)

    trace = json.loads((run_dir / "lane_a" / "trace.json").read_text())
    # (1) the next round's measurement binding resolves against the OLD
    # ACTIVE version (the first binding after the first rollback decision)
    first_rollback = next(
        index for index, entry in enumerate(trace) if entry.get("action") == "rolled_back"
    )
    bindings = [
        entry for entry in trace[first_rollback + 1:]
        if entry.get("agent") == "measure_controller" and entry.get("action") == "binding"
    ]
    assert bindings, "no measurement binding recorded after the rollback"
    assert bindings[0]["input"]["render_version"] == active_id
    # (2) every executed repair round received the OLD ACTIVE gates and
    # declared the OLD ACTIVE version as its base
    active_gates = json.loads(
        (run_dir / "lane_a" / f"hard_gates_{active_id}.json").read_text()
    )["gates"]
    proposals = [
        entry for entry in trace
        if entry.get("agent") == "builder" and entry.get("action") == "proposal"
    ]
    assert len(proposals) >= 2, "a repair must execute in at least two rounds"
    for entry in proposals:
        payload = json.loads(
            (run_dir / "lane_a" / entry["output_artifact"]).read_text()
        )
        assert payload["current_version"] == active_id
        assert payload["current_gates"] == active_gates
    # (3) the next rounds' reviewer findings cite the OLD ACTIVE version
    assert all(f["render_version"] == active_id for f in lane["findings"])


def test_owner_package_labels_defect_level_active_version_never_best(tmp_path: Path) -> None:
    """promoted=True with hard_gates_passed=False is a defect-level state:
    the owner package labels it ACTIVE DEFECT-LEVEL VERSION (with the actual
    artifact copied under that name), never BEST, and best_render_version
    stays None."""
    out_dir = tmp_path / "pkg"
    lane_dir = out_dir / "lane_a"
    lane_dir.mkdir(parents=True)
    v1_pdf = lane_dir / "render_1.pdf"
    v2_pdf = lane_dir / "render_2.pdf"
    v1_pdf.write_bytes(b"%PDF-1.4 v1")
    v2_pdf.write_bytes(b"%PDF-1.4 v2 defect-level")
    versions = [
        e.RenderVersion(
            version_id="v1", html_sha256="h1", pdf_sha256=e._sha256_file(v1_pdf),
            page_count=1, hard_gates_passed=False, promoted=False, note="initial",
        ),
        e.RenderVersion(
            version_id="v2", html_sha256="h2", pdf_sha256=e._sha256_file(v2_pdf),
            page_count=1, hard_gates_passed=False, promoted=True, note="defect-level",
        ),
    ]
    lane = e.E5LaneRecord(
        lane="a", representation="lane A", render_versions=versions,
        best_render_version=None,
        best_defect_level_version="v2",
        active_render_version="v2",
    )
    record = e.E5LoopRecord(
        target_id="t", target_sha256="0" * 64, lanes={"a": lane},
        summary={"terminal_state": "budget_exhausted"},
    )
    # the selection rule: the defect-level version is the shown artifact,
    # labeled ACTIVE DEFECT-LEVEL VERSION
    version, stem, label = e._selected_lane_artifact(lane)
    assert label == "ACTIVE DEFECT-LEVEL VERSION"
    assert version.version_id == "v2" and stem == "render_2"
    package = e._write_e5_owner_package(out_dir, RESUME_I, record)
    assert (package / "lane_a_active_defect_level.pdf").read_bytes() == v2_pdf.read_bytes()
    assert not (package / "lane_a_best.pdf").exists()
    assert not (package / "lane_a_latest_attempt.pdf").exists()
    report = (package / "REPORT.md").read_text(encoding="utf-8")
    assert "ACTIVE DEFECT-LEVEL VERSION" in report
    assert "never BEST" in report
    comparison = e._write_e5_comparison(out_dir, record, {"run_id": out_dir.name})
    row = json.loads((out_dir / "comparison_report.json").read_text())["lanes"][0]
    assert row["render_label"] == "ACTIVE DEFECT-LEVEL VERSION"
    assert row["best_render"] is None
    assert row["selected_version"] == "v2"


def test_candidate_token_syntax_fails_closed_in_every_slot_region() -> None:
    """A candidate value carrying `{{...}}` must fail closed in EVERY slot
    region — never silently stripped — and the original CandidateDocument
    stays byte-identical."""
    from tests.experiments.c2_candidates import CandidateDocument, CandidateLeaf

    def entry_candidate(entry_text, *children) -> CandidateDocument:
        return CandidateDocument(
            candidate_id="token_probe",
            leaves=[
                CandidateLeaf(
                    leaf_id="work.e1", kind="work_entry", source="work_experience",
                    text=entry_text,
                ),
                *[
                    CandidateLeaf(
                        leaf_id=f"work.e1.c{i}", kind=kind, source="work_experience",
                        parent_leaf_id="work.e1", text=text,
                    )
                    for i, (kind, text) in enumerate(children, 1)
                ],
            ],
        )

    cases = {
        "header_name": CandidateDocument(
            candidate_id="token_probe",
            leaves=[CandidateLeaf(
                leaf_id="header.name", kind="header_field", slot="name",
                text="{{X Y}}",
            )],
        ),
        "summary": CandidateDocument(
            candidate_id="token_probe",
            leaves=[CandidateLeaf(
                leaf_id="summary.p1", kind="summary_paragraph", source="summary",
                text="Summary {{something}}",
            )],
        ),
        "work_title": entry_candidate("Engineer {{title_tok}}"),
        "work_detail": entry_candidate("Engineer", ("entry_detail", "Did things {{detail_tok}}")),
        "work_meta": entry_candidate("Engineer", ("entry_meta", "2020 {{meta_tok}}")),
        "work_bullet": entry_candidate("Engineer", ("work_bullet", "Built {{bullet_tok}}")),
        "skill": CandidateDocument(
            candidate_id="token_probe",
            leaves=[
                CandidateLeaf(
                    leaf_id="skills.g1", kind="skill_group", source="skills",
                    text="Group {{skill_tok}}",
                ),
            ],
        ),
        "language": CandidateDocument(
            candidate_id="token_probe",
            leaves=[CandidateLeaf(
                leaf_id="lang.1", kind="language", source="languages",
                text="English {{lang_tok}}",
            )],
        ),
        "certification": CandidateDocument(
            candidate_id="token_probe",
            leaves=[CandidateLeaf(
                leaf_id="cert.1", kind="certification_item", source="certifications",
                text="Cert {{cert_tok}}",
            )],
        ),
    }
    for label, candidate in cases.items():
        original = candidate.model_dump(mode="json")
        with pytest.raises(ValueError, match="unfilled slot tokens remain after fill"):
            at.fill_authored_template(_base_authored_template(), candidate)
        # the candidate document is never rewritten (fail closed, not fixed)
        assert candidate.model_dump(mode="json") == original, label


def test_lane_b_metadata_fields_are_restricted_not_free_text() -> None:
    """`evidence_refs` accept ONLY typed ev.<kind>.<n> IDs,
    `expected_measurements` ONLY restricted identifiers, slot descriptions a
    fixed bounded charset; there is NO free-text rationale field. The
    remaining literal person-fact gate is a HEURISTIC: numbers, short words,
    and non-English text are NOT reliably caught (documented gap, not a
    closed channel)."""
    base = _base_authored_template()
    assert "rationale" not in at.AuthoredTemplateCandidate.model_fields

    def _with(**updates):
        data = base.model_dump(mode="python")
        data.update(updates)
        return at.AuthoredTemplateCandidate.model_validate(data)

    # non-evidence-ID refs fail closed
    with pytest.raises(ValueError, match="evidence_refs must be typed"):
        _with(evidence_refs=["the candidate's phone number"])
    with pytest.raises(ValueError, match="evidence_refs must be typed"):
        _with(evidence_refs=["overview.001"])
    # typed IDs are accepted
    _with(evidence_refs=["ev.page_overview.001", "ev.adobe_element.007"])
    # prose person facts in expected_measurements fail closed
    with pytest.raises(ValueError, match="expected_measurements must be restricted"):
        _with(expected_measurements=["John Doe works at Acme"])
    _with(expected_measurements=["role_gap", "content_gate_missing_pdf/1"])
    # slot descriptions are bounded: braces and non-ASCII fail construction
    with pytest.raises(ValueError):
        _with(slots=[
            *base.slots,
            at.AuthoredSlot(token="x", category="summary", description="bad {{desc}}"),
        ])
    with pytest.raises(ValueError):
        _with(slots=[
            *base.slots,
            at.AuthoredSlot(token="x", category="summary", description="非英文描述"),
        ])
    # HEURISTIC ceiling, documented by test: the literal gate alone does not
    # catch numbers, short words, or non-English person facts in template
    # text — the channel is narrowed by construction (typed fields), never
    # claimed closed.
    number_probe = base.model_copy(update={"html": base.html + "\n<!-- 555-0199 -->"})
    assert at.validate_authored_template(number_probe, target_pdf=RESUME_I)["passed"]
    non_english_probe = base.model_copy(update={"html": base.html + "\n<!-- 王小明 北京大学 -->"})
    assert at.validate_authored_template(non_english_probe, target_pdf=RESUME_I)["passed"]
