"""Focused API tests: mandatory recruiter profile approval on every generation path.

Every final generation path (built-in template, existing uploaded-target
template, approved designer template) must require an artifact-scoped, current,
non-superseded approved profile version. Generation must render from the
persisted approved profile, never from a profile supplied directly in the
request. Negative cases: missing approval (409), superseded approval (409),
wrong artifact (400), changed source draft / target checksum (400), invalid
approval token (400).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app import main
from app.extraction.candidate_schema import CandidateProfile
from app.main import app
from app.template_analysis import layout_proof_service
from tests.helpers.layout_proof import (
    create_and_approve_proofs,
    make_proof_pdf_exporter,
)
from tests.helpers.llm_first import llm_first_document
from tests.helpers.synthetic_pdf import build_styled_target_pdf

client = TestClient(app)


@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "local.sqlite3")
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path / "outputs")
    exporter = make_proof_pdf_exporter()
    monkeypatch.setattr(main, "export_html_to_pdf", exporter)
    monkeypatch.setattr(layout_proof_service, "export_html_to_pdf", exporter)


def _profile(*, full_name: str = "Jane Candidate") -> CandidateProfile:
    return CandidateProfile(
        full_name=full_name,
        email="jane@example.org",
        location="Boston",
        professional_summary="Experienced engineer.",
        skills=["Python", "SQL"],
        languages=[{"name": "English", "proficiency": "Fluent"}],
        work_experience=[
            {
                "company": "DataWorks",
                "title": "Senior Engineer",
                "start_date": "2020",
                "end_date": "2024",
                "description": ["Built analytics pipelines."],
            }
        ],
        education=[{"institution": "Example University", "degree": "BS"}],
    )


def _approve_profile(
    artifact_id: str, profile: CandidateProfile | None = None
) -> str:
    """Approve a profile through the API and return the immutable version id."""
    response = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={
            "profile": (profile or _profile()).model_dump(mode="json"),
            "reviewer_note": "recruiter approved",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["profile_version_id"]


def _make_artifact_with_approval(
    tmp_path: Path,
    artifact_id: str = "artifact_approved_1",
    *,
    profile: CandidateProfile | None = None,
) -> tuple[str, Path, str]:
    """Create an artifact directory with an extraction draft and approve a
    profile for it through the API."""
    artifact_dir = tmp_path / "outputs" / artifact_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    draft = profile or _profile()
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(draft.model_dump(mode="json")), encoding="utf-8"
    )
    version_id = _approve_profile(artifact_id, draft)
    return artifact_id, artifact_dir, version_id


def test_approval_rejects_silently_deleted_source_section(tmp_path: Path) -> None:
    artifact_id = "artifact_source_coverage"
    artifact_dir = tmp_path / "outputs" / artifact_id
    artifact_dir.mkdir(parents=True)
    source = "Jane Candidate\nPROJECTS\nResume Builder\n"
    document = llm_first_document(source)
    draft = _profile()
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(draft.model_dump(mode="json")), encoding="utf-8"
    )
    (artifact_dir / "normalized_candidate_document.json").write_text(
        document.model_dump_json(), encoding="utf-8"
    )

    response = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={"profile": draft.model_dump(mode="json")},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error_code"] == "source_content_unaccounted"
    assert detail["source_blocks"][0]["source_heading"] == "PROJECTS"


def test_approval_rejects_failed_llm_segmentation_audit(tmp_path: Path) -> None:
    artifact_id = "artifact_segmentation_coverage"
    artifact_dir = tmp_path / "outputs" / artifact_id
    artifact_dir.mkdir(parents=True)
    draft = _profile()
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(draft.model_dump(mode="json")), encoding="utf-8"
    )
    (artifact_dir / "segmentation_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "segmentation-audit/1",
                "status": "failed",
                "source_line_count": 2,
                "output_line_count": 2,
                "matched_line_count": 1,
                "missing_lines": [{"line": "Python", "count": 1}],
                "invented_or_altered_lines": [{"line": "Rust", "count": 1}],
                "detail": None,
            }
        ),
        encoding="utf-8",
    )

    response = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={"profile": draft.model_dump(mode="json")},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error_code"] == "segmentation_coverage_failed"
    assert detail["missing_lines"] == [
        {
            "line": "Python",
            "count": 1,
            "source_line_numbers": [],
            "output_block_ids": [],
        }
    ]
    assert detail["invented_or_altered_lines"] == [
        {
            "line": "Rust",
            "count": 1,
            "source_line_numbers": [],
            "output_block_ids": [],
        }
    ]


def _upload_target(tmp_path: Path, artifact_id: str) -> dict[str, object]:
    target = build_styled_target_pdf(tmp_path / "client-format.pdf")
    response = client.post(
        "/api/target-format",
        data={"artifact_id": artifact_id},
        files={"file": ("client-format.pdf", target.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    target_format = response.json()["target_format"]
    assert target_format["used_as_template_source"] is True
    return target_format


def _generate(
    artifact_id: str,
    version_id: str | None,
    *,
    target_format: dict[str, object] | None = None,
    design_id: str | None = None,
    request_profile: dict[str, object] | None = None,
    **extra: object,
):
    payload: dict[str, object] = {
        "artifact_id": artifact_id,
        "approved_profile_version_id": version_id or "",
    }
    if target_format is not None:
        payload["target_format"] = target_format
    if design_id is not None:
        payload["design_id"] = design_id
    # Legacy ``profile`` field: still accepted by the schema for backward
    # compatibility but must be ignored — generation uses the persisted
    # approved profile only.
    if request_profile is not None:
        payload["profile"] = request_profile
    payload.update(extra)
    return client.post("/api/generate", json=payload)


# -- success with valid approval for each template category -------------------


def test_builtin_generation_success_with_approved_profile(tmp_path: Path) -> None:
    artifact_id, artifact_dir, version_id = _make_artifact_with_approval(tmp_path)

    response = _generate(artifact_id, version_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["artifact_id"] == artifact_id
    assert (artifact_dir / "candidate_profile.html").exists()
    assert (artifact_dir / "candidate_profile.pdf").exists()
    assert body["debug_artifacts"]["approved_profile_version_id"] == version_id


def test_generation_uses_persisted_approved_profile_not_request_profile(
    tmp_path: Path,
) -> None:
    """A profile supplied directly in the request body is ignored: the output
    contains the persisted approved profile's facts, not the request data."""
    artifact_id, artifact_dir, version_id = _make_artifact_with_approval(
        tmp_path, profile=_profile(full_name="Approved Jane")
    )

    response = _generate(
        artifact_id,
        version_id,
        request_profile=_profile(full_name="Untrusted Request Name").model_dump(
            mode="json"
        ),
    )
    assert response.status_code == 200, response.text
    text = (artifact_dir / "candidate_profile.html").read_text(encoding="utf-8")
    assert "Approved Jane" in text
    assert "Untrusted Request Name" not in text


