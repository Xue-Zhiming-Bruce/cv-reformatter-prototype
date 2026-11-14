"""Evidence coverage, raw-provider access, crops, provenance, and bounded usage."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import pytest
import app.pipeline.evidence as e
from app.pipeline.runtime import RunBudget, BudgetExhausted, RunTrace
from tests.helpers.pipeline import _pod, _scan_source_for_hardcoded_target_data, synthetic_pipeline_inputs
from tests.helpers.synthetic_pdf import _write_single_page_pdf

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


# A render crop reads the render the pod is bound to. These cases keep the render
# and target pixels distinguishable on purpose: the target page is blank inside
# the box and the render paints a solid marker there.
_RENDER_BBOX = [20.0, 400.0, 120.0, 440.0]  # [x0, top, x1, bottom] in PDF points


def _dual_pod(tmp_path: Path, *, render_pdf: Path | None = None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    inputs = synthetic_pipeline_inputs(tmp_path)
    return e.DualSourcePod(inputs["target_pdf"], inputs["adobe_json"], tmp_path, render_pdf=render_pdf)


def _marked_render_pdf(
    path: Path, *, page_size: tuple[float, float] = (612, 792), fill: str = "1 0 0"
) -> Path:
    """A render PDF whose cropped region is a solid marker, larger than the bbox."""
    path.parent.mkdir(parents=True, exist_ok=True)
    x0, top, x1, bottom = _RENDER_BBOX
    width, height = page_size
    marker = (
        f"q {fill} rg {x0 - 10} {height - bottom - 10} "
        f"{x1 - x0 + 20} {bottom - top + 20} re f Q"
    )
    return _write_single_page_pdf(path, [marker], width_pt=width, height_pt=height)


def _attribution_tools(pod, out_dir: Path):
    budget = RunBudget(max_model_requests=4, max_tool_calls=8)
    trace = RunTrace(out_dir)
    tools = {
        tool.__name__: tool
        for tool in e._pod_tools(pod, budget, trace, agent_name="attribution_investigator")
    }
    return tools, budget, trace


def test_dual_render_crop_without_a_render_is_unavailable_not_faked(tmp_path: Path) -> None:
    pod = _dual_pod(tmp_path)
    parts = pod.inspect_page_region(1, list(_RENDER_BBOX), source="render")
    payload = json.loads(str(parts[0]))
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "no candidate render exists at this checkpoint"
    record = next(r for r in pod.records if r.kind == "region_crop")
    assert record.status == "unavailable" and record.reason and record.artifact is None
    assert not list(tmp_path.glob("region_*.png"))


def test_render_crop_returns_the_bound_render_with_render_provenance(tmp_path: Path) -> None:
    from PIL import Image, ImageChops
    from pydantic_ai import BinaryContent

    render = _marked_render_pdf(tmp_path / "render_1.pdf")
    pod = _dual_pod(tmp_path, render_pdf=render)

    parts = pod.inspect_page_region(1, list(_RENDER_BBOX), source="render", note="render header contact row")

    assert len(parts) == 2
    assert isinstance(parts[1], BinaryContent) and parts[1].media_type == "image/png"
    header = json.loads(str(parts[0]).split("\n", 1)[1])
    record = next(r for r in pod.records if r.kind == "region_crop")
    assert record.status == "available" and record.reason is None
    assert record.tool == "inspect_page_region" and record.source_path == render.name
    assert header["evidence_id"] == record.evidence_id
    artifact = tmp_path / str(record.artifact)
    assert artifact.exists() and artifact.stat().st_size > 0

    assert header["source_kind"] == "render"
    assert header["source_sha256"] == hashlib.sha256(render.read_bytes()).hexdigest()
    page_png = tmp_path / header["source_artifact"]
    assert page_png.name.startswith("render_") and page_png.name.endswith("_page_1.png")
    assert page_png.exists() and page_png.stat().st_size > 0
    assert header["page_size_pt"] == [612.0, 792.0]
    assert header["is_original_resolution"] is True

    # The image is the render's pixels: an independent render of the same PDF,
    # cropped at the reported box, is identical, and the marker is fully inside.
    independent_dir = tmp_path / "independent"
    independent_dir.mkdir()
    independent = e._render_pages(render, independent_dir, "independent")[0]
    with Image.open(independent) as page, Image.open(artifact) as crop:
        expected = page.convert("RGB").crop(tuple(header["crop_box_px"]))
        assert ImageChops.difference(expected, crop.convert("RGB")).getbbox() is None
        assert crop.convert("RGB").getextrema() == ((255, 255), (0, 0), (0, 0))  # every pixel

    # The same bbox for source="target" still returns the target, not the render.
    target_header = json.loads(str(pod.inspect_page_region(1, list(_RENDER_BBOX))[0]).split("\n", 1)[1])
    assert target_header["source_kind"] == "target_pdf"
    assert target_header["target_sha256"] == hashlib.sha256(pod.target_pdf.read_bytes()).hexdigest()
    with Image.open(tmp_path / str(pod.records[-1].artifact)) as target_crop:
        assert ImageChops.difference(target_crop.convert("RGB"), expected).getbbox() is not None


def test_render_crop_uses_the_render_page_geometry(tmp_path: Path) -> None:
    """A render page size different from the target's locates the crop where the
    render's own page size predicts, not where the target's does."""
    from PIL import Image

    page_size = (595.28, 841.89)
    render = _marked_render_pdf(tmp_path / "render_1.pdf", page_size=page_size)
    pod = _dual_pod(tmp_path, render_pdf=render)
    assert pod.page_sizes[1] == (612.0, 792.0)

    header = json.loads(str(pod.inspect_page_region(1, list(_RENDER_BBOX), source="render")[0]).split("\n", 1)[1])
    assert header["page_size_pt"] == pytest.approx(list(page_size), abs=0.01)
    full_w, full_h = header["full_page_pixels"]
    x0, top, _x1, _bottom = header["crop_box_px"]
    assert abs(x0 / full_w - _RENDER_BBOX[0] / page_size[0]) < 1 / full_w
    assert abs(top / full_h - _RENDER_BBOX[1] / page_size[1]) < 1 / full_h
    with Image.open(tmp_path / str(pod.records[-1].artifact)) as crop:
        assert crop.convert("RGB").getextrema() == ((255, 255), (0, 0), (0, 0))  # every pixel


def test_rebinding_a_render_cannot_reuse_the_cached_crop(tmp_path: Path) -> None:
    from PIL import Image

    first = _marked_render_pdf(tmp_path / "a" / "render_1.pdf", fill="1 0 0")
    second = _marked_render_pdf(tmp_path / "b" / "render_1.pdf", fill="0 0 1")
    pod = _dual_pod(tmp_path, render_pdf=first)

    pod.inspect_page_region(1, list(_RENDER_BBOX), source="render")
    pod.render_pdf = second
    header = json.loads(str(pod.inspect_page_region(1, list(_RENDER_BBOX), source="render")[0]).split("\n", 1)[1])

    assert header["source_sha256"] == hashlib.sha256(second.read_bytes()).hexdigest()
    assert header["source_sha256"] != hashlib.sha256(first.read_bytes()).hexdigest()
    for record, expected in ((pod.records[0], ((255, 255), (0, 0), (0, 0))), (pod.records[1], ((0, 0), (0, 0), (255, 255)))):
        with Image.open(tmp_path / str(record.artifact)) as crop:
            assert crop.convert("RGB").getextrema() == expected


@pytest.mark.parametrize(
    "bbox, message",
    [([100.0, 100.0, 50.0, 50.0], "x0 < x1"), ([0.0, 0.0, 900.0, 900.0], "region too large")],
)
def test_render_crop_rejects_invalid_bounds_like_the_target(tmp_path: Path, bbox, message) -> None:
    pod = _dual_pod(tmp_path, render_pdf=_marked_render_pdf(tmp_path / "render_1.pdf"))
    with pytest.raises(ValueError, match=message):
        pod.inspect_page_region(1, list(bbox))
    with pytest.raises(ValueError, match=message):
        pod.inspect_page_region(1, list(bbox), source="render")
    assert not list(tmp_path.glob("region_*.png"))


def test_render_crop_rejects_a_page_past_the_bound_render(tmp_path: Path) -> None:
    pod = _dual_pod(tmp_path, render_pdf=_marked_render_pdf(tmp_path / "render_1.pdf"))
    tools, _budget, _trace = _attribution_tools(pod, tmp_path)
    result = tools["inspect_page_region"](2, list(_RENDER_BBOX), source="render")
    assert result["status"] == "error" and result["tool"] == "inspect_page_region"
    assert "page 2 outside 1..1" in result["error"]
    assert not list(tmp_path.glob("region_*.png"))
    assert [record for record in pod.records if record.kind == "region_crop"] == []


def test_render_crop_runs_through_the_attribution_tool_boundary(tmp_path: Path) -> None:
    from PIL import Image
    from pydantic_ai import BinaryContent

    pod = _dual_pod(tmp_path, render_pdf=_marked_render_pdf(tmp_path / "render_1.pdf"))
    tools, budget, trace = _attribution_tools(pod, tmp_path)

    parts = tools["inspect_page_region"](
        1, list(_RENDER_BBOX), source="render", note="render header contact row"
    )
    assert len(parts) == 2 and isinstance(parts[1], BinaryContent)
    assert budget.tool_calls == 1 and budget.calls_by_tool["inspect_page_region"] == 1
    entry = trace.entries[-1]
    assert entry["action"] == "tool_call" and entry["tool"] == "inspect_page_region"
    assert entry["phase"] == "target_understanding" and entry["agent"] == "attribution_investigator"
    # The available crop is persisted as a run artifact (a reviewer re-inspects
    # exactly these bytes); the refusal below persists no image at all.
    crop = tmp_path / str(next(item for item in pod.records if item.kind == "region_crop").artifact)
    assert crop.exists() and crop.stat().st_size > 0
    with Image.open(crop) as decoded:
        assert decoded.format == "PNG"
    record = next(item for item in pod.records if item.kind == "region_crop")
    assert record.request == {
        "page": 1,
        "bbox_pt": list(_RENDER_BBOX),
        "source": "render",
        "note": "render header contact row",
    }

    refusal_pod = _dual_pod(tmp_path / "refusal")
    refusal_tools, refusal_budget, refusal_trace = _attribution_tools(refusal_pod, tmp_path / "refusal")
    result = refusal_tools["inspect_page_region"](1, list(_RENDER_BBOX), source="render")
    assert len(result) == 1 and json.loads(result[0])["status"] == "unavailable"
    assert not any(isinstance(part, BinaryContent) for part in result)
    assert refusal_budget.tool_calls == 1 and refusal_budget.calls_by_tool["inspect_page_region"] == 1
    refusal_entry = refusal_trace.entries[-1]
    assert refusal_entry["action"] == "tool_call" and refusal_entry["tool"] == "inspect_page_region"
    assert refusal_entry["phase"] == "target_understanding"
    assert next(item for item in refusal_pod.records if item.kind == "region_crop").artifact is None
    assert not list((tmp_path / "refusal").glob("region_*.png"))


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
    """The SDK's own retries hit the cap before transport, and the production
    wrapper surfaces that as BudgetExhausted whoever raised the hook error."""
    import asyncio
    import httpx
    from app.pipeline.runtime import RunTrace, _live_model
    from app.pipeline.agent_adapters import _with_connection_retry

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
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    parent = RunBudget(max_model_requests=2)
    lane = RunBudget(max_model_requests=8, max_request_bytes=240000, parent=parent)
    trace = RunTrace(tmp_path)
    attempts = []

    def call():
        attempts.append(1)
        async def request():
            model = _live_model(lane, trace, "builder")
            async with model.client:
                await model.client.chat.completions.create(
                    model="deepseek-flash", messages=[{"role": "user", "content": "synthetic"}], max_tokens=16,
                )
        asyncio.run(request())

    with pytest.raises(BudgetExhausted, match="budget exhausted"):
        _with_connection_retry(call, trace=trace, what="synthetic builder")
    assert len(sent) == 2  # the third SDK attempt is blocked before transport
    assert lane.model_requests == parent.model_requests == 2
    assert parent.calls_by_mode["live"] == 2
    assert attempts == [1]  # the wrapper never retried a budget failure
    assert "synthetic-secret" not in (tmp_path / "trace.json").read_text()


def test_wrapped_budget_failure_is_unwrapped_and_not_retried(tmp_path: Path) -> None:
    """Pins _with_connection_retry's __cause__ unwrap for the SDK shape that
    wraps request-hook failures in APIConnectionError."""
    import httpx
    from openai import APIConnectionError
    from app.pipeline.runtime import RunTrace
    from app.pipeline.agent_adapters import _with_connection_retry

    def call():
        try:
            raise BudgetExhausted("global model budget exhausted: 0+1 would exceed 0")
        except BudgetExhausted as budget_failure:
            raise APIConnectionError(request=httpx.Request("POST", "https://deepseek.invalid")) from budget_failure

    with pytest.raises(BudgetExhausted, match="budget exhausted"):
        _with_connection_retry(call, trace=RunTrace(tmp_path), what="synthetic builder")


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
    from app.pipeline.agent_adapters import _with_connection_retry

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


@pytest.mark.parametrize("color", [False, True])
def test_whole_page_overviews_preserve_rgb_pixels_with_bounded_encoding(tmp_path: Path, monkeypatch, color: bool) -> None:
    from PIL import Image, ImageChops
    image = Image.new("RGB", (900, 900))
    image.putdata([((i * 17 % 256, i * 29 % 256, i * 53 % 256) if color else (i * 17 % 256,) * 3)
                   for i in range(900 * 900)])
    original = tmp_path / "page.png"
    image.save(original)
    monkeypatch.setattr(e, "_render_pages", lambda *args: [original])
    result = e._overview_pngs(tmp_path / "unused.pdf", tmp_path, "review")[0]
    with Image.open(result) as overview:
        assert overview.size == image.size
        assert ImageChops.difference(overview.convert("RGB"), image).getbbox() is None
        assert overview.mode == ("RGB" if color else "L")
    if not color:
        assert result.stat().st_size < original.stat().st_size
    from app.pipeline.review import prepare_review_image

    prepared = prepare_review_image(result)
    with Image.open(prepared) as decoded:
        assert decoded.size == image.size
        assert ImageChops.difference(decoded.convert("RGB"), image).getbbox() is None
    assert result.exists()  # The original diagnostic PNG remains available.
    assert prepared.stat().st_size <= result.stat().st_size


@pytest.mark.parametrize("caller", ["reviewer", "builder"])
def test_color_model_request_preserves_pixels_and_fits_existing_wire_cap(tmp_path: Path, monkeypatch, caller: str) -> None:
    import random
    from PIL import Image, ImageDraw, ImageFont, ImageChops
    import httpx
    from app.pipeline.review import prepare_review_image, _live_reviewer_findings
    from app.pipeline.runtime import RunTrace
    from app.pipeline.agent_adapters import _live_lane_a_builder

    page = Image.new("RGB", (1272, 1800), "white")
    draw, font, randomizer = ImageDraw.Draw(page), ImageFont.load_default(size=24), random.Random(7)
    for row in range(58):
        text = " ".join(randomizer.choice(["SYNTHETIC", "layout", "project", "measured", "service",
            "candidate", "example", "review", "output", str(row)]) for _ in range(11))
        draw.text((30, 20 + row * 30), text, font=font, fill="#0D9488" if row % 9 == 0 else "#334155")
    page = page.resize((636, 900), Image.Resampling.LANCZOS)
    original = tmp_path / "overview.png"
    page.save(original, optimize=True)
    prepared = prepare_review_image(original)
    assert prepared.suffix == ".webp"
    with Image.open(prepared) as decoded:
        assert ImageChops.difference(page, decoded.convert("RGB")).getbbox() is None
    count = 2 if caller == "reviewer" else 5
    assert count * 4 * ((original.stat().st_size + 2) // 3) > 512000

    requests = []

    async def respond(request):
        body = json.loads(request.content)
        images = [part["image_url"]["url"] for message in body["messages"]
                  if isinstance(message["content"], list) for part in message["content"] if part["type"] == "image_url"]
        assert len(images) == count and all(image.startswith("data:image/webp;base64,") for image in images)
        requests.append(len(request.content))
        tool = body["tools"][0]["function"]["name"]
        arguments = {"response": []} if caller == "reviewer" else {
            "proposal_id": "synthetic-builder", "agent": "llm", "sections": [], "header_contact_labels": False}
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 1,
            "model": "synthetic-vision", "choices": [{"index": 0, "message": {"role": "assistant", "tool_calls": [
                {"id": "result", "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments)}}]},
                "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    class OfflineClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), trust_env=False, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    monkeypatch.setenv("VISUAL_API_BASE", "https://visual.invalid/v1")
    monkeypatch.setenv("VISUAL_API_KEY", "synthetic-key")
    monkeypatch.setenv("A_PIPELINE_VISUAL_MODEL", "synthetic-vision")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://builder.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    monkeypatch.setenv("A_PIPELINE_MODEL", "synthetic-builder")
    budget = RunBudget(max_model_requests=12, max_tool_calls=24, max_request_bytes=512000)
    input_image = prepared if caller == "reviewer" else original
    audit = e.E5AgentAuditSpec(agent="visual_reviewer" if caller == "reviewer" else "lane_a_builder", lane="a", phase="review" if caller == "reviewer" else "repair", round_no=1,
        target_version="target-v1", render_version="render-v1", finding_ids=("finding-1",),
        measurement_ids=(), model="synthetic-vision", instructions="synthetic review",
        image_refs=tuple({"path": input_image.name, "role": "target" if index < 2 else "render", "page": index + 1}
                         for index in range(count)))
    if caller == "reviewer":
        findings = _live_reviewer_findings([input_image] * count, "finding-1", "target-v1", "render-v1",
            1, "synthetic inventory", budget, RunTrace(tmp_path), visual_model=True, audit=audit)
        assert findings == []
    else:
        proposal = _live_lane_a_builder(budget, RunTrace(tmp_path), payload={"stage": "repair"},
            images=[input_image] * count, proposal_id="synthetic-builder", audit=audit)
        assert proposal.header_contact_labels is False
    assert budget.model_requests == 1
    assert len(requests) == 1 and requests[0] < 512000
    record = json.loads((tmp_path / "agent_messages.jsonl").read_text())
    images = record["messages"][0]["parts"][0]["content"][1:]
    assert len(images) == count
    assert all(image["media_type"] == "image/webp" and image["path"] == prepared.name
               and image["sha256"] == hashlib.sha256(prepared.read_bytes()).hexdigest() for image in images)
