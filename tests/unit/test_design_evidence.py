"""Opaque external request and typography-only heading nomination boundaries."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from app.template_analysis.commercial.bridge import derive_heading_candidates
from app.template_analysis.commercial.models import NormalizedTextBlock
from app.template_analysis.design_evidence import build_design_request
from app.template_analysis.design_schemas import (
    CandidateSectionRole,
    RegionReference,
    StyleRoleReference,
    TargetLayoutEvidence,
)


def _region(region_id: str, label: str, size: float, typography: str) -> RegionReference:
    return RegionReference(
        region_id=region_id,
        page_number=1,
        semantic_role="other",
        label=label,
        style_role_ref=f"element.{region_id}",
        font_size_pt=size,
        bold=size > 10,
        typography_class=typography,
    )


def _evidence() -> TargetLayoutEvidence:
    regions = [
        _region("e0", "ALEX EXAMPLE", 24, "title"),
        _region("e1", "NONSTANDARD CAREER CHRONICLE", 14, "heading"),
        _region("e2", "Built private customer systems", 10, "body"),
    ]
    return TargetLayoutEvidence(
        evidence_version="normalized-layout/1",
        target_checksum="sha",
        target_format="pdf",
        page_count=1,
        text_block_count=3,
        layout_class_hint="one_column",
        regions=regions,
        style_roles=[
            StyleRoleReference(
                role_id=f"element.{region.region_id}", label="Measured",
                font_family="Lato", font_size_pt=region.font_size_pt,
                bold=region.bold, color_hex="#112233",
            )
            for region in regions
        ],
    )


def _sections() -> list[CandidateSectionRole]:
    return [
        CandidateSectionRole(role="work_experience", section_label="Work Experience", item_count=2),
        CandidateSectionRole(role="skills", section_label="Skills", item_count=4),
    ]


def test_external_request_contains_all_elements_but_no_target_text() -> None:
    request = build_design_request(_evidence(), _sections())
    assert [region.region_id for region in request.measured_regions] == ["e0", "e1", "e2"]
    assert all(region.label is None for region in request.measured_regions)
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    for forbidden in (
        "ALEX EXAMPLE", "NONSTANDARD CAREER CHRONICLE", "Built private customer systems"
    ):
        assert forbidden not in payload
    assert "font_size_pt" in payload
    assert "typography_class" in payload


def test_target_source_text_is_contained_outside_generation() -> None:
    evidence = _evidence().model_copy(
        update={"local_candidate_texts": ["PRIVATE TARGET EMPLOYER"]}
    )
    request = build_design_request(evidence, _sections())
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    assert "PRIVATE TARGET EMPLOYER" not in payload

    generation_dir = Path(__file__).resolve().parents[2] / "app" / "generation"
    for source_path in generation_dir.glob("*.py"):
        if source_path.name in {"docx_renderer.py", "html_renderer.py"}:
            continue
        source = source_path.read_text(encoding="utf-8")
        assert "local_candidate_texts" not in source
        assert "target_body_texts" not in source


def test_heading_derivation_is_independent_of_text_content() -> None:
    shared = "size:14.00|weight:bold|style:normal|decoration:plain"
    blocks = [
        NormalizedTextBlock(
            element_id="a", text="EXPERIENCE", page_number=1, reading_order=0,
            font_family="Lato", font_size_pt=14, bold=True, color_hex="#000000",
            spacing_before_pt=8, typography_class=shared,
        ),
        NormalizedTextBlock(
            element_id="b", text="Completely unrelated words", page_number=1, reading_order=1,
            font_family="Lato", font_size_pt=14, bold=True, color_hex="#000000",
            spacing_before_pt=8, typography_class=shared,
        ),
        NormalizedTextBlock(
            element_id="c", text="Body", page_number=1, reading_order=2,
            font_family="Lato", font_size_pt=10, bold=False, color_hex="#000000",
            typography_class="size:10.00|weight:regular|style:normal|decoration:plain",
        ),
    ]
    assert [block.element_id for block in derive_heading_candidates(blocks)] == ["a", "b"]


def test_heading_derivation_contains_no_lexical_alias_logic() -> None:
    import app.template_analysis.commercial.bridge as bridge
    import app.template_analysis.design_evidence as evidence

    source = inspect.getsource(bridge.derive_heading_candidates) + inspect.getsource(
        evidence.build_design_request
    )
    assert "_SECTION_ALIASES" not in source
    assert "normalized_label" not in source
    assert "alias" not in source.lower()
