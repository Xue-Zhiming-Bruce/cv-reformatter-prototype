"""Reusable, versioned approved-template references (ADR 0009).

One explicitly approved layout-proof set can be selected by another artifact in
the same local scope instead of being re-derived. The identity assembled here is
the only key: every member of the ADR 0009 inventory must match, and a refusal
names the differing member(s) before any candidate render.

Boundaries: identity assembly, reference recording and selection resolution live
here; it never renders, never writes to a source artifact's proof directory,
approval row or publications, and stores no candidate fact, source text or review
edit. An approval recorded before this contract existed carries no reference and is
never treated as reusable: recording happens only on a new explicit approval event,
with no backfill and no silent upgrade. ``local-synthetic`` is a single-user local
development scope, not authenticated multi-tenant isolation. Stored evidence reads
live in ``template_reuse_evidence``.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

from app.template_analysis.design_schemas import (
    DESIGN_COMPILER_VERSION,
    TargetLayoutEvidence,
)
from app.template_analysis.layout_proof_artifacts import (
    file_sha256,
    layout_spec_checksum,
)
from app.template_analysis.layout_proof_rendering import active_renderer_version
from app.template_analysis.layout_proof_schemas import (
    LayoutContractIdentity,
    LayoutProofApproval,
    PresentationLabelIdentity,
    ProofArtifactIdentity,
    ProofContentIdentity,
    ProofSchemaVersions,
    ReusableTemplateIdentity,
    ReusableTemplateReference,
    TargetEvidenceIdentity,
)
from app.template_analysis.schemas import LayoutTemplateSpec, TemplateStyleSpec
from app.template_analysis.template_reuse_evidence import (
    ReusableTemplateEvidenceError,
    execution_identity,
    font_asset_identity,
    proven_layout_spec,
    source_approval,
    source_artifact_dir,
    spec_font_families,
    target_evidence,
)

#: The only scope a reference is recorded in or accepted from (ADR 0009).
REUSABLE_TEMPLATE_SCOPE = "local-synthetic"

class ReusableTemplateError(RuntimeError):
    """A selection that cannot be honoured before any candidate render.

    ``differing_members`` names every identity member that differs or is absent,
    and ``options`` are the existing ways forward. Never a silent fallback.
    """

    error_code = "reusable_template_mismatch"

    def __init__(
        self,
        message: str,
        *,
        differing_members: list[str],
        # The existing ways a recruiter can still prove a template for this target.
        options: tuple[str, ...] = ("create_layout_proofs", "approve_layout_proofs"),
    ) -> None:
        super().__init__(message)
        self.differing_members = list(differing_members)
        self.options = list(options)


class ReusableTemplateRepository(Protocol):
    def save(self, reference: ReusableTemplateReference) -> None: ...

    def resolve(
        self, *, scope_id: str, identity_sha256: str
    ) -> ReusableTemplateReference | None: ...

    def list_current(self, *, scope_id: str) -> list[ReusableTemplateReference]: ...


def reusable_template_identity(
    *,
    scope_id: str,
    artifact_dir: str | Path,
    approval: LayoutProofApproval,
    target_evidence: TargetLayoutEvidence,
    execution_identity: dict[str, Any],
    compiler_version: str,
) -> ReusableTemplateIdentity:
    """Members 1-13 of the ADR 0009 identity for one approved proof set."""
    _require_reusable(scope_id, approval)
    with _as_refusal():
        spec = proven_layout_spec(artifact_dir, approval)
        fonts = font_asset_identity(spec, approval)
    return ReusableTemplateIdentity(
        scope_id=scope_id,
        target_pdf_sha256=approval.target_checksum,
        target_evidence=TargetEvidenceIdentity.of(target_evidence),
        layout_spec_sha256=approval.layout_spec_checksum,
        layout_contract=LayoutContractIdentity.of(spec),
        compiler_version=compiler_version,
        renderer_version=approval.renderer_version,
        execution_identity=execution_identity,
        proof_content=ProofContentIdentity.of(approval),
        proof_schema_versions=ProofSchemaVersions(),
        proof_artifacts=ProofArtifactIdentity.of(approval),
        font_asset_identity=fonts,
        presentation_labels=PresentationLabelIdentity.of(approval),
    )


def _require_reusable(scope_id: str, approval: LayoutProofApproval) -> None:
    """One recorded scope, and member 14 in state ``approved``."""
    if scope_id != REUSABLE_TEMPLATE_SCOPE:
        raise ReusableTemplateError(
            f"'{scope_id}' is not a reusable-template scope; only "
            f"'{REUSABLE_TEMPLATE_SCOPE}' records references",
            differing_members=["scope_id"],
        )
    if not approval.is_approved:
        raise ReusableTemplateError(
            f"approval {approval.approval_id} is '{approval.state}', not 'approved'",
            differing_members=["approval_id"],
        )


@contextmanager
def _as_refusal() -> Iterator[None]:
    """Report a missing stored record as the identity member it stood for."""
    try:
        yield
    except ReusableTemplateEvidenceError as exc:
        raise ReusableTemplateError(
            str(exc), differing_members=[exc.member]
        ) from exc


def record_reusable_template_reference(
    *,
    artifact_dir: str | Path,
    artifact_id: str,
    approval: LayoutProofApproval,
    repository: ReusableTemplateRepository,
) -> ReusableTemplateReference | None:
    """Record the reuse identity of a newly approved proof set.

    Returns ``None`` when the artifact cannot supply the evidence the identity
    needs: such an approval stays artifact-local and is simply not selectable,
    rather than being upgraded later by inference.
    """
    try:
        identity = recorded_identity(artifact_dir, approval)
    except ReusableTemplateError:
        return None
    reference = ReusableTemplateReference(
        reference_id=identity.identity_sha256,
        scope_id=REUSABLE_TEMPLATE_SCOPE,
        identity=identity,
        artifact_id=artifact_id,
        approval_id=approval.approval_id,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    repository.save(reference)
    return reference


def recorded_identity(
    artifact_dir: str | Path, approval: LayoutProofApproval
) -> ReusableTemplateIdentity:
    """The identity this artifact's own stored evidence produces."""
    with _as_refusal():
        evidence = target_evidence(artifact_dir)
        runs = execution_identity(artifact_dir, approval)
    return reusable_template_identity(
        scope_id=REUSABLE_TEMPLATE_SCOPE,
        artifact_dir=artifact_dir,
        approval=approval,
        target_evidence=evidence,
        execution_identity=runs,
        compiler_version=DESIGN_COMPILER_VERSION,
    )


