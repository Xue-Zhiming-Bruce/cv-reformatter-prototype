"""Measured synthetic Adobe evidence for offline production-path tests."""

from __future__ import annotations

from pathlib import Path

from app.template_analysis.commercial.bridge import build_design_evidence_from_normalized
from app.template_analysis.artifacts import (
    LAYOUT_SPEC_FILENAME,
    NORMALIZED_LAYOUT_FILENAME,
    TARGET_EVIDENCE_FILENAME,
)
from app.template_analysis.commercial.models import (
    MeasurementProvenance,
    NormalizedBox,
    NormalizedLayoutEvidence,
    NormalizedPage,
    NormalizedRule,
    NormalizedTextBlock,
)
from app.template_analysis.design_evidence import target_checksum_from_bytes
from app.template_analysis.design_compiler import compile_measured_layout_template_spec
from app.template_analysis.schemas import (
    AnalysisArtifactManifest,
    BoundingBox,
    PageLayout,
    TargetPdfAnalysis,
    TextBlock,
)


def measured_adobe_evidence() -> NormalizedLayoutEvidence:
    rows = [
        ("title", "Synthetic Target", 16.0, True, "#17365D", 0.08, 0.55, 0.003),
        ("body", "jane@example.test | +1 555 0100 | Boston", 9.5, False, "#252525", 0.08, 0.92, 0.08),
        ("body", "Measured summary", 10.5, False, "#252525", 0.08, 0.92, 0.14),
        ("heading_candidate", "SKILLS", 13.0, True, "#1F4E79", 0.08, 0.92, 0.20),
        ("body", "Alex Example", 10.5, False, "#252525", 0.08, 0.92, 0.27),
        ("body", "Measured body line two", 10.5, False, "#252525", 0.08, 0.92, 0.32),
        ("heading_candidate", "WORK EXPERIENCE", 13.0, True, "#1F4E79", 0.08, 0.92, 0.38),
        ("body", "Measured body line three", 10.5, False, "#252525", 0.08, 0.92, 0.45),
        ("heading_candidate", "EDUCATION", 13.0, True, "#1F4E79", 0.08, 0.92, 0.56),
        ("body", "Measured body line four", 10.5, False, "#252525", 0.08, 0.92, 0.63),
        ("heading_candidate", "LANGUAGES", 13.0, True, "#1F4E79", 0.08, 0.92, 0.72),
        ("body", "Measured body line five", 10.5, False, "#252525", 0.08, 0.92, 0.79),
    ]
    blocks = [
        NormalizedTextBlock(
            element_id=f"adobe.page.1.element.{index}", text=text,
            page_number=1, reading_order=index,
            bbox=NormalizedBox(x0=x0, top=top, x1=x1, bottom=top + 0.035),
            structural_role=role, font_family="Helvetica", font_size_pt=size,
            bold=bold, italic=False, color_hex=color,
            line_height_pt=size * 1.2, spacing_before_pt=6.0 if "heading" in role else 0.0,
            spacing_after_pt=3.0,
        )
        for index, (role, text, size, bold, color, x0, x1, top) in enumerate(rows)
    ]
    rules = [
        NormalizedRule(
            element_id=f"local.rule.{index}",
            page_number=1,
            bbox=NormalizedBox(x0=0.08, top=top, x1=0.92, bottom=top),
            stroke_width_pt=0.75,
            gap_above_pt=3.0,
            gap_below_pt=6.0,
            color_hex="#1F4E79",
            provenance=MeasurementProvenance(
                source="local_pdf",
                provider="offline-test",
                method="synthetic_fixture",
            ),
        )
        for index, top in enumerate((0.12, 0.24, 0.42, 0.60, 0.76))
    ]
    return NormalizedLayoutEvidence(
        provider="adobe", provider_version="offline-test",
        pages=[NormalizedPage(page_number=1, width_pt=595.28, height_pt=841.89)],
        page_count=1, full_text="\n".join(block.text for block in blocks),
        text_blocks=blocks,
        rules=rules,
    )


def mock_run_adobe_layout(_pdf_path: Path):
    return measured_adobe_evidence(), {"provider": "adobe", "archived": True}, b""


def build_synthetic_target_analysis(pdf_path: str | Path, output_dir: str | Path) -> TargetPdfAnalysis:
    normalized = measured_adobe_evidence()
    checksum = target_checksum_from_bytes(Path(pdf_path).read_bytes())
    evidence, style = build_design_evidence_from_normalized(normalized, target_checksum=checksum)
    page = normalized.pages[0]
    blocks = [
        TextBlock(
            text=block.text,
            bbox=BoundingBox(
                x0=block.bbox.x0 * page.width_pt, top=block.bbox.top * page.height_pt,
                x1=block.bbox.x1 * page.width_pt, bottom=block.bbox.bottom * page.height_pt,
            ),
            font_family=block.font_family or "", font_size_pt=block.font_size_pt or 1,
            bold=bool(block.bold), italic=bool(block.italic), color_hex=block.color_hex or "#FFFFFF",
            role="heading" if block.structural_role == "heading_candidate" else ("title" if block.structural_role == "title" else "body"),
        )
        for block in normalized.text_blocks if block.bbox is not None
    ]
    layout = compile_measured_layout_template_spec(evidence, style)
    resolved_output = Path(output_dir)
    resolved_output.mkdir(parents=True, exist_ok=True)
    for filename, model in (
        (NORMALIZED_LAYOUT_FILENAME, normalized),
        (TARGET_EVIDENCE_FILENAME, evidence),
        (LAYOUT_SPEC_FILENAME, layout),
    ):
        (resolved_output / filename).write_text(
            model.model_dump_json(indent=2), encoding="utf-8"
        )
    return TargetPdfAnalysis(
        page_count=1, text_character_count=sum(len(block.text) for block in blocks),
        pages=[PageLayout(page_number=1, width_pt=page.width_pt, height_pt=page.height_pt, text_blocks=blocks)],
        style_spec=style, layout_spec=layout,
        artifacts=AnalysisArtifactManifest(
            raw_layout_filename="unused.json", style_spec_filename="template_style_spec.json",
            layout_spec_filename="layout_template_spec.json",
        ),
    )
