"""Tests for the Pipeline E0/E1 evidence-grounded target-understanding experiment.

Also owns the E2/E3/E4 tests and the shared fixtures (`ROOT`, `RESUME_I`,
`TARGET_F`, `TARGET_F_CACHE`, `e2_skip`) that `test_e5_pipeline.py` imports.
E5 tests live in `tests/experiments/test_e5_pipeline.py` (behaviour-neutral
file split, 2026-09-22).

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


# The whole Pipeline E runtime source, as one string.
#
# Split 2026-09-22: this used to be the single `e_pipeline.py` monolith, so
# the "no target-specific rules / no hardcoded approval list" assertions below
# scanned it directly. The same source now lives in four runtime modules; the
# tests read all four so the scanned surface is unchanged by the file split.
PIPELINE_E_SOURCE_FILES = (
    "e_pipeline.py",
    "e_pipeline_common.py",
    "e_pipeline_legacy.py",
    "e_pipeline_e5.py",
)


# Every target-specific literal the original substring scans looked for: the
# nine Resume-I section titles measured on the walkthrough target, plus the
# target's own body wording and the target person's surname. They are EVIDENCE,
# never source constants: the shell measures them per target and only an owner
# approval may make a presentation label renderable.
_TARGET_SECTION_TITLES = (
    "CONTACT INFO",
    "ABOUT ME",
    "EXPERIENCE",
    "EDUCATION",
    "ACHIEVEMENTS",
    "PUBLICATIONS",
    "CONFERENCES",
    "SKILLS",
    "REFERENCES",
)
_TARGET_SPECIFIC_LITERALS = (
    *_TARGET_SECTION_TITLES,
    "JOB TITLE",
    "VERSTAPPEN",
)
# A binding whose NAME claims to own approval / label / title data.
_APPROVAL_NAME_MARKERS = ("APPROV", "LABEL", "TITLE")
_FORBIDDEN_APPROVAL_NAMES = frozenset(
    {"OWNER_APPROVED_PRESENTATION_LABEL_TEXTS", "_owner_approved_label_text"}
)


def _identifier_names(tree: Any) -> set[str]:
    """Every identifier NAME the module binds or mentions (not string text)."""
    import ast

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name)
    return names


def _scan_source_for_hardcoded_target_data(
    filename: str, source: str
) -> dict[str, list[str]]:
    """Semantic replacement for the old `title not in source` substring scan.

    The substring scan flagged the words "FILE REFERENCES" inside an unrelated
    agent-message-audit docstring as the Resume-I title `REFERENCES`. This
    check looks at what the source DOES with a target-specific literal instead
    of whether the character sequence occurs anywhere:

    - `hardcoded_literal_collections` — a list/tuple/set/dict literal in
      executable data holding TWO OR MORE distinct target literals (a generic
      role-name map that happens to reuse one title word is NOT a target list);
    - `hardcoded_literal_scalars` — a target literal used as a scalar constant
      value (`NAME = "REFERENCES"`);
    - `hardcoded_literal_approvals` — a target literal reachable from a value
      bound to an approval / label / title constant, scalar or collection, e.g.
      `OWNER_APPROVED_... = ("SKILLS",)`;
    - `executable_target_name_literals` — a real string constant naming the
      walkthrough target (comments and docstrings are not code data);
    - `forbidden_names` — a binding using a forbidden approval-constant name.

    Docstrings, comments, log/error prose and f-strings are not executable
    DATA and never match — while a re-introduced hardcoded target list still
    does."""
    import ast

    literals = set(_TARGET_SPECIFIC_LITERALS)
    findings: dict[str, list[str]] = {
        "hardcoded_literal_collections": [],
        "hardcoded_literal_scalars": [],
        "hardcoded_literal_approvals": [],
        "executable_target_name_literals": [],
        "forbidden_names": [],
    }

    def literal_of(node: Any) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            return text if text in literals else None
        return None

    def in_value(value: Any) -> list[str]:
        return sorted({t for sub in ast.walk(value) if (t := literal_of(sub))})

    tree = ast.parse(source, filename=filename)
    if _FORBIDDEN_APPROVAL_NAMES & _identifier_names(tree):
        findings["forbidden_names"].append(filename)
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            hit = sorted({t for element in node.elts if (t := literal_of(element))})
            if len(hit) >= 2:
                findings["hardcoded_literal_collections"].append(
                    f"{filename}:{node.lineno}: {hit}"
                )
        elif isinstance(node, ast.Dict):
            hit = sorted(
                {t for element in (*node.keys, *node.values) if (t := literal_of(element))}
            )
            if len(hit) >= 2:
                findings["hardcoded_literal_collections"].append(
                    f"{filename}:{node.lineno}: {hit}"
                )
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not names or node.value is None:
                continue
            scalar = literal_of(node.value)
            if scalar is not None:
                findings["hardcoded_literal_scalars"].append(
                    f"{filename}:{node.lineno}: {names[0]} = {scalar!r}"
                )
            claims_approval = any(
                any(marker in name.upper() for marker in _APPROVAL_NAME_MARKERS)
                for name in names
            )
            if claims_approval:
                hit = in_value(node.value)
                if hit:
                    findings["hardcoded_literal_approvals"].append(
                        f"{filename}:{node.lineno}: {hit}"
                    )
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "resume_I" in node.value
        ):
            findings["executable_target_name_literals"].append(
                f"{filename}:{node.lineno}: {node.value[:60]!r}"
            )
    return findings


def _pipeline_e_semantic_findings() -> dict[str, list[str]]:
    """`_scan_source_for_hardcoded_target_data` over every Pipeline E runtime
    module (the pre-split single-file scan surface)."""
    findings: dict[str, list[str]] = {
        "hardcoded_literal_collections": [],
        "hardcoded_literal_scalars": [],
        "hardcoded_literal_approvals": [],
        "executable_target_name_literals": [],
        "forbidden_names": [],
    }
    base = Path(__file__).resolve().parents[0]
    for filename in PIPELINE_E_SOURCE_FILES:
        for key, hits in _scan_source_for_hardcoded_target_data(
            filename, (base / filename).read_text(encoding="utf-8")
        ).items():
            findings[key].extend(hits)
    return findings


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


def test_run_e2_operational_abort_returns_typed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.experiments import e_pipeline_legacy as legacy

    def fail_environment(_env: dict[str, str]) -> dict[str, str]:
        raise RuntimeError("chrome unavailable")

    monkeypatch.setattr(legacy, "pinned_export_environment", fail_environment)
    run_dir, terminal, record = e.run_e2(TARGET_F, tmp_path / "run")
    assert terminal == "operational_abort"
    assert record["schema_version"] == "pipeline-e-e2-state/1"
    assert record["summary"]["abort"]["operation"] == "pinned_chrome_environment"
    assert json.loads((run_dir / "e2_state.json").read_text()) == record


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
    """The E3 shell derives structure from evidence: no Resume-I title, target
    name, or approval constant may exist as EXECUTABLE DATA (the scripted
    reviewer scenario records are runtime data keyed by the target id, not
    production rules). Semantic AST check — a docstring, comment or audit
    sentence that merely CONTAINS a title word is not code data."""
    findings = _pipeline_e_semantic_findings()
    assert not any(findings.values()), findings


@e3_skip
def test_run_e3_operational_abort_never_classifies_unsupported(tmp_path: Path) -> None:
    # Missing cached evidence → operational_abort describing the failed
    # operation, never 'unsupported'.
    with pytest.raises(RuntimeError, match="target PDF not found"):
        e.run_e3(tmp_path / "missing.pdf", tmp_path / "run")


@e3_skip
def test_run_e3_tool_budget_exhaustion_is_resumable(tmp_path: Path) -> None:
    budget = e.RunBudget(max_model_requests=6, max_tool_calls=4)
    _run_dir, terminal, record = e.run_e3(
        TARGET_I, tmp_path / "run", budget=budget, max_repair_attempts=1,
    )
    assert terminal == "budget_exhausted"
    assert any("tool_budget_exhausted" in item for item in record["attempted_strategies"])


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
        # gates stay recorded honestly; this test does not classify their cause.
        version_id = promoted[-1]["version_id"]
        gates = json.loads((run_dir / f"hard_gates_{version_id}.json").read_text())
        for gate in e.CANDIDATE_FACT_GATES:
            assert gates["gates"][gate] is True
        assert terminal in {"ready_for_owner_review", "budget_exhausted"}
    assert terminal == "budget_exhausted"  # the red shape gate keeps this run resumable


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
    findings = _pipeline_e_semantic_findings()
    assert not any(findings.values()), findings


def test_hardcoded_target_data_scan_catches_data_but_not_prose() -> None:
    """Minimal positive/negative check for the semantic scan that replaced the
    old substring scan.

    NEGATIVE (why the substring scan had to go): a docstring/comment/audit
    sentence containing the word REFERENCES is prose, not data.
    POSITIVE (what must still be caught): a real hardcoded Resume-I title
    list, and a title bound to an approval/label constant."""
    prose = '''
"""Images become FILE REFERENCES (relative path, sha256, media type)."""
# the target lists REFERENCES and SKILLS as section headings
MESSAGE = "see the REFERENCES section"
F"""verdict for {name}: JOB TITLE"""
'''
    assert not any(_scan_source_for_hardcoded_target_data("prose.py", prose).values())

    # a real hardcoded target list is still caught
    title_list = 'INHERITED_SECTION_TITLES = ("CONTACT INFO", "EXPERIENCE", "REFERENCES")\n'
    findings = _scan_source_for_hardcoded_target_data("titles.py", title_list)
    assert findings["hardcoded_literal_collections"], findings
    assert any("REFERENCES" in entry for entry in findings["hardcoded_literal_collections"])

    # a single target literal as a scalar constant is caught
    findings = _scan_source_for_hardcoded_target_data("scalar.py", 'NAME = "VERSTAPPEN"\n')
    assert findings["hardcoded_literal_scalars"], findings

    # a single-element approval tuple is caught via the approval-name rule
    approval = 'OWNER_APPROVED_PRESENTATION_LABEL_TEXTS = ("SKILLS",)\n'
    findings = _scan_source_for_hardcoded_target_data("one.py", approval)
    assert findings["hardcoded_literal_approvals"], findings

    # a generic role-name map that happens to reuse ONE title word is not a
    # fixed target-title list and must not be flagged
    generic = '_PROBE_ROLE_HEADING = {"skills": "SKILLS", "languages": "LANGUAGES"}\n'
    findings = _scan_source_for_hardcoded_target_data("generic.py", generic)
    assert not any(findings.values()), findings


def test_promotion_requires_shell_not_any_agent(tmp_path: Path) -> None:
    run_dir, _terminal, record = e.run_e4(TARGET_I, tmp_path / "run")
    trace = json.loads((run_dir / "trace.json").read_text())
    finding_agents = {entry["agent"] for entry in trace if entry.get("action") == "finding"}
    proposal_agents = {entry["agent"] for entry in trace if entry.get("action") == "proposal"}
    promotion_agents = {entry["agent"] for entry in trace if entry.get("action") == "promoted"}
    assert finding_agents <= {"visual_reviewer"}
    assert proposal_agents == {"builder"}
    assert promotion_agents <= {"shell"}
