"""Generation application workflow: prepare, render, validate, record.

Owned by ``app.generation`` and consumed by both the FastAPI adapter in
``app.main`` and the native job child in ``app.job_worker``. The workflow never
imports ``app.main`` or FastAPI; it raises :class:`GenerationError`, which the
HTTP adapter maps to the same status codes and detail payloads as before.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf.errors import PyPdfError

from app.extraction.candidate_document_analyzer import (
    NormalizedCandidateDocument,
    build_source_coverage_ledger,
    unaccounted_source_blocks,
    unreviewed_source_blocks,
)
from app.extraction.candidate_schema import CandidateProfile
from app.extraction.coverage_audit import SegmentationCoverageAudit
from app.extraction.llm_extractor import LLMConfigurationError, build_llm_client
from app.final_acceptance import current_output_identity, is_final_acceptance_current
from app.generation.agentic import AgenticGenerationError, generation_strategy
from app.generation.chrome_html_to_pdf import HtmlToPdfExportError, export_html_to_pdf
from app.generation.composition_report import (
    COMPOSITION_REPORT_FILENAME,
    SelectedRenderPlan,
    build_composition_report,
    load_selected_render_plan,
    write_composition_report,
)
from app.generation.artifact_manifest import (
    MANIFEST_FILENAME,
    capture_generation_inputs,
    write_artifact_manifest,
)
from app.generation.content_validation import (
    validate_generated_content,
    validate_output_structure,
)
from app.generation.contracts import GenerateRequest, GenerateResponse, TargetFormatMetadata
from app.generation.followup_message_generator import generate_followup_message
from app.generation.render_plan import build_production_render_plan
from app.generation.template_mapper import build_client_render_context
from app.pipeline.candidate_content import candidate_document_from_context
from app.pipeline.html_renderer import (
    RENDERER_VERSION as PIPELINE_RENDERER_VERSION,
    render_layout_template,
)
from app.profile_approval import (
    ApprovedProfileVersion,
    ProfileApprovalError,
    draft_sha256,
    is_current_approved_profile,
    load_current_approved_profile,
    load_profile_revision,
    profile_sha256,
)
from app.storage.artifact_store import ArtifactStoreError
from app.storage.repository import (
    artifact_store,
    design_repository,
    final_acceptance_repository,
    generation_repository,
    layout_proof_repository,
    profile_revision_repository,
)
from app.template_analysis.artifacts import (
    LAYOUT_SPEC_FILENAME,
    TargetAnalysisArtifactError,
    load_layout_template_spec,
)
from app.template_analysis.design_evidence import (
    candidate_sections_from_profile,
    inventory_sha256,
    target_checksum_from_bytes,
)
from app.template_analysis.design_schemas import DesignArtifactState
from app.template_analysis.design_service import (
    DESIGN_STRATEGY_CLAUDE,
    DESIGN_STRATEGY_OPENAI,
    DesignServiceError,
    design_dir_for,
    get_design_strategy,
    load_design_state,
)
from app.template_analysis.layout_proof_artifacts import layout_spec_checksum
from app.template_analysis.layout_proof_rendering import (
    LayoutProofMismatchError,
    LayoutProofRenderError,
    active_renderer_version,
)
from app.template_analysis.layout_proof_service import (
    load_layout_proof_approval,
    render_candidate_document,
)
from app.template_analysis.layout_proof_validation import LayoutProofValidationError
from app.template_analysis.schemas import (
    LayoutTemplateSpec,
    TemplateStyleSpec,
    built_in_layout_template_spec,
)
from app.template_analysis.visual_comparator import (
    COMPARISON_FILENAME,
    VisualComparisonError,
    compare_pdf_layouts,
)
from app.validation.missing_fields import apply_missing_field_detection


class GenerationError(Exception):
    """Domain failure carrying the HTTP status and payload the adapter returns."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(detail if isinstance(detail, str) else json.dumps(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class GenerationSettings:
    """Explicit production configuration; the adapter resolves these values."""

    outputs_dir: Path
    database_url: str | None
    local_database_path: Path

    def artifact_store(self):
        return artifact_store(
            database_url=self.database_url, local_database_path=self.local_database_path
        )


def is_safe_artifact_id(artifact_id: str) -> bool:
    return bool(artifact_id) and all(
        character.isalnum() or character in {"_", "-"} for character in artifact_id
    )


def new_artifact_id() -> str:
    return f"artifact_{uuid.uuid4().hex}"


def prepare_artifact_dir(
    outputs_dir: Path, artifact_id: str, *, allow_existing: bool = False
) -> Path:
    if not is_safe_artifact_id(artifact_id):
        raise GenerationError(400, "Invalid artifact_id.")
    artifact_dir = outputs_dir / artifact_id
    if artifact_dir.exists() and not artifact_dir.is_dir():
        raise GenerationError(400, "Invalid artifact storage path.")
    if artifact_dir.exists() and not allow_existing:
        raise GenerationError(409, "Artifact already exists.")
    artifact_dir.mkdir(parents=True, exist_ok=allow_existing)
    return artifact_dir


def artifact_url(artifact_id: str, filename: str) -> str:
    return f"/api/artifacts/{artifact_id}/{filename}"


