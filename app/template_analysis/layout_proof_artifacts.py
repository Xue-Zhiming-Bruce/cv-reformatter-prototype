"""Layout-proof artifact identity, paths, and atomic JSON writes.

Checksums of a stored ``LayoutTemplateSpec`` and of proof files, the
artifact-scoped proof directory layout, and the atomic JSON writer used for
stored proof records. Only operations that several proof owners and their
generation consumer share; approval policy and deterministic evidence
validation stay with their own owners.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from app.template_analysis.layout_proof_schemas import (
    LengthVariant,
    VariantProofEvidence,
)
from app.template_analysis.schemas import LayoutTemplateSpec

#: Repository-approved debug artifact location for standalone proof renders.
DEFAULT_PROOF_OUTPUT_DIR = Path("data/generated_outputs") / "layout_proofs"

#: Repository-approved final candidate document location.
DEFAULT_CANDIDATE_OUTPUT_DIR = Path("data/generated_outputs") / "candidate_documents"


def layout_spec_checksum(spec: LayoutTemplateSpec) -> str:
    """Deterministic sha256 of a ``LayoutTemplateSpec``'s canonical JSON."""
    payload = json.dumps(
        spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    """Deterministic sha256 of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# -- artifact-scoped persistence ----------------------------------------------


def layout_proofs_dir_for(artifact_dir: str | Path) -> Path:
    return Path(artifact_dir) / "layout_proofs"


def variant_dir_for(artifact_dir: str | Path, variant: LengthVariant) -> Path:
    return layout_proofs_dir_for(artifact_dir) / "variants" / variant.value


def approval_path_for(artifact_dir: str | Path) -> Path:
    return layout_proofs_dir_for(artifact_dir) / "approval.json"


def save_variant_evidence(
    artifact_dir: str | Path,
    evidence: VariantProofEvidence,
) -> None:
    """Persist per-variant evidence (strict JSON, atomic writes)."""
    directory = variant_dir_for(artifact_dir, evidence.variant)
    write_json_atomic(
        directory / "render_result.json",
        evidence.render_result.model_dump(mode="json"),
    )
    write_json_atomic(
        directory / "structural.json",
        evidence.structural.model_dump(mode="json"),
    )
    write_json_atomic(
        directory / "visual.json",
        evidence.visual.model_dump(mode="json"),
    )


def write_json_atomic(path: Path, payload: object, *, canonical: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=canonical)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
