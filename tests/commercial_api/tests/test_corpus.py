from __future__ import annotations

from pathlib import Path

import pytest

from tests.commercial_api.corpus import (
    CorpusError,
    case_by_id,
    cases_for_lane,
    load_corpus_manifest,
    materialize_case_input,
    sha256_file,
)


@pytest.fixture(scope="module")
def manifest():
    return load_corpus_manifest()


def test_manifest_schema_and_version(manifest) -> None:
    assert manifest.schema_version == "commercial_api/corpus/1"
    assert manifest.corpus_version == "1.1"
    assert len(manifest.cases) >= 16


def test_every_case_declares_lane_applicability(manifest) -> None:
    for case in manifest.cases:
        assert case.case_id
        assert case.lanes, f"{case.case_id} declares no lanes"
        assert case.source_format


def test_extraction_case_declares_expected_profile_and_negative_conditions(manifest) -> None:
    case = case_by_id(manifest, "synthetic_candidate_profile")
    assert case.expected_profile == "expected_candidate_profile.json"
    assert case.negative_conditions
    assert case.thresholds["null_safety_min"] == 1.0


def test_rendering_case_requires_manual_review_and_privacy_exclusions(manifest) -> None:
    case = case_by_id(manifest, "controlled_docx_to_pdf")
    assert case.requires_manual_review is True
    assert case.privacy_exclusions


def test_all_materialized_inputs_match_pinned_checksums(tmp_path: Path, manifest) -> None:
    for case in cases_for_lane(manifest, "layout"):
        path = materialize_case_input(case, tmp_path)
        assert path.is_file()
        assert case.expected_sha256
        assert sha256_file(path) == case.expected_sha256


def test_text_input_materializes_with_checksum_verification(tmp_path: Path, manifest) -> None:
    case = case_by_id(manifest, "synthetic_candidate_profile")
    path = materialize_case_input(case, tmp_path)
    assert path.name == "synthetic_resume.txt"
    assert path.read_text(encoding="utf-8").startswith("Jordan Example")


def test_drift_detected_by_pinned_checksum(tmp_path: Path) -> None:
    source = tmp_path / "drifted.txt"
    source.write_text("content", encoding="utf-8")
    from tests.commercial_api.models import CorpusCase

    case = CorpusCase(
        case_id="drift",
        source_format="text",
        lanes=["layout"],
        input=source.name,
        expected_sha256="0" * 64,
    )
    import tests.commercial_api.corpus as corpus_module

    original_dir = corpus_module.CORPUS_DIR
    try:
        corpus_module.CORPUS_DIR = tmp_path
        with pytest.raises(CorpusError, match="drifted"):
            materialize_case_input(case, tmp_path / "out")
    finally:
        corpus_module.CORPUS_DIR = original_dir
