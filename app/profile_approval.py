"""Approved profile version boundary.

The extraction pipeline writes a *draft* ``CandidateProfile``
(``candidate_profile.json``). Design requests and generation must reference an
immutable, recruiter-approved profile version stored under the existing
filesystem artifact boundary — never the draft. Approving a (possibly
corrected) profile creates a new immutable version; the original draft is
preserved untouched, and version files are never mutated after creation
(superseding is tracked in a separate index).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
    _version_path(artifact_dir, version.profile_version_id).write_text(
        json.dumps(version.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
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


def load_approved_profile_version(
    artifact_dir: str | Path, version_id: str
) -> ApprovedProfileVersion | None:
    """Load one approved profile version; None when missing or invalid."""
    path = _version_path(artifact_dir, version_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ApprovedProfileVersion.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ProfileApprovalError(f"Approved profile version is invalid: {exc}") from exc


def load_current_approved_profile(
    artifact_dir: str | Path,
) -> ApprovedProfileVersion | None:
    """Load the current (non-superseded) approved profile version, if any."""
    index = _load_index(artifact_dir)
    current = index.get("current")
    if not current:
        return None
    return load_approved_profile_version(artifact_dir, current)


def is_current_approved_profile(artifact_dir: str | Path, version_id: str) -> bool:
    return load_current_approved_profile(artifact_dir) is not None and (
        load_current_approved_profile(artifact_dir).profile_version_id == version_id
    )


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
