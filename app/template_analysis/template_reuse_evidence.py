"""Stored evidence one artifact supplies for a reusable-template identity.

Reads only what the artifact already stores: the compiled spec kept beside each
proof, the target-evidence record beside the uploaded target, the proof runs'
frozen `run_config.json`, and the recorded rendering environment. No provider
call, no render, and no write: a missing or unreadable record refuses reuse with
the identity member it stood for (ADR 0009 rules 1 and 6).

Member numbers are the ADR 0009 identity inventory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.pipeline.fonts import installed_font_families
from app.target_publication import TARGETS_DIRNAME, TargetSet
from app.template_analysis.artifacts import (
    LAYOUT_SPEC_FILENAME,
    TARGET_EVIDENCE_FILENAME,
    TargetAnalysisArtifactError,
    load_layout_template_spec,
)
from app.template_analysis.design_schemas import TargetLayoutEvidence
from app.template_analysis.layout_proof_artifacts import (
    layout_proofs_dir_for,
    layout_spec_checksum,
)
from app.template_analysis.layout_proof_rendering import LayoutProofMismatchError
from app.template_analysis.layout_proof_schemas import (
    FontAssetIdentity,
    LayoutProofApproval,
    ReusableTemplateReference,
    VariantProofEvidence,
)
from app.template_analysis.layout_proof_service import (
    LayoutProofValidationError,
    load_layout_proof_approval,
)
from app.template_analysis.schemas import LayoutTemplateSpec, TemplateStyleSpec


class ReusableTemplateEvidenceError(RuntimeError):
    """A stored record the reuse identity needs is missing or unreadable.

    ``member`` names the identity member it stood for, so the refusal can say
    which part of the identity is unavailable.
    """

    def __init__(self, message: str, *, member: str) -> None:
        super().__init__(message)
        self.member = member


def _proof_spec(artifact_dir: str | Path, evidence: VariantProofEvidence) -> LayoutTemplateSpec:
    """The compiled spec kept beside one variant's proof files."""
    proof_dir = layout_proofs_dir_for(artifact_dir) / evidence.render_result.proof_id
    try:
        return load_layout_template_spec(proof_dir / LAYOUT_SPEC_FILENAME)
    except TargetAnalysisArtifactError as exc:
        raise ReusableTemplateEvidenceError(
            f"the '{evidence.variant.value}' proof keeps no readable compiled "
            "LayoutTemplateSpec, so its template cannot be reused",
            member="layout_contract",
        ) from exc


def source_artifact_dir(artifact_dir: str | Path, artifact_id: str) -> Path:
    """The sibling directory of the artifact a reference points at."""
    if not artifact_id or artifact_id in {".", ".."} or "/" in artifact_id or "\\" in artifact_id:
        raise ReusableTemplateEvidenceError(
            f"'{artifact_id}' is not a usable source artifact id", member="approval_id"
        )
    return Path(artifact_dir).resolve().parent / artifact_id


def source_approval(
    source_dir: Path,
    reference: ReusableTemplateReference,
    repository: Any | None,
) -> LayoutProofApproval:
    """Load and re-verify the referenced approval from the source artifact."""
    try:
        approval = load_layout_proof_approval(source_dir, repository=repository)
    except (LayoutProofMismatchError, LayoutProofValidationError) as exc:
        raise ReusableTemplateEvidenceError(
            f"the referenced proof set of '{reference.artifact_id}' (approval "
            f"{reference.approval_id}) is unusable: {exc}",
            member="proof_artifacts",
        ) from exc
    if approval is None:
        raise ReusableTemplateEvidenceError(
            f"the referenced approval {reference.approval_id} is absent for source "
            f"artifact '{reference.artifact_id}', so it cannot be reused",
            member="approval_id",
        )
    if approval.approval_id != reference.approval_id or not approval.is_approved:
        raise ReusableTemplateEvidenceError(
            f"the referenced approval {reference.approval_id} is not the approved set "
            f"of '{reference.artifact_id}' any more (current: {approval.approval_id}, "
            f"{approval.state})",
            member="approval_id",
        )
    return approval


def proven_layout_spec(
    artifact_dir: str | Path, approval: LayoutProofApproval
) -> LayoutTemplateSpec:
    """Members 5 and 12's template side: the spec the proof set was rendered from.

    Read from the proof directories rather than the artifact root, so recording
    and re-checking a reference see the same proven template. The exact spec
    bytes stay bound by member 4 (the approval's checksum) and by member 11 (the
    rendered proof bytes); a JSON round trip through the proof directory can
    renormalise numbers, so agreeing specs are compared with each other here. A
    v1 ``TemplateStyleSpec`` or a non-``measured`` contract is never reusable
    (rule 5), so this refuses instead of reporting a narrower template.
    """
    specs = [_proof_spec(artifact_dir, evidence) for evidence in approval.variants.values()]
    if len({layout_spec_checksum(spec) for spec in specs}) != 1:
        raise ReusableTemplateEvidenceError(
            "the proof variants were rendered from different LayoutTemplateSpecs",
            member="layout_contract",
        )
    if specs[0].structure_contract != "measured":
        raise ReusableTemplateEvidenceError(
            f"a '{specs[0].structure_contract}' structure contract is never reusable; "
            "create and approve fresh short/medium/long proofs for a measured template",
            member="layout_contract",
        )
    return specs[0]