def resolve_reusable_template(
    *,
    artifact_dir: str | Path,
    artifact_id: str,
    reference_id: str,
    repository: ReusableTemplateRepository,
    target_pdf: str | Path,
    layout_spec: LayoutTemplateSpec | TemplateStyleSpec,
) -> LayoutProofApproval:
    """Resolve an explicit selection to its approved proof set, or refuse.

    The selecting artifact's own target and template are compared first, then the
    source's stored evidence is re-derived, so a changed target, spec, compiler,
    renderer, policy, prompt set, model routing, budget or proof file refuses
    before any candidate render. Nothing is copied and nothing is written.
    """
    reference = stored_reference(repository, reference_id)
    differing = selecting_differences(reference, target_pdf, layout_spec)
    if differing:
        raise _refusal(artifact_id, differing)
    with _as_refusal():
        source_dir = source_artifact_dir(artifact_dir, reference.artifact_id)
        approval = source_approval(source_dir, reference, repository)
    differing = source_differences(reference, source_dir, approval)
    if differing:
        raise _refusal(artifact_id, differing)
    return approval


def _refusal(artifact_id: str, differing: list[str]) -> ReusableTemplateError:
    return ReusableTemplateError(
        f"artifact '{artifact_id}' cannot reuse the selected template: "
        + ", ".join(differing) + " differs. Create and approve fresh short/medium/long "
        "proofs for this target, or select the reference recorded for it.",
        differing_members=differing,
    )


def require_reference_store(
    repository: ReusableTemplateRepository | None, reference_id: str
) -> ReusableTemplateRepository:
    """A selection needs the recorded reference table; without it, none exists."""
    if repository is None:
        raise unknown_reference(reference_id, "this deployment stores none")
    return repository


def unknown_reference(reference_id: str, reason: str) -> ReusableTemplateError:
    return ReusableTemplateError(
        f"no reusable template reference '{reference_id}' is recorded in the "
        f"'{REUSABLE_TEMPLATE_SCOPE}' scope ({reason}); create and approve a proof "
        "set, or generate without a selection", differing_members=["reference_id"]
    )


def stored_reference(
    repository: ReusableTemplateRepository, reference_id: str
) -> ReusableTemplateReference:
    """The recorded reference for this id, in this artifact's own scope."""
    reference = repository.resolve(
        scope_id=REUSABLE_TEMPLATE_SCOPE, identity_sha256=reference_id
    )
    if reference is None:
        raise unknown_reference(reference_id, "none matches")
    if reference.scope_id != REUSABLE_TEMPLATE_SCOPE:
        raise ReusableTemplateError(
            f"reference scope '{reference.scope_id}' is not '{REUSABLE_TEMPLATE_SCOPE}'",
            differing_members=["scope_id"],
        )
    return reference


def selecting_differences(
    reference: ReusableTemplateReference,
    target_pdf: str | Path,
    layout_spec: LayoutTemplateSpec | TemplateStyleSpec,
) -> list[str]:
    """Members the selecting artifact attests before any render."""
    stored = reference.identity.members()
    with _as_refusal():
        evidence = target_evidence(Path(target_pdf).parent)
    attested = {
        "scope_id": REUSABLE_TEMPLATE_SCOPE,
        "target_pdf_sha256": file_sha256(target_pdf),
        "target_evidence": TargetEvidenceIdentity.of(evidence).model_dump(mode="json"),
        "layout_spec_sha256": layout_spec_checksum(layout_spec),
        "layout_contract": LayoutContractIdentity.of(layout_spec).model_dump(mode="json"),
        "compiler_version": DESIGN_COMPILER_VERSION,
        "renderer_version": active_renderer_version(),
    }
    differing = [name for name, value in attested.items() if stored[name] != value]
    if stored["font_asset_identity"]["template_font_families"] != spec_font_families(layout_spec):
        differing.append("font_asset_identity")
    return sorted(differing)


def source_differences(
    reference: ReusableTemplateReference,
    source_dir: Path,
    approval: LayoutProofApproval,
) -> list[str]:
    """Members re-derived from the source artifact's stored evidence."""
    identity = recorded_identity(source_dir, approval)
    stored = reference.identity.members()
    return sorted(name for name, value in identity.members().items() if stored[name] != value)