def test_uploaded_target_generation_success_with_approved_profile(
    tmp_path: Path,
) -> None:
    artifact_id, artifact_dir, version_id = _make_artifact_with_approval(
        tmp_path, artifact_id="artifact_approved_target"
    )
    target_format = _upload_target(tmp_path, artifact_id)
    create_and_approve_proofs(client, artifact_id, artifact_dir)

    response = _generate(artifact_id, version_id, target_format=target_format)
    assert response.status_code == 200, response.text
    assert response.json()["artifact_id"] == artifact_id


def test_designer_template_generation_success_with_approved_profile(
    tmp_path: Path,
) -> None:
    artifact_id, artifact_dir, version_id = _make_artifact_with_approval(
        tmp_path, artifact_id="artifact_approved_design"
    )
    _upload_target(tmp_path, artifact_id)
    create = client.post(
        "/api/designs/request",
        json={
            "artifact_id": artifact_id,
            "designer": "deterministic",
            "approved_profile_version_id": version_id,
        },
    )
    assert create.status_code == 200, create.text
    design_id = create.json()["design_id"]
    approve = client.post(
        f"/api/artifacts/{artifact_id}/designs/{design_id}/approve", json={}
    )
    assert approve.status_code == 200, approve.text
    create_and_approve_proofs(
        client, artifact_id, artifact_dir, design_id=design_id
    )

    response = _generate(artifact_id, version_id, design_id=design_id)
    assert response.status_code == 200, response.text
    assert response.json()["design_id"] == design_id


# -- negative: missing approval -> 409 on every path --------------------------


