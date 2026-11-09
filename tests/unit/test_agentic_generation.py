"""Generation-side reuse of an owner-approved presentation-label catalog.

An approved layout proof records the label identity its proof runs validated;
generation reuses it instead of demanding a second approval of the same
catalog, and only for the exact artifact scope, target, layout spec and catalog
identity that approval names.
"""

from __future__ import annotations

from app.generation.agentic import _proof_label_approval
from app.pipeline.service import PresentationLabelApproval
from app.template_analysis.layout_proof_schemas import (
    LayoutProofApproval,
    LengthVariant,
)

TARGET = "a" * 64
SPEC = "b" * 64
CATALOG = "c" * 64


def _catalog(sha256: str = CATALOG) -> dict[str, object]:
    return {
        "target_sha256": TARGET,
        "catalog_sha256": sha256,
        "labels": [],
        "unrecognized_template_ids": ["label.template.section.02.heading"],
    }


def _approval(
    *,
    state: str = "approved",
    artifact_id: str = "artifact-1",
    target_checksum: str = TARGET,
    layout_spec_checksum: str = SPEC,
    catalog_sha256: str | None = CATALOG,
    label_ids: list[str] | None = None,
) -> LayoutProofApproval:
    ids = ["label.template.section.02.heading"] if label_ids is None else label_ids
    return LayoutProofApproval.model_construct(
        artifact_id=artifact_id,
        target_checksum=target_checksum,
        layout_spec_checksum=layout_spec_checksum,
        state=state,
        presentation_labels=(
            [PresentationLabelApproval(
                target_sha256=target_checksum,
                catalog_sha256=catalog_sha256 or CATALOG,
                approved_label_ids=ids,
            )] if ids else []
        ),
        presentation_label_catalog_sha256=catalog_sha256,
    )


def _seed(approval: LayoutProofApproval, catalog: dict[str, object]):
    return _proof_label_approval(
        approval,
        catalog,
        target_checksum=TARGET,
        layout_spec_checksum=SPEC,
    )


def test_approved_proof_labels_seed_the_generation_approval() -> None:
    seeded = _seed(_approval(), _catalog())
    assert seeded is not None
    assert seeded.approved_label_ids == ["label.template.section.02.heading"]
    assert seeded.catalog_sha256 == CATALOG
    assert seeded.target_sha256 == TARGET


def test_stale_catalog_sha_does_not_seed_the_generation_approval() -> None:
    assert _seed(_approval(), _catalog(sha256="d" * 64)) is None


def test_approval_for_another_target_or_spec_does_not_seed_generation() -> None:
    assert _seed(_approval(target_checksum="d" * 64), _catalog()) is None
    assert _seed(_approval(layout_spec_checksum="d" * 64), _catalog()) is None


def test_pending_or_labelless_approval_does_not_seed_generation() -> None:
    assert _seed(_approval(state="pending"), _catalog()) is None
    assert _seed(_approval(label_ids=[]), _catalog()) is None
