import json

import pytest
from pydantic import ValidationError

from app.extraction.coverage_audit import audit_segmentation_lines
from app.extraction.llm_segmentation import (
    LLMSegmentationOutput,
    materialize_llm_segmentation,
    segment_and_extract_resume,
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
                    "items": ["Resume Builder", "• Preserved every source", "line"],
                    "additional_entries": [
                        {
                            "title": "Resume Builder",
                            "description": ["Preserved every source line"],
                        }
                    ],
                },
                {
                    "heading": "EXPERIENCE",
                    "section_type": "work_experience",
                    "items": ["Example Ltd", "• Built fault-tolerant", "services for clients.", "• Monitored results."],
                    "work_experience": [{"company": "Example Ltd", "description": [
                        "Built fault-tolerant services for clients.", "Monitored results.",
                    ]}],
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
    source = ("Jane Candidate\njane@example.com\nPROJECTS\nResume Builder\n• Preserved every source\nline\n"
              "EXPERIENCE\nExample Ltd\n• Built fault-tolerant\nservices for clients.\n• Monitored results.\n")

    audit = audit_segmentation_lines(source, segmentation_output_lines(output))
    profile, document = materialize_llm_segmentation(source, output)

    assert audit.status == "passed"
    assert [block.normalized_heading for block in document.blocks] == [
        "contact",
        "projects",
        "experience",
    ]
    projects = profile.additional_sections[0]
    assert projects.source_block_id == "candidate-block-002"
    assert projects.entries[0].source_block_ids == ["candidate-block-002"]
    assert projects.entries[0].description == ["Preserved every source line"]
    assert profile.work_experience[0].description == [
        "Built fault-tolerant services for clients.", "Monitored results.",
    ]
    assert document.blocks[2].raw_text == "Example Ltd\n• Built fault-tolerant\nservices for clients.\n• Monitored results."
    merged_evidence = [line for line in segmentation_output_lines(output) if line != "services for clients."]
    merged_evidence[merged_evidence.index("• Built fault-tolerant")] += " services for clients."
    assert audit_segmentation_lines(source, merged_evidence).approval_blocked


def test_provider_instructions_distinguish_verbatim_evidence_from_logical_descriptions() -> None:
    class CapturingClient:
        def complete_json(self, system, prompt, **kwargs):
            self.system, self.prompt, self.response_model = system, prompt, kwargs["response_model"]
            return _output().model_dump_json()

    client = CapturingClient()
    segment_and_extract_resume("• Built fault-tolerant\nservices for clients.", client)

    assert "Instruction version: llm-segmentation/2" in client.system
    assert "response schema: llm_segmentation/1" in client.system
    assert "heading and items only" in client.system
    assert "work_experience[].description" in client.system
    assert "additional_entries[].description" in client.system
    assert "logical bullets or paragraphs" in client.system
    assert client.response_model is LLMSegmentationOutput
    assert json.loads(client.prompt.split("SOURCE LINES JSON:\n")[1].split("\n\n")[0]) == [
        "• Built fault-tolerant", "services for clients.",
    ]
