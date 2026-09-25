"""Human acceptance of one exact generated HTML/PDF identity."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


FINAL_ACCEPTANCE_FILENAME = "final_acceptance.json"


class FinalOutputIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    profile_revision_id: str
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    layout_template_version: str
    layout_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    html_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FinalOutputAcceptance(FinalOutputIdentity):
    schema_version: Literal["final-output-acceptance/1"] = "final-output-acceptance/1"
    accepted_at: str
    reviewer_note: str | None = None


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def current_output_identity(artifact_dir: str | Path) -> FinalOutputIdentity:
    directory = Path(artifact_dir)
    metadata = json.loads(
        (directory / "generation_metadata.json").read_text(encoding="utf-8")
    )
    return FinalOutputIdentity(
        profile_revision_id=metadata["profile_revision_id"],
        profile_sha256=metadata["profile_sha256"],
        layout_template_version=metadata["layout_template_version"],
        layout_spec_sha256=metadata["layout_spec_sha256"],
        html_sha256=file_sha256(directory / "candidate_profile.html"),
        pdf_sha256=file_sha256(directory / "candidate_profile.pdf"),
    )


def save_final_acceptance(
    artifact_dir: str | Path,
    identity: FinalOutputIdentity,
    reviewer_note: str | None = None,
) -> FinalOutputAcceptance:
    acceptance = FinalOutputAcceptance(
        **identity.model_dump(),
        accepted_at=datetime.now(UTC).isoformat(),
        reviewer_note=reviewer_note,
    )
    (Path(artifact_dir) / FINAL_ACCEPTANCE_FILENAME).write_text(
        acceptance.model_dump_json(indent=2), encoding="utf-8"
    )
    return acceptance


def load_final_acceptance(
    artifact_dir: str | Path,
) -> FinalOutputAcceptance | None:
    path = Path(artifact_dir) / FINAL_ACCEPTANCE_FILENAME
    if not path.exists():
        return None
    return FinalOutputAcceptance.model_validate_json(path.read_text(encoding="utf-8"))


def is_final_acceptance_current(artifact_dir: str | Path) -> bool:
    acceptance = load_final_acceptance(artifact_dir)
    if acceptance is None:
        return False
    try:
        current = current_output_identity(artifact_dir)
    except (OSError, KeyError, json.JSONDecodeError, ValueError):
        return False
    return acceptance.model_dump(exclude={"schema_version", "accepted_at", "reviewer_note"}) == current.model_dump()
