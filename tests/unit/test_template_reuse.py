"""Reusable template references: identity members, refusals and immutability.

ADR 0009 fixes the reuse key at 13 hashed members plus the referenced approval.
These cases prove each member is really in the key (one differing member is
refused and named on its own), that a selection resolves to the source
artifact's approval, that a legacy or limited-capability template is never
reusable, and that recording or using a reference writes nothing to the source
artifact (ADR 0009 rule 7, issue #37 AC4/AC6/AC7/AC8).
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from app.target_publication import TargetSet, publish_target_set
from app.template_analysis import layout_proof_rendering
from app.template_analysis.artifacts import TARGET_EVIDENCE_FILENAME
from app.template_analysis.design_schemas import TargetLayoutEvidence
from app.template_analysis.layout_proof_artifacts import (
    approval_path_for,
    file_sha256,
    layout_proofs_dir_for,
)
from app.template_analysis.layout_proof_schemas import (
    REUSABLE_TEMPLATE_IDENTITY_MEMBERS,
    LayoutProofApproval,
    ReusableTemplateIdentity,
    ReusableTemplateReference,
)
from app.template_analysis.layout_proof_service import (
    approve_layout_proof,
    create_proof_variants,
)
from app.template_analysis.schemas import built_in_layout_template_spec
from app.template_analysis.template_reuse import (
    REUSABLE_TEMPLATE_SCOPE,
    ReusableTemplateError,
    record_reusable_template_reference,
    resolve_reusable_template,
)
from app.template_analysis.template_reuse_evidence import (
    ReusableTemplateEvidenceError,
    proven_layout_spec,
)
from tests.helpers.layout_proof import make_proof_pdf_exporter
from tests.helpers.synthetic_pdf import build_styled_target_pdf


pytestmark = pytest.mark.usefixtures("a_generation_without_model_loop")

SOURCE_ID = "artifact_source"


class MemoryTemplateRepository:
    """The repository seam in memory; the PostgreSQL table has its own cases."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], ReusableTemplateReference] = {}

    def save(self, reference: ReusableTemplateReference) -> None:
        self.rows.setdefault((reference.scope_id, reference.reference_id), reference)

    def resolve(
        self, *, scope_id: str, identity_sha256: str
    ) -> ReusableTemplateReference | None:
        return self.rows.get((scope_id, identity_sha256))

    def list_current(self, *, scope_id: str) -> list[ReusableTemplateReference]:
        return [row for (scope, _), row in self.rows.items() if scope == scope_id]


@pytest.fixture()
def fake_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.generation.chrome_html_to_pdf.export_html_to_pdf", make_proof_pdf_exporter()
    )


def _target_evidence(artifact_dir: Path, target_pdf: Path) -> None:
    checksum = file_sha256(target_pdf)
    (artifact_dir / TARGET_EVIDENCE_FILENAME).write_text(
        TargetLayoutEvidence(
            evidence_version="normalized-layout/1",
            target_checksum=checksum,
            target_format="pdf",
            page_count=1,
            text_block_count=3,
        ).model_dump_json(),
        encoding="utf-8",
    )


@pytest.fixture()
def source(tmp_path: Path, fake_pdf: None) -> dict[str, object]:
    """An artifact with approved short/medium/long proofs and a recorded reference."""
    artifact_dir = tmp_path / SOURCE_ID
    artifact_dir.mkdir()
    target_pdf = build_styled_target_pdf(artifact_dir / "target.pdf")
    _target_evidence(artifact_dir, target_pdf)
    spec = built_in_layout_template_spec()
    checksum = file_sha256(target_pdf)
    create_proof_variants(
        artifact_dir=artifact_dir,
        artifact_id=SOURCE_ID,
        layout_spec=spec,
        target_pdf=target_pdf,
        target_checksum=checksum,
    )
    approval = approve_layout_proof(
        artifact_dir=artifact_dir,
        artifact_id=SOURCE_ID,
        target_checksum=checksum,
        layout_spec=spec,
        target_pdf=target_pdf,
        reviewer_id="reviewer-1",
    )
    repository = MemoryTemplateRepository()
    reference = record_reusable_template_reference(
        artifact_dir=artifact_dir,
        artifact_id=SOURCE_ID,
        approval=approval,
        repository=repository,
    )
    assert reference is not None
    return {
        "dir": artifact_dir,
        "target": target_pdf,
        "spec": spec,
        "approval": approval,
        "reference": reference,
        "repository": repository,
    }


