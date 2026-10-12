import pytest
from pydantic import ValidationError

from app.extraction.coverage_audit import audit_segmentation_lines
from app.extraction.llm_segmentation import (
    LLMSegmentationOutput,
    materialize_llm_segmentation,
    segmentation_output_lines,
)


def _output() -> LLMSegmentationOutput:
    return LLMSegmentationOutput.model_validate(
        {
            "schema_version": "llm_segmentation/1",
            "sections": [
                {
                    "heading": None,
                    "section_type": "contact",
                    "items": ["Jane Candidate", "jane@example.com"],
                    "full_name": "Jane Candidate",
                    "email": "jane@example.com",
                },
                {
                    "heading": "PROJECTS",
                    "section_type": "projects",
                    "items": ["Resume Builder", "• Preserved every source line"],
                    "additional_entries": [
                        {
                            "title": "Resume Builder",
                            "description": ["Preserved every source line"],
                        }
                    ],
                },
            ],
        }
    )


def test_coverage_audit_catches_missing_and_invented_lines() -> None:
    audit = audit_segmentation_lines(
        "Jane Candidate\nSKILLS\nPython\n",
        ["Jane Candidate", "SKILLS", "Rust"],
    )

    assert audit.status == "failed"
    assert audit.approval_blocked is True
    assert [(item.line, item.count) for item in audit.missing_lines] == [("Python", 1)]
    assert [item.line for item in audit.invented_or_altered_lines] == ["Rust"]


def test_segmentation_schema_rejects_malformed_output() -> None:
    payload = _output().model_dump(mode="json")
    payload["sections"][0]["unexpected"] = "not allowed"

    with pytest.raises(ValidationError):
        LLMSegmentationOutput.model_validate(payload)


def test_materialized_profile_and_blocks_keep_llm_section_lineage() -> None:
    output = _output()
    source = "Jane Candidate\njane@example.com\nPROJECTS\nResume Builder\n• Preserved every source line\n"

    audit = audit_segmentation_lines(source, segmentation_output_lines(output))
    profile, document = materialize_llm_segmentation(source, output)

    assert audit.status == "passed"
    assert [block.normalized_heading for block in document.blocks] == [
        "contact",
        "projects",
    ]
    projects = profile.additional_sections[0]
    assert projects.source_block_id == "candidate-block-002"
    assert projects.entries[0].source_block_ids == ["candidate-block-002"]


def test_unheaded_summary_preserves_lines_and_structured_text_does_not_waive_audit() -> None:
    lines = ["Builds reliable document", "workflows with strict validation."]
    output = LLMSegmentationOutput.model_validate({"sections": [
        {"heading": None, "section_type": "contact", "items": ["Synthetic Candidate"], "full_name": "Synthetic Candidate"},
        {"heading": None, "section_type": "summary", "items": lines, "professional_summary": " ".join(lines)},
    ]})
    source = "\n".join(["Synthetic Candidate", *lines])
    assert audit_segmentation_lines(source, segmentation_output_lines(output)).status == "passed"
    profile, document = materialize_llm_segmentation(source, output)
    assert profile.professional_summary == "Builds reliable document workflows with strict validation."
    summary = document.blocks[1]
    assert summary.candidate_source == "summary" and summary.source_heading is None
    assert summary.items == lines and (summary.source_line_start, summary.source_line_end) == (2, 3)

    output.sections[1].items = lines[:1]
    audit = audit_segmentation_lines(source, segmentation_output_lines(output))
    assert audit.approval_blocked and [item.line for item in audit.missing_lines] == [lines[1]]
    assert output.sections[1].professional_summary == profile.professional_summary
