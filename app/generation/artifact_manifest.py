"""Checksum-bound provenance for the current local generation, not acceptance."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pypdf import PdfReader

from app.extraction.candidate_schema import CandidateProfile, DisplayRule
from app.final_acceptance import FinalOutputIdentity, current_output_identity, file_sha256
from app.generation.composition_report import COMPOSITION_REPORT_FILENAME
from app.profile_approval import ProfileApprovalError, ProfileRevisionRepository, draft_sha256, load_current_approved_profile
from app.generation.template_mapper import CLIENT_DISCLOSURE_VERSION
from app.template_analysis.schemas import LayoutTemplateSpec


MANIFEST_FILENAME = "artifact_manifest.json"
LAYOUT_SNAPSHOT_FILENAME = "generation_layout_spec.json"
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FileIdentity(ManifestModel):
    path: str
    sha256: Digest
    size_bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("Manifest paths must stay inside the owning artifact")
        return value


class FontIdentity(ManifestModel):
    base_font: str
    subtype: str
    embedded_sha256: Digest | None


class CheckIdentity(ManifestModel):
    status: Literal["passed", "diagnostic", "unavailable", "not_run"]
    report: FileIdentity | None = None


class ExecutionIdentity(ManifestModel):
    mode: Literal["deterministic", "recorded_agentic"]
    models: dict[str, str] = Field(default_factory=dict)
    prompt_sha256: dict[str, Digest] = Field(default_factory=dict)
    selected_version: str | None = None


class AnalysisIdentity(ManifestModel):
    provider: str | None = None
    provider_version: str | None = None
    model: str | None = None
    prompt_version: str | None = None


class ArtifactManifest(ManifestModel):
    schema_version: Literal["artifact-manifest/1"] = "artifact-manifest/1"
    artifact_id: str
    created_at: str
    output_identity: FinalOutputIdentity
    inputs: dict[str, FileIdentity]
    outputs: dict[str, FileIdentity]
    schemas: dict[str, str | None]
    renderer_version: str
    component_source_sha256: dict[str, Digest]
    execution: ExecutionIdentity
    analysis: dict[str, AnalysisIdentity]
    disclosure_version: str
    blind_profile: bool
    disclosure_rules: dict[str, DisplayRule]
    pdf_producer: str | None
    fonts: list[FontIdentity]
    checks: dict[str, CheckIdentity]

    @model_validator(mode="after")
    def complete_successful_generation(self) -> ArtifactManifest:
        required_inputs = {
            "profile_revision": f"profiles/approved_profile_{self.output_identity.profile_revision_id}.json",
            "source_draft": "candidate_profile.json",
            "effective_layout": LAYOUT_SNAPSHOT_FILENAME,
        }
        required_outputs = (
            "candidate_profile.html", "candidate_profile.pdf", "generation_metadata.json",
            "production_render_plan.json", "client_render_context.json", "generated_profile.json",
        )
        if any(key not in self.inputs or self.inputs[key].path != path for key, path in required_inputs.items()):
            raise ValueError("Manifest is missing a required generation input")
        if any(key not in self.outputs or self.outputs[key].path != key for key in required_outputs):
            raise ValueError("Manifest is missing a required generation output")
        if (self.outputs["candidate_profile.html"].sha256 != self.output_identity.html_sha256
                or self.outputs["candidate_profile.pdf"].sha256 != self.output_identity.pdf_sha256):
            raise ValueError("Manifest output checksums disagree with the output identity")
        for name in ("content", "structure"):
            check = self.checks.get(name)
            if check is None or check.status != "passed" or check.report is None or check.report.path != f"{name}_validation.json":
                raise ValueError("Manifest is missing an independent successful validation report")
        if self.execution.mode == "recorded_agentic" and not (
            "run_configuration" in self.outputs and "frozen_prompts" in self.outputs
            and self.execution.models and self.execution.prompt_sha256 and self.execution.selected_version
            and self.checks.get("agentic_output_gates") is not None
            and self.checks["agentic_output_gates"].status == "passed"
            and self.checks["agentic_output_gates"].report is not None
        ):
            raise ValueError("Recorded Agentic execution requires frozen model/prompt provenance")
        return self


class GenerationRepository(Protocol):
    def save(self, manifest: ArtifactManifest, layout_spec: LayoutTemplateSpec) -> None: ...

    def current(self, artifact_id: str) -> ArtifactManifest | None: ...

    def output_directory(self, artifact_dir: Path) -> Path: ...

    def selected_output(self, artifact_dir: Path) -> tuple[Path, ArtifactManifest | None]: ...


def identify_file(directory: Path, path: Path) -> FileIdentity:
    root, resolved = directory.resolve(), path.resolve()
    relative = resolved.relative_to(root).as_posix()
    return FileIdentity(path=relative, sha256=file_sha256(resolved), size_bytes=resolved.stat().st_size)


def capture_generation_inputs(
    directory: Path, *, revision_id: str, layout_path: Path | None,
    target_filename: str | None,
) -> dict[str, FileIdentity]:
    """Freeze existing inputs before rendering; absent legacy evidence stays absent."""
    paths = {
        "profile_revision": directory / "profiles" / f"approved_profile_{revision_id}.json",
        "source_draft": directory / "candidate_profile.json",
    }
    for name in (
        "source_resume.pdf", "source_resume.docx", "raw_extracted_text.txt",
        "normalized_candidate_document.json", "candidate_field_evidence.json",
        "candidate_segmentation.json", "segmentation_audit.json", "llm_segmentation.json",
    ):
        if (directory / name).is_file():
            paths[name] = directory / name
    if target_filename is not None:
        paths["target_document"] = directory / target_filename
        for name in ("adobe_raw_response.json", "normalized_layout_evidence.json", "target_layout_evidence.json"):
            if (directory / name).is_file():
                paths[name] = directory / name
    if layout_path is not None:
        paths["approved_layout"] = layout_path
        paths["layout_proof_approval"] = directory / "layout_proofs" / "approval.json"
        if (layout_path.parent / "design_state.json").is_file():
            paths["approved_design_state"] = layout_path.parent / "design_state.json"
    return {name: identify_file(directory, path) for name, path in paths.items()}


def _matches(directory: Path, reference: FileIdentity) -> bool:
    return identify_file(directory, directory / reference.path) == reference


def _pdf_fonts(path: Path) -> tuple[str | None, list[FontIdentity]]:
    fonts: dict[tuple[str, str, str | None], FontIdentity] = {}
    with PdfReader(path) as pdf:
        for page in pdf.pages:
            resources = page.get("/Resources")
            font_map = resources.get_object().get("/Font") if resources else None
            for value in font_map.get_object().values() if font_map else ():
                font = value.get_object()
                descendants = font.get("/DescendantFonts")
                faces = descendants.get_object() if descendants is not None else [font]
                for face_ref in faces:
                    face = face_ref.get_object()
                    descriptor_ref = face.get("/FontDescriptor")
                    descriptor = descriptor_ref.get_object() if descriptor_ref else {}
                    embedded = next((descriptor[key].get_object().get_data()
                                     for key in ("/FontFile", "/FontFile2", "/FontFile3") if key in descriptor), None)
                    item = FontIdentity(base_font=str(face.get("/BaseFont", "unknown")),
                        subtype=str(face.get("/Subtype", "unknown")),
                        embedded_sha256=hashlib.sha256(embedded).hexdigest() if embedded is not None else None)
                    fonts[(item.base_font, item.subtype, item.embedded_sha256)] = item
        producer = str(pdf.metadata.producer) if pdf.metadata and pdf.metadata.producer else None
    return producer, sorted(fonts.values(), key=lambda item: (item.base_font, item.subtype, item.embedded_sha256 or ""))


def write_artifact_manifest(
    directory: Path, *, artifact_id: str, inputs: dict[str, FileIdentity],
    layout_spec: LayoutTemplateSpec, blind_profile: bool, disclosure_rules: dict[str, DisplayRule],
    uses_agentic: bool, visual_status: Literal["diagnostic", "unavailable", "not_run"],
    repository: GenerationRepository | None = None,
) -> ArtifactManifest:
    if not all(_matches(directory, reference) for reference in inputs.values()):
        raise ValueError("Generation inputs changed while rendering; no new manifest was published")
    identity = current_output_identity(directory)
    metadata = json.loads((directory / "generation_metadata.json").read_text())
    schemas: dict[str, str | None] = {
        # CandidateProfile has no explicit version field; bind its actual JSON schema.
        "candidate_profile_json_schema_sha256": hashlib.sha256(json.dumps(CandidateProfile.model_json_schema(), sort_keys=True).encode()).hexdigest(),
        "layout_template": layout_spec.schema_version,
    }
    for key, name in (("profile_revision", inputs["profile_revision"].path),
                      ("normalized_candidate", "normalized_candidate_document.json"),
                      ("target_evidence", "normalized_layout_evidence.json"),
                      ("candidate_segmentation", "candidate_segmentation.json")):
        recorded = any(ref.path == name for ref in inputs.values())
        schemas[key] = json.loads((directory / name).read_text()).get("schema_version") if recorded else None
    analysis = {"candidate": AnalysisIdentity(), "target": AnalysisIdentity()}
    for stage, name in (("candidate", "candidate_segmentation.json"), ("target", "normalized_layout_evidence.json")):
        if name in inputs:
            recorded = json.loads((directory / name).read_text())
            analysis[stage] = AnalysisIdentity(**{key: recorded.get(key) for key in AnalysisIdentity.model_fields})
    outputs = {name: identify_file(directory, directory / name) for name in (
        "candidate_profile.html", "candidate_profile.pdf", "generation_metadata.json",
        "production_render_plan.json", "client_render_context.json", "generated_profile.json",
    )}
    # Additive: older manifests stay valid, a new one owns the report's checksum.
    if (directory / COMPOSITION_REPORT_FILENAME).is_file():
        outputs["composition_report"] = identify_file(
            directory, directory / COMPOSITION_REPORT_FILENAME
        )
    checks: dict[str, CheckIdentity] = {}
    for name in ("content", "structure"):
        reference = identify_file(directory, directory / f"{name}_validation.json")
        if json.loads((directory / reference.path).read_text()).get("passed") is not True:
            raise ValueError(f"Cannot publish a successful manifest without {name} validation")
        checks[name] = CheckIdentity(status="passed", report=reference)
    checks["visual_comparison"] = CheckIdentity(status=visual_status,
        report=identify_file(directory, directory / "visual_comparison.json") if visual_status == "diagnostic" else None)
    checks["agentic_output_gates"] = CheckIdentity(status="not_run")
    checks["target_candidate_facts"] = CheckIdentity(status="not_run")
    execution = ExecutionIdentity(mode="deterministic")
    if uses_agentic:
        report_path = directory / "agentic_generation.json"
        report = json.loads(report_path.read_text())
        outputs["agentic_generation"] = identify_file(directory, report_path)
        if not report.get("run_directory"):
            raise ValueError("Agentic generation is missing its frozen execution provenance")
        run_dir = (directory / report["run_directory"]).resolve()
        run_dir.relative_to(directory.resolve())
        outputs["run_configuration"] = identify_file(directory, run_dir / "run_config.json")
        outputs["frozen_prompts"] = identify_file(directory, run_dir / "prompts.json")
        config = json.loads((run_dir / "run_config.json").read_text())
        execution = ExecutionIdentity(mode="recorded_agentic", selected_version=report["selected_version"],
            models={key: config["model_configuration"][key] for key in ("text_model", "vision_model")},
            prompt_sha256=config["prompts"]["sha256"])
        if report.get("gate_validation", {}).get("passed") is not True:
            raise ValueError("Cannot publish a successful manifest without Agentic output gates")
        checks["agentic_output_gates"] = CheckIdentity(status="passed", report=outputs["agentic_generation"])
        if report["gate_validation"].get("gates", {}).get("no_target_candidate_facts") is True:
            checks["target_candidate_facts"] = CheckIdentity(status="passed", report=outputs["agentic_generation"])
    snapshot = directory / LAYOUT_SNAPSHOT_FILENAME
    snapshot.write_text(layout_spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    inputs = {**inputs, "effective_layout": identify_file(directory, snapshot)}
    root = Path(__file__).resolve().parents[2]
    components = {name: file_sha256(root / name) for name in (
        "app/pipeline/layout_compiler.py", "app/pipeline/html_renderer.py",
        "app/generation/template_mapper.py", "app/generation/chrome_html_to_pdf.py",
    )}
    producer, fonts = _pdf_fonts(directory / "candidate_profile.pdf")
    manifest = ArtifactManifest(artifact_id=artifact_id, created_at=datetime.now(UTC).isoformat(),
        output_identity=identity, inputs=inputs, outputs=outputs, schemas=schemas,
        renderer_version=metadata["renderer_version"], component_source_sha256=components,
        execution=execution, analysis=analysis, disclosure_version=CLIENT_DISCLOSURE_VERSION,
        blind_profile=blind_profile, disclosure_rules=disclosure_rules,
        pdf_producer=producer, fonts=fonts, checks=checks)
    if repository is not None:
        repository.save(manifest, layout_spec)
    # shortcut: single-writer local publication; replace with P1/P2 job-owned publication.
    temporary = directory / f".{MANIFEST_FILENAME}.tmp"
    temporary.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(directory / MANIFEST_FILENAME)
    return manifest


def is_artifact_manifest_current(
    directory: str | Path, *, profile_repository: ProfileRevisionRepository | None = None,
    repository: GenerationRepository | None = None,
) -> bool:
    directory = Path(directory)
    try:
        output_dir = repository.output_directory(directory) if repository is not None else directory
        manifest = ArtifactManifest.model_validate_json((output_dir / MANIFEST_FILENAME).read_text())
        if repository is not None and repository.current(directory.name) != manifest:
            return False
        revision = load_current_approved_profile(directory, repository=profile_repository)
        if (manifest.artifact_id != directory.name or revision is None
                or revision.profile_revision_id != manifest.output_identity.profile_revision_id
                or revision.profile_sha256 != manifest.output_identity.profile_sha256
                or revision.source_draft_sha256 != manifest.inputs["source_draft"].sha256
                or draft_sha256(directory) != manifest.inputs["source_draft"].sha256):
            return False
        references = [*manifest.inputs.values(), *manifest.outputs.values(),
                      *(check.report for check in manifest.checks.values() if check.report is not None)]
        return (manifest.output_identity == current_output_identity(output_dir)
                and all(_matches(output_dir, reference) for reference in references))
    except (OSError, ValueError, KeyError, ProfileApprovalError):
        return False
