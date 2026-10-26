"""Immutable candidate revisions with PostgreSQL or legacy filesystem storage.

Legacy approved-profile names and endpoints remain compatible. Saving a revision
permits a preview, but only exact final-output acceptance makes it client-ready.
Source drafts and audit copies stay immutable; the store owns the current pointer.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.extraction.candidate_schema import CandidateProfile

APPROVED_PROFILE_SCHEMA_VERSION = "approved-profile/1"

DRAFT_FILENAME = "candidate_profile.json"
PROFILES_DIRNAME = "profiles"


class ProfileApprovalError(RuntimeError):
    """Raised for invalid approval operations."""


class ApprovedProfileVersion(BaseModel):
    """Immutable, recruiter-approved profile version. Strict model: no extra
    fields may be invented during approval."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["approved-profile/1"] = APPROVED_PROFILE_SCHEMA_VERSION
    profile_version_id: str
    artifact_id: str
    profile: CandidateProfile
    profile_sha256: str
    source_draft_sha256: str
    approved_at: str
    reviewer_note: str | None = None

    @property
    def profile_revision_id(self) -> str:
        """Revision name for the legacy on-disk ``profile_version_id`` field."""
        return self.profile_version_id

    @property
    def created_at(self) -> str:
        """Creation time stored in the legacy ``approved_at`` field."""
        return self.approved_at


class ProfileRevisionRepository(Protocol):
    def save(self, artifact_dir: Path, version: ApprovedProfileVersion) -> None: ...

    def load(self, artifact_id: str, revision_id: str) -> ApprovedProfileVersion | None: ...

    def current(self, artifact_id: str) -> ApprovedProfileVersion | None: ...


