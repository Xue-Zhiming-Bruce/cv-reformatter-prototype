"""Evidence coverage, raw-provider access, crops, provenance, and bounded usage."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import pytest
import app.pipeline.evidence as e
from app.pipeline.runtime import RunBudget, BudgetExhausted
from tests.helpers.pipeline import _pod, _scan_source_for_hardcoded_target_data

pytestmark = pytest.mark.integration

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


def test_adobe_lookup_returns_original_nodes_with_lossy_layers_retained(tmp_path: Path) -> None:
    raw = _minimal_raw()
    raw["structured_data"]["elements"][0]["Bounds"] = [10, 10, 20, 20]
    pod = _pod(tmp_path, raw=raw)
    result = pod.inspect_adobe_json(page_number=1, limit=50)
    assert result["status"] == "available"
    assert result["normalization_applied"] is False
    assert result["verbatim"] is True
    # The raw text-leaf count is the pre-normalization number and is reported.
    assert result["raw_text_leaf_count"] == 2
    paths = " ".join(str(node.get("Path", "")) for node in result["nodes"])
    # Table frames and list labels are exactly what normalization drops.
    assert "/Table/" in paths or "/Lbl" in paths or "/LI" in paths
    # CharBounds/Bounds survive verbatim so geometry can be re-measured.
    assert any("Bounds" in node for node in result["nodes"])


def test_adobe_lookup_forwards_raw_paths_that_normalization_drops(tmp_path: Path) -> None:
    raw = _minimal_raw()
    raw["structured_data"]["elements"][0]["Bounds"] = [10, 10, 20, 20]
    pod = _pod(tmp_path, raw=raw)
    dropped = [
        node
        for node in pod.inspect_adobe_json(limit=100)["nodes"]
        if "/Table/" in str(node.get("Path", ""))
    ]
    assert dropped, "raw-only table nodes must survive access"


def test_adobe_lookup_reports_no_match_as_unavailable(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    result = pod.inspect_adobe_json(element_id=999999999)
    assert result["status"] == "unavailable"
    assert "no raw element matched" in result["reason"]
    assert next(r for r in pod.records if r.kind == "adobe_element").status == "unavailable"


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


def test_local_measurement_is_provenanced_and_not_an_analyzer_fallback(tmp_path: Path) -> None:
    pod = _pod(tmp_path)
    result = pod.measure_local_pdf(page_number=1, include="rules")
    assert result["provenance"] == "local_pdf"
    assert result["is_analyzer_fallback"] is False
    assert result["source_sha256"] == hashlib.sha256(pod.target_pdf.read_bytes()).hexdigest()
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




def test_budget_counts_live_and_scripted_calls_separately() -> None:
    """Phase 0: 'model requests' must not merge scripted agent invocations
    with live provider calls; token usage belongs to live calls only."""
    from app.pipeline.runtime import BudgetExhausted

    budget = RunBudget(max_model_requests=3, max_tool_calls=8)
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
    from types import SimpleNamespace
    from app.pipeline.runtime import RunTrace, _record_usage

    _record_usage(budget, RunTrace(Path("unused")), "visual_reviewer", "review", SimpleNamespace(usage=_Usage()))
    assert budget.model_requests == 3  # successful usage must not double-count HTTP reservations
    assert budget.usage["input_tokens"] == 22


def test_live_http_budget_counts_failed_sdk_retries_before_io(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    import httpx
    from openai import APIConnectionError
    from app.pipeline.runtime import RunTrace, _live_model

    sent = []
    async def respond(request):
        sent.append(request)
        return httpx.Response(500, json={"error": {"message": "transient failure"}})

    class OfflineClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://deepseek.invalid")
    parent = RunBudget(max_model_requests=2)
    lane = RunBudget(max_model_requests=8, max_request_bytes=240000, parent=parent)
    trace = RunTrace(tmp_path)
    model = _live_model(lane, trace, "builder")

    async def call():
        async with model.client:
            with pytest.raises(APIConnectionError):
                await model.client.chat.completions.create(
                    model="deepseek-flash", messages=[{"role": "user", "content": "synthetic"}], max_tokens=16,
                )
    asyncio.run(call())
    assert len(sent) == 2  # the third SDK attempt is blocked before transport
    assert lane.model_requests == parent.model_requests == 2
    assert parent.calls_by_mode["live"] == 2
    assert "synthetic-secret" not in (tmp_path / "trace.json").read_text()


@pytest.mark.parametrize("body", [
    {"messages": [], "max_tokens": 16385},
    {"messages": [{"content": [{"type": "image_url"}] * 9}], "max_tokens": 16},
    {"messages": [{"content": "x" * 1000}], "max_tokens": 16},
])
def test_live_request_envelope_rejects_before_transport(tmp_path: Path, monkeypatch, body) -> None:
    import asyncio
    from functools import partial
    import httpx
    from app.pipeline.runtime import RunTrace, _budgeted_http_client

    def unexpected_request(request):
        pytest.fail("over-budget payload reached the network transport")
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(unexpected_request)))
    budget = RunBudget(max_model_requests=2, max_request_bytes=600)
    async def call():
        async with _budgeted_http_client(budget, RunTrace(tmp_path), "builder") as client:
            with pytest.raises(BudgetExhausted):
                await client.post("https://deepseek.invalid/chat/completions", json=body)
    asyncio.run(call())
    assert budget.model_requests == 0


def test_sdk_wrapped_envelope_failure_is_not_retried_as_a_connection_error(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    import httpx
    from app.pipeline.runtime import RunTrace, _live_model
    from app.pipeline.service import _with_connection_retry

    def unexpected_request(_request):
        pytest.fail("over-budget payload reached the network transport")

    class OfflineClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(unexpected_request), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://deepseek.invalid")
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    budget = RunBudget(max_model_requests=2, max_request_bytes=600)
    trace = RunTrace(tmp_path)
    attempts = []

    def call():
        attempts.append(1)
        async def request():
            model = _live_model(budget, trace, "builder")
            async with model.client:
                await model.client.chat.completions.create(
                    model="deepseek-flash", messages=[{"role": "user", "content": "x" * 1000}], max_tokens=16)
        asyncio.run(request())

    with pytest.raises(BudgetExhausted, match="envelope"):
        _with_connection_retry(call, trace=trace, what="synthetic reviewer")
    assert attempts == [1] and budget.model_requests == 0


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