def artifact_metadata_url(artifact_id: str) -> str:
    return f"/api/artifacts/{artifact_id}/metadata"


def requested_profile_revision_id(
    revision_id: str | None, legacy_version_id: str | None
) -> str | None:
    revision_id = (revision_id or "").strip() or None
    legacy_version_id = (legacy_version_id or "").strip() or None
    if revision_id and legacy_version_id and revision_id != legacy_version_id:
        raise GenerationError(400, "Profile revision identifiers disagree.")
    return revision_id or legacy_version_id


def load_saved_target_format(artifact_dir: Path) -> TargetFormatMetadata | None:
    metadata_path = artifact_dir / "target_format.json"
    if not metadata_path.exists():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return TargetFormatMetadata.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise GenerationError(400, "Saved target format metadata is invalid.") from exc


def validate_target_format_reference(
    artifact_id: str,
    artifact_dir: Path,
    target_format: TargetFormatMetadata,
) -> None:
    expected_filename_by_role = {
        "docx_template": "target_format_template.docx",
        "pdf_reference": "target_format_reference.pdf",
    }
    expected_filename = expected_filename_by_role.get(target_format.role)
    if (
        target_format.artifact_id != artifact_id
        or expected_filename is None
        or target_format.stored_filename != expected_filename
        or target_format.download_url != artifact_url(artifact_id, expected_filename)
    ):
        raise GenerationError(400, "Invalid target format reference.")

    stored_path = artifact_dir / target_format.stored_filename
    if not stored_path.exists() or not stored_path.is_file():
        raise GenerationError(
            400, "The target format reference has not been uploaded for this artifact."
        )