def profile_sha256(profile: CandidateProfile) -> str:
    """Stable checksum of a CandidateProfile (no secrets: hashed only)."""
    payload = json.dumps(
        profile.model_dump(mode="json"), sort_keys=True, ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def draft_sha256(artifact_dir: str | Path) -> str:
    """Checksum of the extraction draft file on disk."""
    draft_path = Path(artifact_dir) / DRAFT_FILENAME
    if not draft_path.exists():
        raise ProfileApprovalError(
            f"No extraction draft ({DRAFT_FILENAME}) exists for this artifact."
        )
    return hashlib.sha256(draft_path.read_bytes()).hexdigest()


def new_profile_version_id() -> str:
    return f"profile_v{uuid.uuid4().hex[:12]}"


def _profiles_dir(artifact_dir: str | Path) -> Path:
    return Path(artifact_dir) / PROFILES_DIRNAME


def _version_path(artifact_dir: str | Path, version_id: str) -> Path:
    return _profiles_dir(artifact_dir) / f"approved_profile_{version_id}.json"


def _index_path(artifact_dir: str | Path) -> Path:
    return _profiles_dir(artifact_dir) / "index.json"


def save_approved_profile(
    artifact_dir: str | Path,
    *,
    artifact_id: str,
    profile: CandidateProfile,
    source_draft_sha256: str,
    reviewer_note: str | None = None,
    repository: ProfileRevisionRepository | None = None,
) -> ApprovedProfileVersion:
    """Create a new immutable approved profile version. The previously current
    version is marked superseded in the index only; version files themselves
    are never rewritten."""
    directory = _profiles_dir(artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    version = ApprovedProfileVersion(
        profile_version_id=new_profile_version_id(),
        artifact_id=artifact_id,
        profile=profile,
        profile_sha256=profile_sha256(profile),
        source_draft_sha256=source_draft_sha256,
        approved_at=_now_iso(),
        reviewer_note=reviewer_note,
    )
    if repository is not None:
        repository.save(Path(artifact_dir), version)
    _write_version_snapshot(artifact_dir, version, canonical=repository is not None)
    if repository is not None:
        return version  # PostgreSQL owns the current pointer; no filesystem index fallback.
    index = _load_index(artifact_dir)
    previous = index.get("current")
    versions = index.setdefault("versions", {})
    if previous:
        versions[previous] = {
            "superseded": True,
            "superseded_at": _now_iso(),
        }
    versions[version.profile_version_id] = {
        "superseded": False,
        "superseded_at": None,
    }
    index["current"] = version.profile_version_id
    _write_index(artifact_dir, index)
    return version


def save_profile_revision(
    artifact_dir: str | Path,
    *,
    artifact_id: str,
    profile: CandidateProfile,
    source_draft_sha256: str,
    repository: ProfileRevisionRepository | None = None,
) -> ApprovedProfileVersion:
    """Save an immutable revision in the existing profile-version store.

    The legacy schema/file names are retained only for migration compatibility;
    no human approval is implied.
    """
    return save_approved_profile(
        artifact_dir,
        artifact_id=artifact_id,
        profile=profile,
        source_draft_sha256=source_draft_sha256,
        repository=repository,
    )


def load_approved_profile_version(
    artifact_dir: str | Path, version_id: str, *,
    repository: ProfileRevisionRepository | None = None,
) -> ApprovedProfileVersion | None:
    """Load one approved profile version; None when missing or invalid."""
    if repository is not None:
        version = repository.load(Path(artifact_dir).name, version_id)
        if version is not None:
            _write_version_snapshot(artifact_dir, version, canonical=True)
        return version
    path = _version_path(artifact_dir, version_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ApprovedProfileVersion.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ProfileApprovalError(f"Approved profile version is invalid: {exc}") from exc


def load_profile_revision(
    artifact_dir: str | Path, revision_id: str, *,
    repository: ProfileRevisionRepository | None = None,
) -> ApprovedProfileVersion | None:
    """Transitional reader over the existing immutable version files."""
    return load_approved_profile_version(artifact_dir, revision_id, repository=repository)


def load_current_approved_profile(
    artifact_dir: str | Path,
    *, repository: ProfileRevisionRepository | None = None,
) -> ApprovedProfileVersion | None:
    """Load the current (non-superseded) approved profile version, if any."""
    if repository is not None:
        version = repository.current(Path(artifact_dir).name)
        if version is not None:
            _write_version_snapshot(artifact_dir, version, canonical=True)
        return version
    index = _load_index(artifact_dir)
    current = index.get("current")
    if not current:
        return None
    return load_approved_profile_version(artifact_dir, current)


def is_current_approved_profile(
    artifact_dir: str | Path, version_id: str, *, repository: ProfileRevisionRepository | None = None,
) -> bool:
    current = load_current_approved_profile(artifact_dir, repository=repository)
    return current is not None and current.profile_version_id == version_id


def _write_version_snapshot(
    artifact_dir: str | Path, version: ApprovedProfileVersion, *, canonical: bool = False,
) -> None:
    """Publish an immutable audit copy; missing PostgreSQL copies can be rebuilt."""
    path = _version_path(artifact_dir, version.profile_version_id)
    # JSONB reorders object keys; database-backed audit copies need stable bytes.
    content = json.dumps(version.model_dump(mode="json"), indent=2, ensure_ascii=False, sort_keys=canonical)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent) as snapshot:
            snapshot.write(content)
            snapshot.flush()
            try:
                os.link(snapshot.name, path)  # Publish complete bytes without overwriting an existing revision.
            except FileExistsError:
                pass
    if path.read_text(encoding="utf-8") != content:
        raise ProfileApprovalError("Immutable profile revision artifact disagrees with the saved revision.")


def _load_index(artifact_dir: str | Path) -> dict[str, object]:
    path = _index_path(artifact_dir)
    if not path.exists():
        return {"current": None, "versions": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ProfileApprovalError("Approved profile index is invalid.") from None
    return {
        "current": payload.get("current"),
        "versions": payload.get("versions") or {},
    }


def _write_index(artifact_dir: str | Path, index: dict[str, object]) -> None:
    _index_path(artifact_dir).write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