def execution_identity(
    artifact_dir: str | Path, approval: LayoutProofApproval
) -> dict[str, object]:
    """Member 8: the frozen run configuration the proof runs recorded.

    A proof set rendered without the model loop records no configuration, so its
    provenance mode alone identifies the execution. Variants must agree.
    """
    modes = {
        evidence.render_result.execution_mode for evidence in approval.variants.values()
    }
    if len(modes) != 1:
        raise ReusableTemplateEvidenceError(
            "the proof set mixes execution provenance: " + ", ".join(sorted(modes)),
            member="execution_identity",
        )
    recorded = [
        frozen_run_config(artifact_dir, evidence.render_result.proof_id)
        for evidence in approval.variants.values()
    ]
    present = [item for item in recorded if item is not None]
    if len(present) not in {0, len(recorded)} or len(
        {json.dumps(item, sort_keys=True) for item in present}
    ) > 1:
        raise ReusableTemplateEvidenceError(
            "the proof runs recorded different frozen execution contracts (models, "
            "sampling, budgets, prompts, tools or gate policy)",
            member="execution_identity",
        )
    return {"mode": modes.pop(), "frozen": present[0] if present else None}


def frozen_run_config(artifact_dir: str | Path, proof_id: str) -> dict[str, object] | None:
    """The recorded models/sampling/budgets/prompts/tools/gate policy, if any."""
    paths = sorted(
        (layout_proofs_dir_for(artifact_dir) / proof_id / "pipeline_runs").glob(
            "*/pipeline/run_config.json"
        )
    )
    if not paths:
        return None
    try:
        config = json.loads(paths[-1].read_text(encoding="utf-8"))
        return {
            "models": config["model_configuration"],
            "sampling": config["sampling"],
            "budgets": config["budgets"],
            "prompts_sha256": config["prompts"]["sha256"],
            "permitted_tools": config["permitted_tools"],
            "gate_policy": config["gate_policy"],
        }
    except (OSError, ValueError, KeyError) as exc:
        raise ReusableTemplateEvidenceError(
            f"the proof run's frozen run configuration is unreadable: {exc}",
            member="execution_identity",
        ) from exc


def font_asset_identity(
    spec: LayoutTemplateSpec | TemplateStyleSpec, approval: LayoutProofApproval
) -> FontAssetIdentity:
    """Member 12: template font families plus the recorded render environment.

    The proof HTML/PDF checksums (member 11) bind the embedded font bytes; this
    member names the families the template asks for, which of them this host
    has, and the environment each proof was exported in.
    """
    environments = {
        json.dumps(evidence.render_result.rendering_environment or {}, sort_keys=True)
        for evidence in approval.variants.values()
    }
    if len(environments) != 1:
        raise ReusableTemplateEvidenceError(
            "the proof variants recorded different rendering environments",
            member="font_asset_identity",
        )
    families = spec_font_families(spec)
    return FontAssetIdentity(
        template_font_families=families,
        proof_rendering_environment={
            **json.loads(environments.pop()),
            "installed_font_families": sorted(
                installed
                for installed in installed_font_families()
                if any(requested.casefold().replace(" ", "") in installed for requested in families)
            ),
        },
    )


def spec_font_families(spec: LayoutTemplateSpec | TemplateStyleSpec) -> list[str]:
    """Every font family and postscript name the template names, sorted."""
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"font_family", "font_postscript_name"} and isinstance(value, str):
                    found.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(spec.model_dump(mode="json"))
    return sorted(found)


def target_evidence(directory: str | Path) -> TargetLayoutEvidence:
    """Member 3's record, read from an artifact's published target set.

    ``directory`` is the artifact directory, or the target-set epoch a caller
    already pinned its target PDF from; the evidence record is published beside
    the target it describes. A pinned epoch is read where it is, as the set it
    already is: handed to a view as an artifact root it would read as a legacy
    artifact and adopt itself into its own ``.targets`` on every resolution.
    """
    pinned = Path(directory)
    view = (
        TargetSet(pinned.parent.parent, resolved=pinned)
        if pinned.parent.name == TARGETS_DIRNAME
        else TargetSet(pinned)
    )
    try:
        path = view.read(TARGET_EVIDENCE_FILENAME)
        return TargetLayoutEvidence.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError) as exc:
        raise ReusableTemplateEvidenceError(
            f"artifact '{Path(directory).name}' keeps no readable "
            f"'{TARGET_EVIDENCE_FILENAME}' record, so no reuse identity can be "
            "computed for it; re-analyze the target",
            member="target_evidence",
        ) from exc
