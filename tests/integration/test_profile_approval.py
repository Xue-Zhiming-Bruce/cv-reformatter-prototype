"""Approved profile version boundary: immutable model, checksums, filesystem
artifact storage, and strictness (no profile fields can be invented)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.extraction.candidate_schema import CandidateProfile
from app.profile_approval import (
    APPROVED_PROFILE_SCHEMA_VERSION,
    ApprovedProfileVersion,
    ProfileApprovalError,
    draft_sha256,
    load_approved_profile_version,
    load_current_approved_profile,
    profile_sha256,
    save_approved_profile,
)


def _profile(**updates: object) -> CandidateProfile:
    base = CandidateProfile(
        full_name="Jane Candidate",
        email="jane@example.com",
        professional_summary="Backend engineer.",
        skills=["Python", "SQL"],
        work_experience=[{"company": "DataWorks", "title": "Engineer"}],
        education=[{"institution": "Example University"}],
    )
    return base.model_copy(update=updates)


def _save(artifact_dir: Path, profile: CandidateProfile, **kwargs: object):
    return save_approved_profile(
        artifact_dir,
        artifact_id="artifact_test_1",
        profile=profile,
        source_draft_sha256=draft_sha256(artifact_dir),
        **kwargs,
    )


def test_model_is_strict_and_versioned() -> None:
    profile = _profile()
    version = ApprovedProfileVersion(
        profile_version_id="profile_v1",
        artifact_id="a1",
        profile=profile,
        profile_sha256=profile_sha256(profile),
        source_draft_sha256="draftsha",
        approved_at="2026-01-01T00:00:00Z",
    )
    assert version.schema_version == APPROVED_PROFILE_SCHEMA_VERSION
    with pytest.raises(ValidationError):
        ApprovedProfileVersion.model_validate(
            {**version.model_dump(), "invented_field": "nope"}
        )


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact_1"
    artifact_dir.mkdir()
    draft = _profile()
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(draft.model_dump(mode="json")), encoding="utf-8"
    )
    saved = _save(artifact_dir, draft, reviewer_note="recruiter reviewed")
    loaded = load_current_approved_profile(artifact_dir)
    assert loaded is not None
    assert loaded.profile_version_id == saved.profile_version_id
    assert loaded.profile == draft
    assert loaded.profile_sha256 == profile_sha256(draft)
    assert loaded.source_draft_sha256 == draft_sha256(artifact_dir)
    assert loaded.reviewer_note == "recruiter reviewed"
    assert loaded.artifact_id == "artifact_test_1"
    # The draft file is preserved untouched.
    assert (artifact_dir / "candidate_profile.json").exists()


def test_newer_approval_supersedes_without_mutating_older_version(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifact_1"
    artifact_dir.mkdir()
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(_profile().model_dump(mode="json")), encoding="utf-8"
    )
    v1 = _save(artifact_dir, _profile())
    version_one_path = (
        artifact_dir / "profiles" / f"approved_profile_{v1.profile_version_id}.json"
    )
    version_one_bytes = version_one_path.read_bytes()

    corrected = _profile(skills=["Python", "SQL", "Go"])
    v2 = _save(artifact_dir, corrected)
    assert v2.profile_version_id != v1.profile_version_id

    # Older version file is byte-identical (immutable); only the index moved.
    assert version_one_path.read_bytes() == version_one_bytes
    current = load_current_approved_profile(artifact_dir)
    assert current.profile_version_id == v2.profile_version_id
    assert current.profile.skills == ["Python", "SQL", "Go"]
    older = load_approved_profile_version(artifact_dir, v1.profile_version_id)
    assert older is not None
    assert older.profile.skills == ["Python", "SQL"]


def test_no_approved_profile_yet(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact_1"
    artifact_dir.mkdir()
    assert load_current_approved_profile(artifact_dir) is None


def test_draft_sha256_requires_draft_file(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact_1"
    artifact_dir.mkdir()
    with pytest.raises(ProfileApprovalError):
        draft_sha256(artifact_dir)


def test_profile_sha256_is_stable_and_sensitive_to_changes() -> None:
    first = profile_sha256(_profile())
    assert first == profile_sha256(_profile())
    assert first != profile_sha256(_profile(skills=["Python"]))
    assert len(first) == 64


def test_approval_never_invents_fields(tmp_path: Path) -> None:
    """Approval stores exactly the submitted profile; no field is fabricated
    (no salary, no inferred employment dates, no invented values)."""
    artifact_dir = tmp_path / "artifact_1"
    artifact_dir.mkdir()
    (artifact_dir / "candidate_profile.json").write_text(
        json.dumps(_profile().model_dump(mode="json")), encoding="utf-8"
    )
    sparse = CandidateProfile(
        full_name="Only Name",
    )
    saved = _save(artifact_dir, sparse)
    assert saved.profile.model_dump(mode="json") == sparse.model_dump(mode="json")
    assert saved.profile.salary_expectation is None
    assert saved.profile.work_experience == []