def _selecting_artifact(tmp_path: Path, source: dict[str, object]) -> tuple[Path, Path]:
    """A second candidate artifact with the same target bytes and template."""
    artifact_dir = tmp_path / "artifact_second"
    artifact_dir.mkdir()
    target_pdf = build_styled_target_pdf(artifact_dir / "target.pdf")
    _target_evidence(artifact_dir, target_pdf)
    return artifact_dir, target_pdf


def _published_selecting_artifact(tmp_path: Path) -> tuple[Path, Path]:
    """A second artifact whose target set is published under ``.targets``, as an upload leaves it.

    The returned target PDF is the published one a caller pins its generation
    from: a file inside ``<artifact>/.targets/set-...``, not the artifact root.
    """
    artifact_dir = tmp_path / "artifact_second"
    staging = tmp_path / ".staging_second"
    staging.mkdir()
    target_pdf = build_styled_target_pdf(staging / "target_format_reference.pdf")
    _target_evidence(staging, target_pdf)
    publish_target_set(
        artifact_dir,
        staging,
        ("target_format_reference.pdf", TARGET_EVIDENCE_FILENAME),
        threading.Event(),
    )
    pinned = TargetSet(artifact_dir).read("target_format_reference.pdf")
    assert pinned is not None
    return artifact_dir, pinned


# Every member, with a value that is deliberately not the recorded one. The
# order is the documented identity order, so a missing entry is a missing test.
WRONG_MEMBER_VALUES: dict[str, object] = {
    "scope_id": "tenant-2",
    "target_pdf_sha256": "0" * 64,
    "target_evidence": {
        "schema_version": "layout/evidence/1",
        "evidence_version": "normalized-layout/0",
        "evidence_checksum": "0" * 64,
    },
    "layout_spec_sha256": "1" * 64,
    "layout_contract": {
        "schema_version": "2.0",
        "template_version": "99",
        "structure_contract": "measured",
    },
    "compiler_version": "0",
    "renderer_version": "agentic-a/0",
    "execution_identity": {"mode": "offline-renderer/1", "frozen": None},
    "proof_content": {
        "schema_version": "layout/proof/context/0",
        "context_checksums": {"short": "2" * 64, "medium": "2" * 64, "long": "2" * 64},
    },
    "proof_schema_versions": {
        "approval": "layout/proof/approval/1",
        "variant": "layout/proof/variant/1",
        "render_result": "layout/proof/render/result/4",
        "structural": "layout/proof/structural/1",
        "visual": "layout/proof/visual/1",
    },
    "proof_artifacts": {
        "short": {
            "proof_id": "layout_proof_short_other",
            "html_sha256": "3" * 64,
            "pdf_sha256": "3" * 64,
            "page_count": 1,
        },
        "medium": {
            "proof_id": "layout_proof_medium_other",
            "html_sha256": "4" * 64,
            "pdf_sha256": "4" * 64,
            "page_count": 2,
        },
        "long": {
            "proof_id": "layout_proof_long_other",
            "html_sha256": "5" * 64,
            "pdf_sha256": "5" * 64,
            "page_count": 3,
        },
    },
    "font_asset_identity": {
        "template_font_families": ["Times New Roman"],
        "proof_rendering_environment": {"fallback_fonts": ["Times New Roman"]},
    },
    "presentation_labels": {"catalog_sha256": None, "label_ids": ["label_other"]},
}


def _identity_with(identity: ReusableTemplateIdentity, member: str) -> ReusableTemplateIdentity:
    payload = identity.model_dump(mode="json")
    payload[member] = WRONG_MEMBER_VALUES[member]
    return ReusableTemplateIdentity.model_validate(payload)


def _stored_reference(
    source: dict[str, object], identity: ReusableTemplateIdentity
) -> ReusableTemplateReference:
    return ReusableTemplateReference(
        reference_id=identity.identity_sha256,
        scope_id=identity.scope_id,
        identity=identity,
        artifact_id=SOURCE_ID,
        approval_id=source["approval"].approval_id,
        created_at="2026-11-17T00:00:00+00:00",
    )


def test_identity_carries_the_thirteen_documented_members(source: dict[str, object]) -> None:
    identity = source["reference"].identity
    assert tuple(identity.members()) == REUSABLE_TEMPLATE_IDENTITY_MEMBERS
    assert len(REUSABLE_TEMPLATE_IDENTITY_MEMBERS) == 13
    assert identity.scope_id == REUSABLE_TEMPLATE_SCOPE
    assert set(WRONG_MEMBER_VALUES) == set(REUSABLE_TEMPLATE_IDENTITY_MEMBERS)