@pytest.mark.parametrize(
    ("path_name", "setup"),
    [
        ("builtin", "none"),
        ("uploaded_target", "target"),
        ("designer", "design"),
    ],
)
def test_generate_without_approval_is_rejected_on_every_path(
    tmp_path: Path, path_name: str, setup: str
) -> None:
    artifact_id, artifact_dir, version_id = _make_artifact_with_approval(
        tmp_path, artifact_id=f"artifact_no_approval_{path_name}"
    )
    target_format = None
    design_id = None
    if setup == "target":
        target_format = _upload_target(tmp_path, artifact_id)
        create_and_approve_proofs(client, artifact_id, artifact_dir)
    elif setup == "design":
        _upload_target(tmp_path, artifact_id)
        create = client.post(
            "/api/designs/request",
            json={
                "artifact_id": artifact_id,
                "designer": "deterministic",
                "approved_profile_version_id": version_id,
            },
        ).json()
        design_id = create["design_id"]
        client.post(
            f"/api/artifacts/{artifact_id}/designs/{design_id}/approve", json={}
        )
        create_and_approve_proofs(
            client, artifact_id, artifact_dir, design_id=design_id
        )

    # No approved_profile_version_id -> structured 409, never a 200.
    response = _generate(
        artifact_id, None, target_format=target_format, design_id=design_id
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "approved_profile_missing"


def test_generate_rejects_invalid_approval_token(tmp_path: Path) -> None:
    artifact_id, _artifact_dir, _version_id = _make_artifact_with_approval(tmp_path)

    response = _generate(artifact_id, "profile_v_does_not_exist")
    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "approved_profile_invalid"


def test_generate_rejects_superseded_approval(tmp_path: Path) -> None:
    artifact_id, _artifact_dir, version_one = _make_artifact_with_approval(tmp_path)
    version_two = _approve_profile(
        artifact_id, _profile(full_name="Corrected Jane")
    )
    assert version_two != version_one

    # Referencing the older, now-superseded version must fail with 409.
    response = _generate(artifact_id, version_one)
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "approved_profile_superseded"

    # The current version still generates.
    current = _generate(artifact_id, version_two)
    assert current.status_code == 200, current.text


def test_generate_rejects_approval_for_other_artifact(tmp_path: Path) -> None:
    """An approval created under artifact A must never authorize generation for
    artifact B, even when its version file is present under B."""
    artifact_id_a, _artifact_dir_a, version_a = _make_artifact_with_approval(
        tmp_path, artifact_id="artifact_owner_a"
    )
    artifact_id_b, artifact_dir_b, _version_b = _make_artifact_with_approval(
        tmp_path, artifact_id="artifact_owner_b"
    )
    assert artifact_id_a != artifact_id_b
    # Copy A's immutable version file + index into B's profiles directory so the
    # loader can resolve it; the artifact-scope check must still reject it.
    profiles_b = artifact_dir_b / "profiles"
    profiles_b.mkdir(parents=True, exist_ok=True)
    version_file = (
        tmp_path
        / "outputs"
        / artifact_id_a
        / "profiles"
        / f"approved_profile_{version_a}.json"
    )
    (profiles_b / version_file.name).write_bytes(version_file.read_bytes())
    index_b = (
        tmp_path / "outputs" / artifact_id_a / "profiles" / "index.json"
    ).read_bytes()
    (profiles_b / "index.json").write_bytes(index_b)

    response = _generate(artifact_id_b, version_a)
    assert response.status_code == 400
    assert (
        response.json()["detail"]["error_code"] == "approved_profile_artifact_mismatch"
    )


def test_generate_rejects_approval_after_draft_changed(tmp_path: Path) -> None:
    """The approval records the source draft checksum; when the artifact's
    extraction draft changes after approval, the approval no longer covers the
    current source and generation must fail with 400."""
    artifact_id, artifact_dir, version_id = _make_artifact_with_approval(tmp_path)
    assert (artifact_dir / "candidate_profile.json").exists()

    # Re-processed artifact: the draft changes after the approval was recorded.
    changed_draft = _profile(full_name="Reprocessed Jane").model_dump(mode="json")
    changed_draft["skills"] = ["Python", "SQL", "Go"]
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(changed_draft), encoding="utf-8"
    )

    response = _generate(artifact_id, version_id)
    assert response.status_code == 400
    assert (
        response.json()["detail"]["error_code"] == "approved_profile_draft_mismatch"
    )


