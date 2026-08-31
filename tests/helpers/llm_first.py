"""Deterministic llm_first-path builders for offline tests (mock provider).

The LLM-first segmentation path (ADR 0005) is the only candidate
segmentation path. These helpers drive it end-to-end with MockLLMClient so
unit and integration tests never call a live provider.
"""

from app.extraction.llm_extractor import MockLLMClient
from app.extraction.llm_segmentation import (
    materialize_llm_segmentation,
    segment_and_extract_resume,
)


def llm_first_document(source_text: str):
    """Normalize one resume into a NormalizedCandidateDocument via the mock."""
    _, document = materialize_llm_segmentation(
        source_text, segment_and_extract_resume(source_text, MockLLMClient())
    )
    return document


def llm_first_profile(source_text: str):
    """Extract one resume into a CandidateProfile via the mock LLM path."""
    profile, _ = materialize_llm_segmentation(
        source_text, segment_and_extract_resume(source_text, MockLLMClient())
    )
    return profile