def load_normalized_candidate_document(artifact_dir: Path) -> NormalizedCandidateDocument:
    normalized_path = artifact_dir / "normalized_candidate_document.json"
    if not normalized_path.exists():
        # Artifacts without a source-analysis run (e.g. target-only flows)
        # have no source blocks; an empty document keeps the coverage gate
        # vacuous instead of reconstructing from raw text.
        return NormalizedCandidateDocument(source_text_sha256="0" * 64, blocks=[])
    try:
        return NormalizedCandidateDocument.model_validate_json(
            normalized_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise GenerationError(
            400, "Saved normalized candidate document is invalid."
        ) from exc


def validate_profile_source_review(
    artifact_dir: Path, profile: CandidateProfile
) -> None:
    segmentation_audit_path = artifact_dir / "segmentation_audit.json"
    if segmentation_audit_path.exists():
        try:
            segmentation_audit = SegmentationCoverageAudit.model_validate_json(
                segmentation_audit_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise GenerationError(400, "Saved segmentation coverage audit is invalid.") from exc
        if segmentation_audit.approval_blocked:
            raise GenerationError(
                409,
                {
                    "error_code": "segmentation_coverage_failed",
                    "message": (
                        "LLM segmentation changed, omitted, duplicated, or invented "
                        "source lines; profile approval is blocked."
                    ),
                    "missing_lines": [
                        item.model_dump(mode="json")
                        for item in segmentation_audit.missing_lines
                    ],
                    "invented_or_altered_lines": [
                        item.model_dump(mode="json")
                        for item in segmentation_audit.invented_or_altered_lines
                    ],
                },
            )
    normalized_document = load_normalized_candidate_document(artifact_dir)
    missing_blocks = unaccounted_source_blocks(profile, normalized_document)
    unreviewed_blocks = unreviewed_source_blocks(profile, normalized_document)
    if missing_blocks or unreviewed_blocks:
        raise GenerationError(
            409,
            {
                "error_code": "source_content_unaccounted",
                "message": (
                    "Every optional or custom source section must have an explicit "
                    "show or hide decision before approval, and sections entered "
                    "pending recruiter review (unknown/other classifications) must "
                    "be reviewed."
                ),
                "source_blocks": [
                    {
                        "source_block_id": block.block_id,
                        "source_heading": block.source_heading,
                        "classification": block.classification,
                    }
                    for block in missing_blocks
                ],
                "pending_review_sections": [
                    {
                        "source_block_id": block.block_id,
                        "source_heading": block.source_heading,
                        "classification": block.classification,
                    }
                    for block in unreviewed_blocks
                ],
            },
        )


def require_profile_revision(
    settings: GenerationSettings,
    *,
    artifact_dir: Path,
    artifact_id: str,
    profile_revision_id: str | None,
) -> ApprovedProfileVersion:
    """Require a current saved revision. Legacy error codes remain compatible;
    a saved revision is not human acceptance of the generated output.
    """
    if not (profile_revision_id or "").strip():
        raise GenerationError(
            409,
            {
                "error_code": "approved_profile_missing",
                "message": (
                    "profile_revision_id is required. Save a profile revision "
                    "before generating a preview; the legacy "
                    "approved_profile_version_id is also accepted."
                ),
                "options": ["save_profile_revision"],
            },
        )
    profile_repository = profile_revision_repository(settings.database_url)
    try:
        approved = load_profile_revision(
            artifact_dir, profile_revision_id, repository=profile_repository
        )
    except ProfileApprovalError as exc:
        raise GenerationError(
            400,
            {"error_code": "approved_profile_invalid", "message": str(exc)},
        ) from exc
    if approved is None:
        raise GenerationError(
            400,
            {
                "error_code": "approved_profile_invalid",
                "message": (
                    f"Profile revision '{profile_revision_id}' does not resolve "
                    "to a saved profile for this artifact."
                ),
            },
        )
    if approved.artifact_id != artifact_id:
        raise GenerationError(
            400,
            {
                "error_code": "approved_profile_artifact_mismatch",
                "message": (
                    f"The approved profile version belongs to artifact "
                    f"'{approved.artifact_id}', not '{artifact_id}'."
                ),
            },
        )
    try:
        current_draft = draft_sha256(artifact_dir)
    except ProfileApprovalError as exc:
        raise GenerationError(
            400,
            {"error_code": "approved_profile_draft_mismatch", "message": str(exc)},
        ) from exc
    if approved.source_draft_sha256 != current_draft:
        raise GenerationError(
            400,
            {
                "error_code": "approved_profile_draft_mismatch",
                "message": (
                    "The artifact's extraction draft changed after this profile "
                    "was approved; the approval no longer covers the current "
                    "source. Re-approve the profile for the current draft."
                ),
                "options": ["approve_profile"],
            },
        )
    if not is_current_approved_profile(
        artifact_dir, approved.profile_version_id, repository=profile_repository
    ):
        raise GenerationError(
            409,
            {
                "error_code": "approved_profile_superseded",
                "message": (
                    "The referenced approved profile version has been superseded "
                    "by a newer approval; pass the current profile_version_id or "
                    "approve again."
                ),
                "options": ["approve_profile"],
            },
        )
    validate_profile_source_review(artifact_dir, approved.profile)
    return approved


def load_approved_design(
    settings: GenerationSettings,
    artifact_id: str,
    artifact_dir: Path,
    design_id: str,
    profile: CandidateProfile,
) -> DesignArtifactState:
    """Load an approved design for generation. Rejects unapproved states,
    approved-profile version/checksum drift, and inventory drift, and never
    calls the designer."""
    if not is_safe_design_id(design_id):
        raise GenerationError(404, "Design not found.")
    try:
        state = load_design_state(
            design_dir_for(artifact_dir, design_id),
            repository=design_repository(settings.database_url),
        )
    except DesignServiceError as exc:
        raise GenerationError(404, str(exc)) from exc
    if state.artifact_id != artifact_id:
        raise GenerationError(404, "Design not found for this artifact.")
    if state.state != "approved":
        raise GenerationError(
            409,
            {
                "error_code": "design_not_approved",
                "state": state.state,
                "message": "The referenced design is not approved.",
            },
        )
    current_approved = load_current_approved_profile(
        artifact_dir, repository=profile_revision_repository(settings.database_url)
    )
    if current_approved is None:
        raise GenerationError(
            409,
            {
                "error_code": "approved_profile_missing",
                "message": (
                    "No approved profile version exists for this artifact; approve a "
                    "profile before generating from a design."
                ),
            },
        )
    if (
        state.approved_profile_version_id != current_approved.profile_version_id
        or state.profile_sha256 != current_approved.profile_sha256
    ):
        raise GenerationError(
            409,
            {
                "error_code": "design_profile_mismatch",
                "message": (
                    "The approved profile version/checksum changed since this design "
                    "was approved; request a new design for the current approved "
                    "profile version."
                ),
                "options": ["request_new_design", "approve_current_profile"],
            },
        )
    current_inventory = inventory_sha256(candidate_sections_from_profile(profile))
    if state.inventory_sha256 != current_inventory:
        raise GenerationError(
            409,
            {
                "error_code": "design_inventory_mismatch",
                "message": (
                    "Candidate section inventory changed since design approval; "
                    "request a new design or use allow_existing_template=true."
                ),
            },
        )
    return state


def require_layout_proof_approval(
    settings: GenerationSettings,
    *,
    artifact_dir: Path,
    artifact_id: str,
    target_format: TargetFormatMetadata,
    style_spec: TemplateStyleSpec | LayoutTemplateSpec,
) -> Any:
    """Load and validate the persisted layout-proof approval for generation.

    Returns the approved ``LayoutProofApproval`` or raises a 409 generation
    error. Never falls back to an unapproved template.
    """
    try:
        approval = load_layout_proof_approval(
            artifact_dir, repository=layout_proof_repository(settings.database_url)
        )
    except LayoutProofMismatchError as exc:
        raise GenerationError(
            409, {"error_code": "layout_proof_mismatch", "message": str(exc)}
        ) from exc
    except LayoutProofValidationError as exc:
        raise GenerationError(
            409, {"error_code": "layout_proof_validation_failed", "message": str(exc)}
        ) from exc
    if approval is None:
        raise GenerationError(
            409,
            {
                "error_code": "layout_proof_required",
                "message": (
                    "Generation from an uploaded target layout requires an approved "
                    "layout proof. Create the short/medium/long synthetic proofs and "
                    "approve them before generating."
                ),
                "options": ["create_layout_proofs", "approve_layout_proofs"],
            },
        )
    if not approval.is_approved:
        raise GenerationError(
            409,
            {
                "error_code": "layout_proof_approval_required",
                "message": "The layout proof set exists but is not approved.",
                "options": ["approve_layout_proofs"],
            },
        )
    if approval.artifact_id != artifact_id:
        raise GenerationError(
            409,
            {
                "error_code": "layout_proof_mismatch",
                "message": "The layout-proof approval does not belong to this artifact.",
            },
        )
    stored_path = artifact_dir / target_format.stored_filename
    try:
        target_checksum = target_checksum_from_bytes(stored_path.read_bytes())
    except OSError as exc:
        raise GenerationError(400, "Target format file is missing.") from exc
    if approval.target_checksum != target_checksum:
        raise GenerationError(
            409,
            {
                "error_code": "layout_proof_mismatch",
                "message": "The layout-proof approval does not cover this target checksum.",
            },
        )
    if approval.layout_spec_checksum != layout_spec_checksum(style_spec):
        raise GenerationError(
            409,
            {
                "error_code": "layout_proof_mismatch",
                "message": "The layout-proof approval does not cover this LayoutTemplateSpec.",
            },
        )
    return approval


def write_profile_debug_artifacts(
    *,
    artifact_dir: Path,
    profile: CandidateProfile,
    original_text: str | None,
    profile_filename: str = "candidate_profile.json",
    normalized_document: NormalizedCandidateDocument | None = None,
    field_evidence: Any | None = None,
) -> dict[str, str]:
    """Write profile debug artifacts. The default ``profile_filename``
    (``candidate_profile.json``) is the extraction draft and is written only
    by the process endpoint; the generation endpoint passes a separate
    ``generated_profile.json`` so the preserved draft is never overwritten
    (it is the file the profile-approval checksum is computed from)."""
    debug_artifacts: dict[str, str] = {}

    if original_text is not None:
        raw_text_path = artifact_dir / "raw_extracted_text.txt"
        raw_text_path.write_text(original_text, encoding="utf-8")
        debug_artifacts["raw_extracted_text"] = str(raw_text_path)

    if normalized_document is not None:
        normalized_path = artifact_dir / "normalized_candidate_document.json"
        write_json(normalized_path, normalized_document.model_dump(mode="json"))
        debug_artifacts["normalized_candidate_document"] = str(normalized_path)

    if field_evidence is not None:
        evidence_path = artifact_dir / "candidate_field_evidence.json"
        write_json(evidence_path, field_evidence.model_dump(mode="json"))
        debug_artifacts["candidate_field_evidence"] = str(evidence_path)

    profile_path = artifact_dir / profile_filename
    write_json(profile_path, profile.model_dump(mode="json"))
    debug_artifacts[Path(profile_filename).stem] = str(profile_path)

    missing_fields_path = artifact_dir / "missing_fields.json"
    write_json(
        missing_fields_path,
        [field.model_dump(mode="json") for field in profile.missing_fields],
    )
    debug_artifacts["missing_fields"] = str(missing_fields_path)

    return debug_artifacts


def write_debug_artifacts(
    *,
    artifact_dir: Path,
    profile: CandidateProfile,
    render_context: Any,
    original_text: str | None,
    target_format: TargetFormatMetadata | None = None,
    profile_filename: str = "candidate_profile.json",
) -> dict[str, str]:
    debug_artifacts = write_profile_debug_artifacts(
        artifact_dir=artifact_dir,
        profile=profile,
        original_text=original_text,
        profile_filename=profile_filename,
    )

    render_context_path = artifact_dir / "client_render_context.json"
    write_json(render_context_path, render_context.model_dump(mode="json"))
    debug_artifacts["client_render_context"] = str(render_context_path)

    if target_format is not None:
        target_format_path = artifact_dir / "target_format.json"
        write_json(target_format_path, target_format.model_dump(mode="json"))
        debug_artifacts["target_format"] = str(target_format_path)

    return debug_artifacts


def draft_followup(
    profile: CandidateProfile, *, stage: str = "message", language: str = "English"
) -> str:
    try:
        llm_client = build_llm_client(stage=stage)
    except LLMConfigurationError:
        llm_client = None
    return generate_followup_message(profile, llm_client=llm_client, language=language)


def is_safe_design_id(design_id: str) -> bool:
    return bool(design_id) and all(
        character.isalnum() or character in {"_", "-"} for character in design_id
    )


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass
class PreparedGeneration:
    """Validated inputs for one generation, reused by later phases."""

    artifact_id: str
    artifact_dir: Path
    target_format: TargetFormatMetadata | None
    profile_revision: ApprovedProfileVersion
    reviewed_profile: CandidateProfile
    render_context: Any
    style_spec: LayoutTemplateSpec | None
    design_id: str | None
    design_template_version: str | None
    uses_uploaded_target: bool
    layout_proof_approval: Any
    normalized_document: NormalizedCandidateDocument
    effective_layout_spec: LayoutTemplateSpec
    production_render_plan: Any
    fallback_warnings: list[str]
    manifest_inputs: dict[str, Any]
    debug_artifacts: dict[str, str] = field(default_factory=dict)


@dataclass
class RenderedGeneration:
    html_path: Path
    pdf_path: Path
    selected_plan: SelectedRenderPlan


@dataclass
class ValidatedGeneration:
    content_validation: Any
    structure_validation: Any


def prepare_generation(
    request: GenerateRequest, settings: GenerationSettings
) -> PreparedGeneration:
    """Resolve the artifact, saved revision, template, and proof; no rendering."""
    try:
        generation_strategy()
    except AgenticGenerationError as exc:
        raise GenerationError(500, str(exc)) from exc
    target_format = request.target_format
    artifact_id = (
        request.artifact_id
        or (target_format.artifact_id if target_format else None)
        or new_artifact_id()
    )
    artifact_dir = prepare_artifact_dir(
        settings.outputs_dir,
        artifact_id,
        allow_existing=request.artifact_id is not None or target_format is not None,
    )
    if target_format is None and request.artifact_id is not None:
        target_format = load_saved_target_format(artifact_dir)
    if target_format is not None and target_format.artifact_id != artifact_id:
        raise GenerationError(400, "target_format.artifact_id must match artifact_id.")
    fallback_warnings: list[str] = []
    if request.allow_built_in_fallback:
        fallback_warnings.append(
            "allow_built_in_fallback is deprecated and never honored: the "
            "built-in template is a normal no-target choice only and is not "
            "an escape for an uploaded target (retired 2026-08-28)."
        )
    if target_format is not None:
        validate_target_format_reference(artifact_id, artifact_dir, target_format)
        if target_format.analysis_status == "fallback_to_built_in":
            raise GenerationError(
                409,
                {
                    "error_code": "uploaded_target_fallback_removed",
                    "message": (
                        "The uploaded target could not be compiled into a measured "
                        "LayoutTemplateSpec. Built-in fallback is unavailable for "
                        "uploaded targets; analyze a supported target instead."
                    ),
                    "options": ["upload_new_target"],
                },
            )

    profile_revision_id = requested_profile_revision_id(
        request.profile_revision_id, request.approved_profile_version_id
    )
    profile_revision = require_profile_revision(
        settings,
        artifact_dir=artifact_dir,
        artifact_id=artifact_id,
        profile_revision_id=profile_revision_id,
    )
    merged_profile = profile_revision.profile.model_copy(
        update={
            "client_display_rules": {
                **profile_revision.profile.client_display_rules,
                **request.client_display_rules,
            }
        }
    )
    reviewed_profile = apply_missing_field_detection(merged_profile)

    render_context = build_client_render_context(
        reviewed_profile,
        blind_profile=request.blind_profile,
        template_name=request.template_name,
    )
    style_spec = None
    if (
        target_format is not None
        and target_format.role in {"pdf_reference", "docx_template"}
        and target_format.used_as_template_source
    ):
        try:
            style_spec = load_layout_template_spec(
                artifact_dir / LAYOUT_SPEC_FILENAME
            )
        except TargetAnalysisArtifactError as exc:
            raise GenerationError(
                400,
                (
                    "The uploaded target format has no valid artifact-root "
                    "LayoutTemplateSpec. Re-analyze this target."
                ),
            ) from exc
        if style_spec.structure_contract == "legacy" or (
            target_format.role == "pdf_reference"
            and style_spec.structure_contract != "measured"
        ):
            raise GenerationError(
                409,
                {
                    "error_code": "measured_layout_spec_required",
                    "message": (
                        "Generation from an uploaded target requires its measured "
                        "structure contract; legacy v1 migrations are not accepted."
                    ),
                    "options": ["reanalyze_target"],
                },
            )

    design_id = request.design_id
    design_template_version: str | None = None
    if design_id is not None:
        design_state = load_approved_design(
            settings, artifact_id, artifact_dir, design_id, reviewed_profile
        )
        style_spec = load_layout_template_spec(
            design_dir_for(artifact_dir, design_id) / "layout_template_spec.json"
        )
        design_template_version = design_state.template_version
    elif (
        get_design_strategy() in {DESIGN_STRATEGY_CLAUDE, DESIGN_STRATEGY_OPENAI}
        and target_format is not None
        and target_format.role in {"pdf_reference", "docx_template"}
        and target_format.used_as_template_source
        and not request.allow_existing_template
    ):
        raise GenerationError(
            409,
            {
                "error_code": "fallback_requires_approval",
                "message": (
                    "TEMPLATE_DESIGN_STRATEGY=claude_designer requires an approved "
                    "design before generation from a requested target format. "
                    "Approve a design for this artifact, or explicitly set "
                    "allow_existing_template=true to use the existing template path."
                ),
                "options": ["approve_design", "allow_existing_template"],
            },
        )
    elif (
        request.allow_existing_template
        and target_format is not None
        and get_design_strategy()
        in {DESIGN_STRATEGY_CLAUDE, DESIGN_STRATEGY_OPENAI}
    ):
        fallback_warnings.append(
            "EXPLICIT FALLBACK: allow_existing_template=true selected the "
            "artifact-root measured template instead of an approved designer template."
        )

    # Layout-proof gate: any generation driven by an uploaded target layout
    # must have a valid persisted layout-proof approval covering this exact
    # artifact, target checksum, and LayoutTemplateSpec. An approved designer
    # workflow (design_id) approves the template DESIGN; layout-proof approval
    # is a separate, independent gate that proves the exact template reflows
    # safely with short/medium/long synthetic content. Both are required and
    # neither silently substitutes for the other.
    uses_uploaded_target = (
        target_format is not None
        and target_format.used_as_template_source
        and style_spec is not None
    )
    if uses_uploaded_target:
        layout_proof_approval = require_layout_proof_approval(
            settings,
            artifact_dir=artifact_dir,
            artifact_id=artifact_id,
            target_format=target_format,
            style_spec=style_spec,
        )
    else:
        layout_proof_approval = None

    normalized_document = load_normalized_candidate_document(artifact_dir)
    effective_layout_spec = style_spec or built_in_layout_template_spec()
    production_render_plan = build_production_render_plan(
        approved_profile_version_id=profile_revision.profile_revision_id,
        profile_sha256=profile_revision.profile_sha256,
        document=normalized_document,
        context=render_context,
        layout_spec=effective_layout_spec,
        warnings=fallback_warnings,
    )

    try:
        manifest_inputs = capture_generation_inputs(
            artifact_dir,
            revision_id=profile_revision.profile_revision_id,
            layout_path=(
                (
                    design_dir_for(artifact_dir, design_id) / LAYOUT_SPEC_FILENAME
                    if design_id is not None
                    else artifact_dir / LAYOUT_SPEC_FILENAME
                )
                if uses_uploaded_target
                else None
            ),
            target_filename=target_format.stored_filename if target_format else None,
        )
    except (OSError, ValueError) as exc:
        raise GenerationError(
            500,
            {
                "error_code": "artifact_manifest_unavailable",
                "message": "Generation inputs could not be verified.",
            },
        ) from exc

    debug_artifacts = write_debug_artifacts(
        artifact_dir=artifact_dir,
        profile=reviewed_profile,
        render_context=render_context,
        # Request-supplied original_text is NOT trusted source evidence: only
        # resume processing may create raw_extracted_text.txt, and generation
        # must never rewrite or delete it (nor candidate_profile.json). The
        # legacy original_text field remains accepted for compatibility but is
        # ignored here.
        original_text=None,
        target_format=target_format,
        profile_filename="generated_profile.json",
    )
    debug_artifacts["profile_revision_id"] = profile_revision.profile_revision_id
    if request.approved_profile_version_id:
        debug_artifacts["approved_profile_version_id"] = (
            request.approved_profile_version_id
        )
    render_plan_path = artifact_dir / "production_render_plan.json"
    write_json(render_plan_path, production_render_plan.model_dump(mode="json"))
    debug_artifacts["production_render_plan"] = str(render_plan_path)
    generation_metadata_path = artifact_dir / "generation_metadata.json"
    write_json(
        generation_metadata_path,
        {
            "profile_revision_id": profile_revision.profile_revision_id,
            "profile_sha256": profile_revision.profile_sha256,
            "renderer_version": (
                active_renderer_version()
                if uses_uploaded_target
                else PIPELINE_RENDERER_VERSION
            ),
            "layout_template_version": effective_layout_spec.template_version,
            "layout_spec_sha256": production_render_plan.layout_spec_sha256,
            "layout_spec_filename": (
                LAYOUT_SPEC_FILENAME if uses_uploaded_target else None
            ),
            "fallback_warnings": fallback_warnings,
        },
    )
    debug_artifacts["generation_metadata"] = str(generation_metadata_path)

    return PreparedGeneration(
        artifact_id=artifact_id,
        artifact_dir=artifact_dir,
        target_format=target_format,
        profile_revision=profile_revision,
        reviewed_profile=reviewed_profile,
        render_context=render_context,
        style_spec=style_spec,
        design_id=design_id,
        design_template_version=design_template_version,
        uses_uploaded_target=uses_uploaded_target,
        layout_proof_approval=layout_proof_approval,
        normalized_document=normalized_document,
        effective_layout_spec=effective_layout_spec,
        production_render_plan=production_render_plan,
        fallback_warnings=fallback_warnings,
        manifest_inputs=manifest_inputs,
        debug_artifacts=debug_artifacts,
    )


def render_prepared_generation(prepared: PreparedGeneration) -> RenderedGeneration:
    """Render the prepared inputs with the current native or approved renderer."""
    artifact_id = prepared.artifact_id
    artifact_dir = prepared.artifact_dir
    html_path = artifact_dir / "candidate_profile.html"
    pdf_path = artifact_dir / "candidate_profile.pdf"
    try:
        if prepared.uses_uploaded_target:
            # Approval-aware candidate renderer: renders only with an approved,
            # artifact-scoped, checksum-matched layout-proof approval.
            html_path, exported_pdf_path = render_candidate_document(
                approval=prepared.layout_proof_approval,
                artifact_id=artifact_id,
                layout_spec=prepared.style_spec,
                render_context=prepared.render_context,
                output_dir=artifact_dir,
                target_pdf=(
                    artifact_dir / prepared.target_format.stored_filename
                ),
            )
            # Read the arrangement plan of the version that was just published;
            # a stale or unselected plan is reported as unknown, never used.
            selected_plan = load_selected_render_plan(artifact_dir)
        else:
            _, plan, html = render_layout_template(
                prepared.effective_layout_spec,
                candidate_document_from_context(
                    prepared.render_context,
                    candidate_id=(
                        f"candidate-{prepared.profile_revision.profile_sha256[:16]}"
                    ),
                    include_subheading=(
                        prepared.effective_layout_spec.header.show_candidate_subheading
                    ),
                ),
            )
            selected_plan = SelectedRenderPlan(plan=plan)
            html_path.write_text(html, encoding="utf-8")
            exported_pdf_path = export_html_to_pdf(html_path, pdf_path)
    except (
        ValueError,
        HtmlToPdfExportError,
        FileNotFoundError,
        LayoutProofRenderError,
    ) as exc:
        raise GenerationError(500, str(exc)) from exc

    if exported_pdf_path != pdf_path and exported_pdf_path.exists():
        exported_pdf_path.replace(pdf_path)
    if prepared.uses_uploaded_target:
        prepared.debug_artifacts["agentic_generation"] = str(
            artifact_dir / "agentic_generation.json"
        )
    return RenderedGeneration(
        html_path=html_path, pdf_path=pdf_path, selected_plan=selected_plan
    )


def validate_generation(
    prepared: PreparedGeneration, rendered: RenderedGeneration
) -> ValidatedGeneration:
    """Run the required content and structure gates on the rendered files."""
    artifact_dir = prepared.artifact_dir
    try:
        content_validation = validate_generated_content(
            prepared.render_context,
            pdf_path=rendered.pdf_path,
            layout_spec=prepared.effective_layout_spec,
        )
    except (OSError, ValueError) as exc:
        raise GenerationError(
            500,
            {
                "error_code": "content_validation_unavailable",
                "message": f"Generated content could not be verified: {exc}",
            },
        ) from exc
    content_validation_path = artifact_dir / "content_validation.json"
    write_json(content_validation_path, content_validation.model_dump(mode="json"))
    prepared.debug_artifacts["content_validation"] = str(content_validation_path)
    if not content_validation.passed:
        raise GenerationError(
            500,
            {
                "error_code": "generated_content_incomplete",
                "message": "Approved content is missing from a generated output.",
                "checks": [
                    check.model_dump(mode="json")
                    for check in content_validation.checks
                    if not check.passed
                ],
            },
        )

    try:
        structure_validation = validate_output_structure(
            prepared.render_context,
            html_path=rendered.html_path,
            layout_spec=prepared.effective_layout_spec,
            pdf_path=rendered.pdf_path,
            coverage_ledger=build_source_coverage_ledger(
                prepared.profile_revision.profile, prepared.normalized_document
            ),
        )
    except (OSError, ValueError) as exc:
        raise GenerationError(
            500,
            {
                "error_code": "structure_validation_unavailable",
                "message": f"Generated structure could not be verified: {exc}",
            },
        ) from exc
    structure_validation_path = artifact_dir / "structure_validation.json"
    write_json(
        structure_validation_path, structure_validation.model_dump(mode="json")
    )
    prepared.debug_artifacts["structure_validation"] = str(structure_validation_path)
    if not structure_validation.passed:
        raise GenerationError(
            500,
            {
                "error_code": "generated_structure_incomplete",
                "message": (
                    "Generated output does not match the expected entry "
                    "structure (tiers, bullet state, ordering, bullet counts)."
                ),
                "checks": [
                    check.model_dump(mode="json")
                    for check in structure_validation.checks
                    if not check.matched or check.issues
                ],
            },
        )
    return ValidatedGeneration(
        content_validation=content_validation,
        structure_validation=structure_validation,
    )


def record_generation(
    request: GenerateRequest,
    settings: GenerationSettings,
    prepared: PreparedGeneration,
    rendered: RenderedGeneration,
    validated: ValidatedGeneration,
) -> GenerateResponse:
    """Persist manifests/publication records and build the existing response."""
    artifact_id = prepared.artifact_id
    artifact_dir = prepared.artifact_dir
    debug_artifacts = prepared.debug_artifacts
    effective_layout_spec = prepared.effective_layout_spec
    reviewed_profile = prepared.reviewed_profile
    target_format = prepared.target_format

    debug_artifacts["generated_html"] = str(rendered.html_path)
    debug_artifacts["generated_pdf"] = str(rendered.pdf_path)
    if prepared.design_id is not None:
        debug_artifacts["design_id"] = prepared.design_id
        debug_artifacts["design_state"] = "approved"
        debug_artifacts["layout_template_spec"] = str(
            design_dir_for(artifact_dir, prepared.design_id) / "layout_template_spec.json"
        )
        debug_artifacts["template_version"] = prepared.design_template_version or ""

    html_url = artifact_url(artifact_id, "candidate_profile.html")
    pdf_url = artifact_url(artifact_id, "candidate_profile.pdf")
    visual_comparison: Any | None = None
    visual_comparison_url: str | None = None
    visual_comparison_artifact_urls: dict[str, str] = {}
    if target_format is not None and target_format.role == "pdf_reference":
        try:
            visual_comparison = compare_pdf_layouts(
                artifact_dir / target_format.stored_filename,
                rendered.pdf_path,
                artifact_dir,
            )
        except VisualComparisonError as exc:
            comparison_error_path = artifact_dir / "visual_comparison_error.txt"
            comparison_error_path.write_text(str(exc), encoding="utf-8")
            debug_artifacts["visual_comparison_error"] = str(comparison_error_path)
        else:
            visual_comparison_url = artifact_url(artifact_id, COMPARISON_FILENAME)
            visual_comparison_artifact_urls["report"] = visual_comparison_url
            debug_artifacts["visual_comparison"] = str(
                artifact_dir / COMPARISON_FILENAME
            )
            for index, filename in enumerate(
                visual_comparison.target_page_filenames, start=1
            ):
                visual_comparison_artifact_urls[f"target_page_{index:03d}"] = (
                    artifact_url(artifact_id, filename)
                )
                debug_artifacts[f"comparison_target_page_{index:03d}"] = str(
                    artifact_dir / filename
                )
            for index, filename in enumerate(
                visual_comparison.generated_page_filenames, start=1
            ):
                visual_comparison_artifact_urls[f"generated_page_{index:03d}"] = (
                    artifact_url(artifact_id, filename)
                )
                debug_artifacts[f"comparison_generated_page_{index:03d}"] = str(
                    artifact_dir / filename
                )
            for page in visual_comparison.pages:
                visual_comparison_artifact_urls[
                    f"difference_page_{page.page_number:03d}"
                ] = artifact_url(artifact_id, page.difference_filename)
                debug_artifacts[
                    f"visual_comparison_page_{page.page_number:03d}"
                ] = str(artifact_dir / page.difference_filename)

    # The arrangement report belongs to this exact output: it is written before
    # the manifest so the published manifest owns its checksum.
    output_identity = current_output_identity(artifact_dir)
    composition_report = build_composition_report(
        artifact_id=artifact_id,
        output_identity=output_identity,
        production_plan=prepared.production_render_plan,
        selected=rendered.selected_plan,
    )
    composition_report_path = write_composition_report(
        artifact_dir, composition_report
    )
    debug_artifacts["composition_report"] = str(composition_report_path)

    try:
        write_artifact_manifest(
            artifact_dir,
            artifact_id=artifact_id,
            inputs=prepared.manifest_inputs,
            layout_spec=effective_layout_spec,
            blind_profile=request.blind_profile,
            disclosure_rules=reviewed_profile.client_display_rules,
            uses_agentic=bool(prepared.uses_uploaded_target),
            repository=generation_repository(settings.database_url),
            visual_status=(
                "diagnostic"
                if visual_comparison is not None
                else "unavailable"
                if target_format is not None and target_format.role == "pdf_reference"
                else "not_run"
            ),
        )
    except (OSError, ValueError, PyPdfError) as exc:
        raise GenerationError(
            500,
            {
                "error_code": "artifact_manifest_unavailable",
                "message": "Generation provenance could not be verified or saved.",
            },
        ) from exc
    debug_artifacts["artifact_manifest"] = str(artifact_dir / MANIFEST_FILENAME)

    try:
        settings.artifact_store().record_generated_outputs(
            artifact_id=artifact_id,
            artifact_dir=artifact_dir,
            template_name=request.template_name,
            blind_profile=request.blind_profile,
            html_surface_url=html_url,
            pdf_download_url=pdf_url,
            pdf_preview_url=pdf_url,
            missing_fields=reviewed_profile.missing_fields,
            debug_artifacts=debug_artifacts,
        )
    except ArtifactStoreError as exc:
        raise GenerationError(500, str(exc)) from exc

    client_ready = is_final_acceptance_current(
        artifact_dir,
        profile_repository=profile_revision_repository(settings.database_url),
        repository=final_acceptance_repository(settings.database_url),
    )

    return GenerateResponse(
        artifact_id=artifact_id,
        html_surface_url=html_url,
        pdf_download_url=pdf_url,
        pdf_preview_url=pdf_url,
        artifact_metadata_url=artifact_metadata_url(artifact_id),
        followup_message=draft_followup(reviewed_profile),
        missing_fields=reviewed_profile.missing_fields,
        debug_artifacts=debug_artifacts,
        visual_comparison_url=visual_comparison_url,
        visual_comparison=visual_comparison,
        visual_comparison_artifact_urls=visual_comparison_artifact_urls,
        design_id=prepared.design_id,
        template_version=prepared.design_template_version,
        profile_sha256=prepared.profile_revision.profile_sha256,
        profile_revision_id=prepared.profile_revision.profile_revision_id,
        layout_template_version=output_identity.layout_template_version,
        layout_spec_sha256=output_identity.layout_spec_sha256,
        html_sha256=output_identity.html_sha256,
        pdf_sha256=output_identity.pdf_sha256,
        composition_report=composition_report,
        composition_report_url=artifact_url(
            artifact_id, COMPOSITION_REPORT_FILENAME
        ),
        client_ready=client_ready,
    )


def generate_outputs(
    request: GenerateRequest, *, settings: GenerationSettings
) -> GenerateResponse:
    """Prepare inputs → render → validate → record for one generation request."""
    prepared = prepare_generation(request, settings)
    rendered = render_prepared_generation(prepared)
    validated = validate_generation(prepared, rendered)
    return record_generation(request, settings, prepared, rendered, validated)