def test_generate_rejects_approval_for_other_target_checksum(tmp_path: Path) -> None:
    """Uploaded-target path: the profile approval is mandatory even when the
    layout-proof gate is satisfied; and when the target file changes, the
    layout-proof gate rejects the mismatch (never silent fallback)."""
    artifact_id, artifact_dir, version_id = _make_artifact_with_approval(
        tmp_path, artifact_id="artifact_target_checksum"
    )
    target_format = _upload_target(tmp_path, artifact_id)
    create_and_approve_proofs(client, artifact_id, artifact_dir)
    # Replace the stored target file; the layout-proof approval no longer
    # covers this target checksum.
    (artifact_dir / "target_format_reference.pdf").write_bytes(
        b"%PDF-1.4 different target bytes\n"
    )

    response = _generate(artifact_id, version_id, target_format=target_format)
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "layout_proof_mismatch"


def test_pending_review_blocks_approval_and_generation_until_recruiter_reviews(
    tmp_path: Path,
) -> None:
    """Unknown/other sections enter pending review; approval returns 409 and
    generation stays blocked until the recruiter reviews the section."""
    from app.extraction.llm_segmentation import (
        LLMSegmentationOutput,
        materialize_llm_segmentation,
    )

    artifact_id = "artifact_pending_review"
    artifact_dir = tmp_path / "outputs" / artifact_id
    artifact_dir.mkdir(parents=True)
    source = "Jane Candidate\nSUMMARY\nEngineer.\nHACKATHONS\nBuilt a thing.\n"
    output = LLMSegmentationOutput.model_validate(
        {
            "schema_version": "llm_segmentation/1",
            "sections": [
                {
                    "heading": None,
                    "section_type": "contact",
                    "items": ["Jane Candidate"],
                },
                {
                    "heading": "SUMMARY",
                    "section_type": "summary",
                    "items": ["Engineer."],
                    "professional_summary": "Engineer.",
                },
                {
                    "heading": "HACKATHONS",
                    "section_type": "other",
                    "items": ["Built a thing."],
                    "review_state": "pending_review",
                },
            ],
        }
    )
    _, document = materialize_llm_segmentation(source, output)
    hackathons_block = next(
        block
        for block in document.blocks
        if block.normalized_heading == "hackathons"
    )
    pending_profile = _profile().model_copy(
        deep=True,
        update={
            "additional_sections": [
                {
                    "section_type": "other",
                    "source_heading": "HACKATHONS",
                    "source_block_id": hackathons_block.block_id,
                    "review_state": "pending_review",
                    "entries": [{"title": "Built a thing."}],
                }
            ]
        },
    )
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(pending_profile.model_dump(mode="json")), encoding="utf-8"
    )
    (artifact_dir / "normalized_candidate_document.json").write_text(
        document.model_dump_json(), encoding="utf-8"
    )

    response = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={"profile": pending_profile.model_dump(mode="json")},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error_code"] == "source_content_unaccounted"
    assert detail["pending_review_sections"][0]["source_heading"] == "HACKATHONS"

    # No approved profile yet: generation must be blocked.
    generation = _generate(artifact_id, "")
    assert generation.status_code == 409

    # Recruiter reviews the classification: approval succeeds.
    from app.extraction.candidate_schema import AdditionalSection

    reviewed_profile = pending_profile.model_copy(
        deep=True,
        update={
            "additional_sections": [
                AdditionalSection.model_validate(section).model_copy(
                    update={"review_state": "reviewed"}
                )
                for section in pending_profile.additional_sections
            ]
        },
    )
    approval = client.post(
        f"/api/artifacts/{artifact_id}/profiles/approve",
        json={"profile": reviewed_profile.model_dump(mode="json")},
    )
    assert approval.status_code == 200, approval.text
    version_id = approval.json()["profile_version_id"]

    generation = _generate(artifact_id, version_id)
    assert generation.status_code == 200, generation.text


