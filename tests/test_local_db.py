from pathlib import Path

from app.extraction.candidate_schema import MissingField
from app.storage.local_db import LocalArtifactStore


def test_local_artifact_store_records_and_updates_artifact_metadata(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "cv_reformatter.sqlite3")
    artifact_dir = tmp_path / "artifact_demo"
    artifact_dir.mkdir()
    missing_fields = [
        MissingField(
            field_name="salary_expectation",
            label="Salary expectation",
            reason="Not present in source resume.",
        )
    ]

    store.record_processed_resume(
        artifact_id="artifact_demo",
        artifact_dir=artifact_dir,
        source_filename="resume.docx",
        source_file_type="docx",
        original_pdf_preview_url="/api/artifacts/artifact_demo/original_resume_preview.pdf",
        original_preview_error=None,
        missing_fields=missing_fields,
        debug_artifacts={"candidate_profile": "candidate_profile.json"},
    )
    store.record_target_format(
        artifact_id="artifact_demo",
        artifact_dir=artifact_dir,
        target_format_role="pdf_reference",
        target_format_filename="sample.pdf",
        target_format_download_url="/api/artifacts/artifact_demo/target_format_reference.pdf",
        debug_artifacts={"target_format": "target_format.json"},
    )
    store.record_generated_outputs(
        artifact_id="artifact_demo",
        artifact_dir=artifact_dir,
        template_name="apex_standard",
        blind_profile=True,
        docx_download_url="/api/artifacts/artifact_demo/candidate_profile.docx",
        pdf_download_url="/api/artifacts/artifact_demo/candidate_profile.pdf",
        pdf_preview_url="/api/artifacts/artifact_demo/candidate_profile.pdf",
        missing_fields=missing_fields,
        debug_artifacts={"generated_pdf": "candidate_profile.pdf"},
    )

    metadata = store.get_artifact("artifact_demo")
    assert metadata is not None
    assert metadata["status"] == "generated"
    assert metadata["source_filename"] == "resume.docx"
    assert metadata["target_format_role"] == "pdf_reference"
    assert metadata["blind_profile"] is True
    assert metadata["missing_field_labels"] == ["Salary expectation"]
    assert metadata["debug_artifacts"] == {
        "candidate_profile": "candidate_profile.json",
        "target_format": "target_format.json",
        "generated_pdf": "candidate_profile.pdf",
    }

    artifacts = store.list_artifacts()
    assert [artifact["artifact_id"] for artifact in artifacts] == ["artifact_demo"]
