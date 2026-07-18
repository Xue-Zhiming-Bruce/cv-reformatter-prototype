from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


BLUEPRINT_DIR = Path(__file__).resolve().parent.parent / "templates"


class SourceReferenceType(StrEnum):
    BUILT_IN = "built_in"
    DOCX = "docx"
    PDF_REFERENCE = "pdf_reference"


class BlueprintSection(StrEnum):
    HEADER = "header"
    CONTACT = "contact"
    SUMMARY = "summary"
    HIGHLIGHTS = "highlights"
    LANGUAGES = "languages"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    CERTIFICATIONS = "certifications"
    ADDITIONAL_DETAILS = "additional_details"
    SKILLS = "skills"


class BlueprintModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class PageGeometry(BlueprintModel):
    width_inches: float = Field(gt=0)
    height_inches: float = Field(gt=0)
    top_margin_inches: float = Field(ge=0)
    right_margin_inches: float = Field(ge=0)
    bottom_margin_inches: float = Field(ge=0)
    left_margin_inches: float = Field(ge=0)
    header_distance_inches: float = Field(ge=0)
    footer_distance_inches: float = Field(ge=0)


class TextStyle(BlueprintModel):
    font_name: str
    size_pt: float = Field(gt=0)
    bold: bool = False
    italic: bool = False
    color_hex: str = "000000"
    line_spacing: float = Field(gt=0)
    space_before_pt: float = Field(ge=0)
    space_after_pt: float = Field(ge=0)

    @field_validator("color_hex")
    @classmethod
    def validate_color_hex(cls, value: str) -> str:
        normalized = value.removeprefix("#").upper()
        if len(normalized) != 6 or any(character not in "0123456789ABCDEF" for character in normalized):
            raise ValueError("color_hex must contain exactly six hexadecimal characters")
        return normalized


class OverflowPolicy(BlueprintModel):
    allow_automatic_page_breaks: bool
    keep_experience_heading_with_first_bullet: bool
    keep_section_heading_with_next: bool
    widow_control: bool
    split_long_experience_entries: bool


class TargetFormatBlueprint(BlueprintModel):
    template_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    version: int = Field(ge=1)
    display_name: str
    source_reference_type: SourceReferenceType
    source_sha256: list[str] = Field(default_factory=list)
    renderer_key: Literal["apex_standard", "classic_10554236"]
    page: PageGeometry
    typography: dict[str, TextStyle]
    section_order: list[BlueprintSection]
    field_mappings: dict[str, str]
    skills_columns: int = Field(ge=1, le=4)
    bullet_left_indent_inches: float = Field(ge=0)
    bullet_hanging_indent_inches: float = Field(ge=0)
    overflow: OverflowPolicy

    @field_validator("source_sha256")
    @classmethod
    def validate_source_sha256(cls, values: list[str]) -> list[str]:
        for value in values:
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError("source_sha256 entries must be lowercase SHA-256 digests")
        return values

    @field_validator("typography")
    @classmethod
    def require_typography_roles(cls, value: dict[str, TextStyle]) -> dict[str, TextStyle]:
        required = {"normal", "title", "section", "entry_heading"}
        missing = required.difference(value)
        if missing:
            raise ValueError(f"typography is missing required roles: {', '.join(sorted(missing))}")
        return value


class UnknownTargetFormatBlueprintError(ValueError):
    """Raised when generation requests an unregistered template identifier."""


def load_target_format_blueprints(blueprint_dir: str | Path = BLUEPRINT_DIR) -> dict[str, TargetFormatBlueprint]:
    directory = Path(blueprint_dir)
    blueprints: dict[str, TargetFormatBlueprint] = {}
    for path in sorted(directory.glob("*/blueprint.json")):
        blueprint = TargetFormatBlueprint.model_validate_json(path.read_text(encoding="utf-8"))
        if blueprint.template_id in blueprints:
            raise ValueError(f"Duplicate target format blueprint: {blueprint.template_id}")
        blueprints[blueprint.template_id] = blueprint
    return blueprints


def get_target_format_blueprint(template_id: str) -> TargetFormatBlueprint:
    blueprints = load_target_format_blueprints()
    try:
        return blueprints[template_id]
    except KeyError as exc:
        raise UnknownTargetFormatBlueprintError(f"Unknown target format blueprint: {template_id}") from exc


def match_target_format_blueprint(
    contents: bytes,
    *,
    source_reference_type: SourceReferenceType,
) -> TargetFormatBlueprint | None:
    fingerprint = hashlib.sha256(contents).hexdigest()
    for blueprint in load_target_format_blueprints().values():
        if (
            blueprint.source_reference_type == source_reference_type
            and fingerprint in blueprint.source_sha256
        ):
            return blueprint
    return None