def test_identity_digest_covers_every_member(source: dict[str, object]) -> None:
    identity = source["reference"].identity
    digests = {
        member: _identity_with(identity, member).identity_sha256
        for member in REUSABLE_TEMPLATE_IDENTITY_MEMBERS
    }
    assert identity.identity_sha256 not in digests.values()
    assert len(set(digests.values())) == 13


def test_a_second_artifact_resolves_the_recorded_approval(
    tmp_path: Path, source: dict[str, object]
) -> None:
    artifact_dir, target_pdf = _selecting_artifact(tmp_path, source)
    resolved = resolve_reusable_template(
        artifact_dir=artifact_dir,
        artifact_id="artifact_second",
        reference_id=source["reference"].reference_id,
        repository=source["repository"],
        target_pdf=target_pdf,
        layout_spec=source["spec"],
    )
    assert resolved.approval_id == source["approval"].approval_id
    assert resolved.artifact_id == SOURCE_ID


@pytest.mark.parametrize("member", list(REUSABLE_TEMPLATE_IDENTITY_MEMBERS))
def test_each_single_member_mismatch_is_refused_and_named(
    tmp_path: Path, source: dict[str, object], member: str
) -> None:
    artifact_dir, target_pdf = _selecting_artifact(tmp_path, source)
    mutated = _stored_reference(source, _identity_with(source["reference"].identity, member))
    source["repository"].rows[(mutated.scope_id, mutated.reference_id)] = mutated
    with pytest.raises(ReusableTemplateError) as refusal:
        resolve_reusable_template(
            artifact_dir=artifact_dir,
            artifact_id="artifact_second",
            reference_id=mutated.reference_id,
            repository=source["repository"],
            target_pdf=target_pdf,
            layout_spec=source["spec"],
        )
    # A reference recorded in another scope is simply not this scope's reference.
    expected = ["reference_id"] if member == "scope_id" else [member]
    assert refusal.value.differing_members == expected
    assert refusal.value.error_code == "reusable_template_mismatch"


def test_matching_identity_in_another_scope_is_a_different_reference(
    tmp_path: Path, source: dict[str, object]
) -> None:
    artifact_dir, target_pdf = _selecting_artifact(tmp_path, source)
    identity = source["reference"].identity.model_copy(update={"scope_id": "tenant-2"})
    other = _stored_reference(source, identity)
    source["repository"].rows[("tenant-2", other.reference_id)] = other
    with pytest.raises(ReusableTemplateError) as refusal:
        resolve_reusable_template(
            artifact_dir=artifact_dir,
            artifact_id="artifact_second",
            reference_id=other.reference_id,
            repository=source["repository"],
            target_pdf=target_pdf,
            layout_spec=source["spec"],
        )
    assert refusal.value.differing_members == ["reference_id"]


def test_a_store_that_answers_another_scope_is_refused_by_scope(
    tmp_path: Path, source: dict[str, object]
) -> None:
    class ScopeIgnoringRepository(MemoryTemplateRepository):
        def resolve(self, *, scope_id: str, identity_sha256: str):
            return self.rows.get(("tenant-2", identity_sha256))

    artifact_dir, target_pdf = _selecting_artifact(tmp_path, source)
    identity = source["reference"].identity.model_copy(update={"scope_id": "tenant-2"})
    other = _stored_reference(source, identity)
    repository = ScopeIgnoringRepository()
    repository.rows[("tenant-2", other.reference_id)] = other
    with pytest.raises(ReusableTemplateError) as refusal:
        resolve_reusable_template(
            artifact_dir=artifact_dir,
            artifact_id="artifact_second",
            reference_id=other.reference_id,
            repository=repository,
            target_pdf=target_pdf,
            layout_spec=source["spec"],
        )
    assert refusal.value.differing_members == ["scope_id"]


def test_unknown_reference_is_refused_with_the_existing_options(
    tmp_path: Path, source: dict[str, object]
) -> None:
    artifact_dir, target_pdf = _selecting_artifact(tmp_path, source)
    with pytest.raises(ReusableTemplateError) as refusal:
        resolve_reusable_template(
            artifact_dir=artifact_dir,
            artifact_id="artifact_second",
            reference_id="0" * 64,
            repository=source["repository"],
            target_pdf=target_pdf,
            layout_spec=source["spec"],
        )
    assert refusal.value.differing_members == ["reference_id"]
    assert refusal.value.options == ["create_layout_proofs", "approve_layout_proofs"]