def test_generation_persists_structure_validation_report(tmp_path: Path) -> None:
    """Production generation runs the structure gate and persists its report."""
    from app.generation.content_validation import StructureGateReport

    profile = _profile().model_copy(
        deep=True,
        update={
            "additional_sections": [
                {
                    "section_type": "projects",
                    "source_heading": "PROJECTS",
                    "source_block_id": "candidate-block-004",
                    "entries": [
                        {
                            "title": "Resume Builder",
                            "links": ["https://example.test/demo"],
                            "description": ["Built a generic content-preservation gate."],
                        }
                    ],
                }
            ]
        },
    )
    artifact_id, artifact_dir, version_id = _make_artifact_with_approval(
        tmp_path, profile=profile
    )

    response = _generate(artifact_id, version_id)
    assert response.status_code == 200, response.text

    report_path = artifact_dir / "structure_validation.json"
    assert report_path.exists()
    report = StructureGateReport.model_validate_json(report_path.read_text())
    assert report.passed is True
    assert any(check.section_heading == "PROJECTS" and check.matched for check in report.checks)
    projects_check = next(
        check for check in report.checks if check.section_heading == "PROJECTS"
    )
    # Unavailable tier measurements are recorded, not silently passed.
    assert any(
        "entry-title tier measurement unavailable" in note
        for note in projects_check.notes
    )
    assert any(
        "link tier measurement unavailable" in note for note in projects_check.notes
    )
    debug = response.json()["debug_artifacts"]
    assert str(report_path) == debug["structure_validation"]
    # Coverage safety is checked too: the approved profile is fully accounted.
    assert any(
        check.section_heading == "source-coverage" and check.matched
        for check in report.checks
    )


def test_generation_fails_closed_on_structure_gate_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A structural mismatch persists the report and fails generation closed."""
    from app.generation.content_validation import (
        StructureGateCheck,
        StructureGateReport,
    )

    artifact_id, artifact_dir, version_id = _make_artifact_with_approval(tmp_path)

    def _failing_gate(*args: object, **kwargs: object) -> StructureGateReport:
        return StructureGateReport(
            passed=False,
            checks=[
                StructureGateCheck(
                    section_heading="PROJECTS",
                    matched=True,
                    issues=["entry 1: expected 3 description bullets, found 2"],
                )
            ],
        )

    monkeypatch.setattr(main, "validate_output_structure", _failing_gate)
    response = _generate(artifact_id, version_id)

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["error_code"] == "generated_structure_incomplete"
    assert detail["checks"][0]["issues"] == ["entry 1: expected 3 description bullets, found 2"]

    report_path = artifact_dir / "structure_validation.json"
    assert report_path.exists()
    persisted = StructureGateReport.model_validate_json(report_path.read_text())
    assert persisted.passed is False


def test_generation_fails_closed_on_broken_render_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An intentionally defaced render (title text and metadata semantics)
    passes the content gate but fails the production structure gate
    closed, persisting the evidence."""
    from app.generation.content_validation import (
        ContentValidationReport,
        StructureGateReport,
    )

    profile = _profile().model_copy(
        deep=True,
        update={
            "additional_sections": [
                {
                    "section_type": "projects",
                    "source_heading": "PROJECTS",
                    "source_block_id": "candidate-block-004",
                    "entries": [
                        {
                            "title": "Resume Builder",
                            "links": ["https://example.test/demo"],
                            "description": ["Built a generic content-preservation gate."],
                        }
                    ],
                }
            ]
        },
    )
    artifact_id, artifact_dir, version_id = _make_artifact_with_approval(
        tmp_path, profile=profile
    )

    real_render_html = main.render_html

    def _render_with_broken_entry_semantics(render_context, style_spec, html_path):
        real_render_html(render_context, style_spec, html_path)
        path = Path(html_path)
        html = path.read_text(encoding="utf-8")
        html = html.replace(">Resume Builder</p>", ">• Resume Builder</p>", 1)
        html = html.replace('class="entry-metadata"', 'class="broken-metadata"', 1)
        path.write_text(html, encoding="utf-8")
        return path

    monkeypatch.setattr(main, "render_html", _render_with_broken_entry_semantics)
    response = _generate(artifact_id, version_id)

    assert response.status_code == 500
    assert (
        response.json()["detail"]["error_code"] == "generated_structure_incomplete"
    ), response.text
    content = ContentValidationReport.model_validate_json(
        (artifact_dir / "content_validation.json").read_text()
    )
    assert content.passed is True, content.checks  # text survived; structure did not
    report = StructureGateReport.model_validate_json(
        (artifact_dir / "structure_validation.json").read_text()
    )
    assert report.passed is False
    issues = " ".join(issue for check in report.checks for issue in check.issues)
    assert "expected title 'Resume Builder', found '• Resume Builder'" in issues
    assert "expected 1 metadata links, found 0" in issues
