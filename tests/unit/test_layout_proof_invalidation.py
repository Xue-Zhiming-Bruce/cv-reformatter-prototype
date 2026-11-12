"""Unit: a stored layout-proof approval is compared against its own scope."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main
from app.template_analysis.layout_proof_artifacts import layout_spec_checksum
from app.template_analysis.layout_proof_service import LayoutProofContextError
from app.template_analysis.schemas import built_in_layout_template_spec


def _approval(**overrides: object) -> SimpleNamespace:
    """The approval fields the scope comparison reads."""
    fields: dict[str, object] = {
        "artifact_id": "artifact-1",
        "design_id": "design-20261112",
        "layout_spec_checksum": "recorded-layout-checksum",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_a_design_approval_is_compared_against_the_designs_own_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6: the design's compiled spec is the scope, not the generation spec."""
    generation = _approval(design_id=None)
    assert main._approved_scope_layout_checksum(
        tmp_path, generation, "generation-checksum"
    ) == "generation-checksum"

    spec = built_in_layout_template_spec()
    monkeypatch.setattr(
        main,
        "resolve_layout_proof_context",
        lambda *args, **kwargs: (None, spec, Path("target.pdf"), "0" * 64),
    )
    assert main._approved_scope_layout_checksum(
        tmp_path, _approval(), "generation-checksum"
    ) == layout_spec_checksum(spec)


@pytest.mark.parametrize(
    "failure",
    [
        LayoutProofContextError("Design not found.", status_code=404),
        HTTPException(status_code=409, detail="no compiled spec"),
        OSError("the design scope cannot be read"),
        ValueError("the stored spec is not readable"),
    ],
)
def test_an_unresolvable_design_scope_is_unverifiable_not_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """AC6/AC4: reporting the recorded checksum never claims a spec change."""
    monkeypatch.setattr(
        main,
        "resolve_layout_proof_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )
    assert main._approved_scope_layout_checksum(
        tmp_path, _approval(), "generation-checksum"
    ) == "recorded-layout-checksum"