@pytest.mark.parametrize("contract", ["legacy", "limited_capability"])
def test_legacy_or_limited_capability_template_is_never_reusable(
    contract: str, source: dict[str, object]
) -> None:
    spec = source["spec"].model_copy(update={"structure_contract": contract})
    artifact_dir = source["dir"]
    for evidence in source["approval"].variants.values():
        proof_dir = layout_proofs_dir_for(artifact_dir) / evidence.render_result.proof_id
        (proof_dir / "layout_template_spec.json").write_text(
            spec.model_dump_json(), encoding="utf-8"
        )
    with pytest.raises(ReusableTemplateEvidenceError) as refusal:
        proven_layout_spec(artifact_dir, source["approval"])
    assert refusal.value.member == "layout_contract"


def test_a_rejected_approval_never_records_a_reference(tmp_path: Path) -> None:
    repository = MemoryTemplateRepository()
    approval = LayoutProofApproval.model_construct(
        approval_id="approval_rejected",
        artifact_id=SOURCE_ID,
        target_checksum="6" * 64,
        layout_spec_checksum="6" * 64,
        renderer_version=layout_proof_rendering.active_renderer_version(),
        state="rejected",
        variants={},
    )
    recorded = record_reusable_template_reference(
        artifact_dir=tmp_path,
        artifact_id=SOURCE_ID,
        approval=approval,
        repository=repository,
    )
    assert recorded is None
    assert repository.rows == {}


def _tree_checksums(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _tree_entries(directory: Path) -> dict[str, str]:
    """Every entry under ``directory``: a link's target, a file's hash, a directory."""
    entries: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            entries[relative] = f"-> {path.readlink()}"
        elif path.is_file():
            entries[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            entries[relative] = "<dir>"
    return entries


def test_recording_and_using_a_reference_leaves_the_source_artifact_unchanged(
    tmp_path: Path, source: dict[str, object]
) -> None:
    artifact_dir = source["dir"]
    before = _tree_checksums(artifact_dir)
    before_approval = json.loads(approval_path_for(artifact_dir).read_text(encoding="utf-8"))
    selecting_dir, target_pdf = _selecting_artifact(tmp_path, source)
    record_reusable_template_reference(
        artifact_dir=artifact_dir,
        artifact_id=SOURCE_ID,
        approval=source["approval"],
        repository=source["repository"],
    )
    resolve_reusable_template(
        artifact_dir=selecting_dir,
        artifact_id="artifact_second",
        reference_id=source["reference"].reference_id,
        repository=source["repository"],
        target_pdf=target_pdf,
        layout_spec=source["spec"],
    )
    assert _tree_checksums(artifact_dir) == before
    assert json.loads(approval_path_for(artifact_dir).read_text(encoding="utf-8")) == before_approval
    assert _tree_checksums(selecting_dir) != before


def test_a_resolution_leaves_the_selecting_artifacts_published_set_unchanged(
    tmp_path: Path, source: dict[str, object]
) -> None:
    """A resolution reads the pinned set; it does not publish a set inside it.

    At 94d2a1c the selection handed the pinned epoch to ``TargetSet`` as an
    artifact root, so the read adopted the epoch as a legacy artifact: its
    published files moved into ``<epoch>/.targets/set-...`` and were replaced by
    symlinks, one more level per generation.
    """
    artifact_dir, target_pdf = _published_selecting_artifact(tmp_path)
    before = _tree_entries(artifact_dir)
    resolved = resolve_reusable_template(
        artifact_dir=artifact_dir,
        artifact_id="artifact_second",
        reference_id=source["reference"].reference_id,
        repository=source["repository"],
        target_pdf=target_pdf,
        layout_spec=source["spec"],
    )
    assert resolved.approval_id == source["approval"].approval_id
    assert _tree_entries(artifact_dir) == before


def test_recorded_reference_stores_no_candidate_content(source: dict[str, object]) -> None:
    stored = source["reference"].model_dump(mode="json")
    text = json.dumps(stored)
    assert "proof" in text  # the reference points at proof identities...
    assert "candidate" not in text.lower()
    assert set(stored) == {
        "schema_version",
        "reference_id",
        "scope_id",
        "identity",
        "artifact_id",
        "approval_id",
        "created_at",
    }
