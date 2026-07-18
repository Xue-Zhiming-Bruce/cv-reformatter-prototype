import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.generation.target_format_blueprint import (
    SourceReferenceType,
    TargetFormatBlueprint,
    UnknownTargetFormatBlueprintError,
    get_target_format_blueprint,
    load_target_format_blueprints,
    match_target_format_blueprint,
)


def test_registered_blueprints_are_strict_and_versioned() -> None:
    blueprints = load_target_format_blueprints()

    assert set(blueprints) == {"apex_standard", "client_10554236_v1"}
    classic = blueprints["client_10554236_v1"]
    assert classic.version == 1
    assert classic.source_reference_type == SourceReferenceType.PDF_REFERENCE
    assert classic.renderer_key == "classic_10554236"
    assert classic.skills_columns == 2
    assert classic.page.width_inches == pytest.approx(8.2639)


def test_blueprint_rejects_unknown_fields() -> None:
    payload = get_target_format_blueprint("apex_standard").model_dump()
    payload["unexpected"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TargetFormatBlueprint.model_validate(payload)


def test_unknown_blueprint_has_clear_error() -> None:
    with pytest.raises(UnknownTargetFormatBlueprintError, match="Unknown target format blueprint"):
        get_target_format_blueprint("not_registered")


def test_match_uses_source_type_and_sha256(tmp_path: Path) -> None:
    contents = b"synthetic target reference"
    blueprint = get_target_format_blueprint("client_10554236_v1").model_copy(
        update={"source_sha256": [hashlib.sha256(contents).hexdigest()]}
    )
    blueprint_dir = tmp_path / blueprint.template_id
    blueprint_dir.mkdir()
    (blueprint_dir / "blueprint.json").write_text(
        blueprint.model_dump_json(indent=2),
        encoding="utf-8",
    )

    matched = _match_from_directory(contents, tmp_path)

    assert matched is not None
    assert matched.template_id == "client_10554236_v1"
    assert match_target_format_blueprint(
        contents,
        source_reference_type=SourceReferenceType.DOCX,
    ) is None


def _match_from_directory(contents: bytes, blueprint_dir: Path) -> TargetFormatBlueprint | None:
    fingerprint = hashlib.sha256(contents).hexdigest()
    for blueprint in load_target_format_blueprints(blueprint_dir).values():
        if (
            blueprint.source_reference_type == SourceReferenceType.PDF_REFERENCE
            and fingerprint in blueprint.source_sha256
        ):
            return blueprint
    return None
