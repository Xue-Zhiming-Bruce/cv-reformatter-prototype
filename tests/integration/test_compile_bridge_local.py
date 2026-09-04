"""Offline A-F acceptance replay for compile bridge v1."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from app.extraction.candidate_schema import CandidateProfile
from app.generation.template_mapper import build_client_render_context
from app.template_analysis.commercial import (
    build_design_evidence_from_normalized,
    enrich_badges_from_local_pdf,
    enrich_colors_from_local_pdf,
    enrich_rules_from_local_pdf,
    normalize_adobe_layout,
)
from app.template_analysis.design_compiler import compile_layout_template_spec
from app.template_analysis.design_evidence import build_design_request
from app.template_analysis.design_schemas import (
    CandidateSectionRole,
    DesignProposal,
    SectionMapping,
)
from app.template_analysis.design_validator import validate_design_proposal

pytestmark = pytest.mark.local_dataset

ROOT = Path(__file__).resolve().parents[2]
RAW_RUN = ROOT / "tests/test_results/commercial_api/commercial_api_20260823T120612498974Z/cases/layout"
PDF_ROOT = ROOT / "tests/local_datasets/resume_matrix"

SECTIONS = [
    CandidateSectionRole(role="contact", section_label="Contact", item_count=1),
    CandidateSectionRole(role="summary", section_label="Summary", item_count=1),
    CandidateSectionRole(role="skills", section_label="Skills", item_count=3),
    CandidateSectionRole(role="languages", section_label="Languages", item_count=1),
    CandidateSectionRole(role="work_experience", section_label="Work Experience", item_count=2),
    CandidateSectionRole(role="education", section_label="Education", item_count=1),
    CandidateSectionRole(role="certifications", section_label="Certifications", item_count=1),
    CandidateSectionRole(role="additional_details", section_label="Additional Details", item_count=1),
]


def _proposal(evidence) -> DesignProposal:
    candidates = [region for region in evidence.regions if region.semantic_role == "heading"]
    by_class = Counter(region.typography_class for region in candidates if region.typography_class)
    visual_class = by_class.most_common(1)[0][0]
    headings = [region for region in candidates if region.typography_class == visual_class]
    mappings: list[SectionMapping] = []
    for index, section in enumerate(SECTIONS):
        region = headings[index] if index < len(headings) else None
        mappings.append(SectionMapping(
            mapping_id=f"m{index}", source_role=section.role,
            target_region_id=region.region_id if region else None,
            target_semantic_role="full_width",
            target_label=section.section_label,
            action="map" if region else "preserve_as_additional",
            confidence=1,
            evidence_refs=[region.region_id] if region else [],
        ))
    return DesignProposal(
        proposal_id="offline-af", layout_class=evidence.layout_class_hint,
        section_order=[mapping.mapping_id for mapping in mappings], mappings=mappings,
        style_role_refs=[
            "global.heading",
            *[region.style_role_ref for region in headings if region.style_role_ref],
        ],
        overflow_policy="continue_next_page", confidence=1,
        reason_codes=["injected_offline_semantic_proposal"],
    )


@pytest.mark.parametrize("letter", list("ABCDEF"))
def test_archived_adobe_af_fails_closed_when_color_is_unmeasured(letter: str, tmp_path: Path) -> None:
    pdf = PDF_ROOT / f"resume_{letter}.pdf"
    raw_path = RAW_RUN / f"resume_{letter}/adobe/raw_redacted.json"
    if not pdf.is_file() or not raw_path.is_file():
        pytest.skip("authorized local A-F replay corpus is unavailable")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    normalized = normalize_adobe_layout(raw)
    with pytest.raises(Exception, match="lacks measured color_hex"):
        build_design_evidence_from_normalized(
            normalized, target_checksum=hashlib.sha256(pdf.read_bytes()).hexdigest()
        )


@pytest.mark.parametrize("letter", list("ABCDEF"))
def test_archived_adobe_af_compiles_with_local_color_enrichment(letter: str) -> None:
    """ADR 0002 amendment: local pdfplumber color measurement unblocks the bridge."""
    pdf = PDF_ROOT / f"resume_{letter}.pdf"
    raw_path = RAW_RUN / f"resume_{letter}/adobe/raw_redacted.json"
    if not pdf.is_file() or not raw_path.is_file():
        pytest.skip("authorized local A-F replay corpus is unavailable")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    normalized = enrich_colors_from_local_pdf(normalize_adobe_layout(raw), pdf)
    normalized = enrich_badges_from_local_pdf(normalized, pdf)
    normalized = enrich_rules_from_local_pdf(normalized, pdf)

    measured = [b for b in normalized.text_blocks if b.text.strip()]
    colored = [b for b in measured if b.color_hex is not None]
    assert colored, f"resume_{letter}: local measurement produced no colors"
    assert all(
        b.provenance["color_hex"].source == "local_pdf" for b in colored
    )

    evidence, style = build_design_evidence_from_normalized(
        normalized, target_checksum=hashlib.sha256(pdf.read_bytes()).hexdigest()
    )
    # the measured majority colors propagate into the compiled spec heading
    heading_colors = {
        block.color_hex
        for block in colored
        if block.bold and (block.font_size_pt or 0) >= 11.0
    }
    assert style.heading.color_hex in heading_colors or heading_colors == set()
    assert evidence.layout_class_hint in {"one_column", "two_column", "sidebar", "unknown"}
    if letter == "A":
        assert style.decoration.header_rule is False
        assert style.decoration.heading_rule is False
    elif letter in {"B", "C"}:
        assert style.body.font_size_pt == {"B": 10.5, "C": 10.0}[letter]
        assert style.heading.character_spacing_pt == pytest.approx(0.8, abs=0.05)
        assert style.decoration.header_rule is True
        assert style.decoration.heading_rule is True
        expected = {
            "B": (6.84, 9.78, 4.20, 4.00),
            "C": (6.94, 10.07, 4.12, 4.28),
        }[letter]
        assert style.decoration.header_rule_gap_above_pt == pytest.approx(expected[0], abs=0.1)
        assert style.decoration.header_rule_gap_below_pt == pytest.approx(expected[1], abs=0.1)
        assert style.decoration.heading_rule_gap_above_pt == pytest.approx(expected[2], abs=0.1)
        assert style.decoration.heading_rule_gap_below_pt == pytest.approx(expected[3], abs=0.1)
        assert style.decoration.header_rule_length_pt == pytest.approx(463.276, abs=0.1)
        assert style.decoration.heading_rule_length_pt == pytest.approx(463.276, abs=0.1)
        request = build_design_request(evidence, SECTIONS)
        layout = compile_layout_template_spec(
            request,
            _proposal(evidence),
            evidence,
            style,
        )
        assert layout.header.show_candidate_subheading is False
        assert layout.header.contact_fields == [
            "email",
            "phone",
            "location",
            "linkedin_url",
        ]
        assert layout.header.contact_style is not None
        assert layout.header.contact_style.font_size_pt == {"B": 9.5, "C": 9.0}[letter]
        assert [section.label for section in layout.sections] == [
            "",
            "SKILLS",
            "EXPERIENCE",
            "PROJECTS",
            "EDUCATION",
            "CERTIFICATIONS",
            "ADDITIONAL SECTIONS",
            "LANGUAGES",
            "ADDITIONAL LINKS OR DATA",
        ]
        assert layout.sections[0].show_heading is False
        assert all(
            section.heading_style is None
            or section.heading_style.character_spacing_pt == pytest.approx(0.8, abs=0.05)
            for section in layout.sections
        )
        experience = next(
            section for section in layout.sections if section.source == "work_experience"
        )
        assert experience.entry_title_style is not None
        assert experience.entry_title_style.font_size_pt == {"B": 11.5, "C": 11.0}[letter]
        links = layout.sections[-1]
        assert links.contact_fields == ["portfolio_url"]
        skills = next(section for section in layout.sections if section.source == "skills")
        if letter == "B":
            assert normalized.badge_clusters == []
            assert skills.badge_style is None
            assert skills.skill_group_gap_pt == pytest.approx(3.0, abs=0.1)
            assert skills.skill_first_row_adjustment_pt == pytest.approx(-0.81, abs=0.1)
            assert experience.entry_gap_pt == pytest.approx(5.67, abs=0.1)
        else:
            assert len(normalized.badge_clusters) == 1
            cluster = normalized.badge_clusters[0]
            assert cluster.badge_count == 18
            assert cluster.fill_color_hex == "#99F6E4"
            assert cluster.text_color_hex == "#334155"
            assert cluster.height_pt == 18
            assert cluster.items_per_line == [6, 6, 6]
            assert cluster.provenance.source == "local_pdf"
            assert skills.badge_style is not None
            assert skills.badge_style.badge_fill_hex == "#99F6E4"
            assert skills.badge_style.badge_text_hex == "#334155"


def test_nonstandard_target_identity_uses_opaque_ids() -> None:
    raw_path = RAW_RUN / "resume_D/adobe/raw_redacted.json"
    if not raw_path.is_file():
        pytest.skip("authorized local target D is unavailable")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    target_text = "\n".join(
        str(element.get("Text") or "")
        for element in raw["structured_data"].get("elements", [])
    )
    assert "HIGHLIGHTS" in target_text and "ANOTHER SECTION" in target_text
    normalized = normalize_adobe_layout(raw).model_copy(
        update={
            "text_blocks": [
                block.model_copy(update={"color_hex": "#252525"})
                for block in normalize_adobe_layout(raw).text_blocks
            ]
        }
    )
    evidence, _ = build_design_evidence_from_normalized(normalized, target_checksum="sha")
    request = build_design_request(evidence, SECTIONS)
    payload = json.dumps(request.model_dump(mode="json"))
    assert "HIGHLIGHTS" not in payload
    assert "ANOTHER SECTION" not in payload
    assert any(region.typography_class for region in request.measured_regions)
