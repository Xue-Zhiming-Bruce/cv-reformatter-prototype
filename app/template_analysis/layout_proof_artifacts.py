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
from typing import Literal

from app.template_analysis.layout_proof_schemas import (
    LengthVariant,
    LayoutProofRenderResult,
    VariantProofEvidence,
    VisualComparisonEvidence,
)
from app.template_analysis.schemas import LayoutTemplateSpec, VisualComparisonReport
from app.template_analysis.visual_comparator import COMPARISON_FILENAME

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


# -- reviewable proof resources -----------------------------------------------


class LayoutProofResourceError(RuntimeError):
    """A proof resource request that must not be served.

    ``error_code`` is the structured API error the route returns. Messages name
    the variant and what is wrong, never a filesystem path, the proof directory
    layout, or a stack trace.
    """

    def __init__(
        self,
        error_code: Literal[
            "layout_proof_resource_not_found", "layout_proof_resource_mismatch"
        ],
        message: str,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code


def _recorded_proof_result(
    artifact_dir: str | Path, artifact_id: str, variant: LengthVariant
) -> LayoutProofRenderResult:
    """The variant's recorded render result, strictly validated for this artifact.

    ``layout_proof_validation`` imports this module, so the loader is imported
    here rather than at module scope.
    """
    from app.template_analysis.layout_proof_validation import (
        LayoutProofIncompleteError,
        LayoutProofValidationError,
        load_layout_proof_render_result,
    )

    try:
        return load_layout_proof_render_result(
            artifact_dir, variant, artifact_id=artifact_id
        )
    except LayoutProofIncompleteError as exc:
        raise LayoutProofResourceError(
            "layout_proof_resource_not_found",
            f"no {variant.value} proof is recorded for this artifact",
        ) from exc
    except LayoutProofValidationError as exc:
        raise LayoutProofResourceError(
            "layout_proof_resource_mismatch",
            f"the recorded {variant.value} proof cannot be read",
        ) from exc


def resolve_proof_resource(
    *,
    artifact_dir: str | Path,
    artifact_id: str,
    variant: LengthVariant,
    kind: Literal["pdf", "html"],
) -> Path:
    """The variant's recorded proof ``pdf`` or ``html`` file, checksum-verified.

    The filename comes from the variant's own ``render_result.json`` (never from
    a request), the real file must stay inside that variant's proof directory,
    and its bytes must hash to the recorded checksum.
    """
    result = _recorded_proof_result(artifact_dir, artifact_id, variant)
    if kind == "pdf":
        filename, expected_checksum = result.pdf_filename, result.pdf_checksum
    else:
        filename, expected_checksum = result.html_filename, result.html_checksum
    proof_dir = layout_proofs_dir_for(artifact_dir) / result.proof_id
    label = f"proof {kind.upper()}"
    path = _contained_file(proof_dir / filename, proof_dir, variant, label)
    if _file_sha256_or_none(path, variant, label) != expected_checksum:
        raise LayoutProofResourceError(
            "layout_proof_resource_mismatch",
            f"the {variant.value} proof {kind.upper()} changed after it was recorded",
        )
    return path


def _file_sha256_or_none(path: Path, variant: LengthVariant, label: str) -> str | None:
    """The file's digest, or ``None`` when its bytes cannot be read at all."""
    try:
        return file_sha256(path)
    except OSError as exc:
        raise LayoutProofResourceError(
            "layout_proof_resource_mismatch",
            f"the {variant.value} {label} cannot be read",
        ) from exc


def resolve_comparison_resource(
    *,
    artifact_dir: str | Path,
    artifact_id: str,
    variant: LengthVariant,
    filename: str,
) -> Path:
    """One comparison image or the visual report the variant's evidence names.

    ``filename`` must be the report filename or a page image named by the
    variant's own ``visual.json``, and the real file must stay inside that
    variant's ``visual/`` directory.
    """
    _recorded_proof_result(artifact_dir, artifact_id, variant)
    report = _recorded_visual_report(artifact_dir, variant)
    named = {
        COMPARISON_FILENAME,
        *report.target_page_filenames,
        *report.generated_page_filenames,
        *(page.difference_filename for page in report.pages),
    }
    if filename not in named:
        raise LayoutProofResourceError(
            "layout_proof_resource_mismatch",
            f"the {variant.value} comparison evidence does not name this file",
        )
    visual_dir = variant_dir_for(artifact_dir, variant) / "visual"
    return _contained_file(
        visual_dir / filename, visual_dir, variant, "comparison resource"
    )


def _recorded_visual_report(
    artifact_dir: str | Path, variant: LengthVariant
) -> VisualComparisonReport:
    path = variant_dir_for(artifact_dir, variant) / "visual.json"
    try:
        evidence = VisualComparisonEvidence.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise LayoutProofResourceError(
            "layout_proof_resource_mismatch",
            f"the stored {variant.value} comparison evidence cannot be read",
        ) from exc
    return evidence.report


def _contained_file(
    path: Path, directory: Path, variant: LengthVariant, label: str
) -> Path:
    """Resolve ``path`` only when its real file stays inside ``directory``."""
    try:
        resolved_path = path.resolve()
        resolved_directory = directory.resolve()
    except OSError as exc:
        raise LayoutProofResourceError(
            "layout_proof_resource_mismatch",
            f"the {variant.value} {label} cannot be resolved",
        ) from exc
    if not resolved_path.is_relative_to(resolved_directory):
        raise LayoutProofResourceError(
            "layout_proof_resource_mismatch",
            f"the {variant.value} {label} does not resolve inside its own directory",
        )
    if not resolved_path.is_file():
        raise LayoutProofResourceError(
            "layout_proof_resource_not_found",
            f"the {variant.value} {label} is not available",
        )
    return resolved_path


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
