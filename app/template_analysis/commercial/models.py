"""Strict provider-neutral evidence emitted by commercial layout analyzers."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MeasurementProvenance(EvidenceModel):
    source: Literal["provider", "local_pdf", "derived"]
    provider: str
    source_element_id: str | None = None
    method: str


class NormalizedBox(EvidenceModel):
    """Page-relative coordinates using a top-left origin and a 0..1 range."""

    x0: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self) -> "NormalizedBox":
        if self.x1 < self.x0 or self.bottom < self.top:
            raise ValueError("normalized box coordinates must be ordered")
        return self


class NormalizedPage(EvidenceModel):
    page_number: int = Field(ge=1)
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)


class NormalizedTextBlock(EvidenceModel):
    element_id: str = ""
    text: str
    page_number: int = Field(ge=1)
    reading_order: int = Field(default=0, ge=0)
    bbox: NormalizedBox | None = None
    structural_role: str = Field(
        default="other", validation_alias=AliasChoices("structural_role", "role")
    )
    font_family: str | None = None
    font_size_pt: float | None = Field(
        default=None, gt=0, validation_alias=AliasChoices("font_size_pt", "font_size")
    )
    bold: bool | None = None
    italic: bool | None = None
    color_hex: str | None = Field(
        default=None, validation_alias=AliasChoices("color_hex", "color")
    )
    baseline: float | None = Field(default=None, ge=0, le=1)
    line_height_pt: float | None = Field(
        default=None, gt=0,
        validation_alias=AliasChoices("line_height_pt", "line_height"),
    )
    spacing_before_pt: float | None = Field(default=None, ge=-72)
    spacing_after_pt: float | None = Field(default=None, ge=-72)
    text_align: str | None = None
    postscript_name: str | None = None
    char_bounds: list[NormalizedBox] = Field(default_factory=list)
    granularity: Literal["character", "line", "paragraph", "element"] = "line"
    typography_class: str | None = None
    provenance: dict[str, MeasurementProvenance] = Field(default_factory=dict)

    # Compatibility aliases used by the existing evaluation harness.
    @property
    def role(self) -> str:
        return self.structural_role

    @property
    def font_size(self) -> float | None:
        return self.font_size_pt

    @property
    def color(self) -> str | None:
        return self.color_hex

    @property
    def line_height(self) -> float | None:
        return self.line_height_pt


class NormalizedRule(EvidenceModel):
    """A long horizontal target separator measured from the local PDF."""

    element_id: str
    page_number: int = Field(ge=1)
    bbox: NormalizedBox
    stroke_width_pt: float = Field(ge=0.25, le=6.0)
    gap_above_pt: float | None = Field(default=None, ge=0, le=144)
    gap_below_pt: float | None = Field(default=None, ge=0, le=144)
    color_hex: str
    provenance: MeasurementProvenance


class NormalizedBadgeCluster(EvidenceModel):
    """Repeated short-text shapes measured from one target list region."""

    element_id: str
    page_number: int = Field(ge=1)
    bbox: NormalizedBox
    fill_color_hex: str
    text_color_hex: str
    height_pt: float = Field(ge=10, le=30)
    horizontal_padding_pt: float = Field(ge=0, le=72)
    items_per_line: list[int] = Field(min_length=1)
    badge_count: int = Field(ge=1)
    provenance: MeasurementProvenance

    @model_validator(mode="after")
    def grouping_matches_count(self) -> "NormalizedBadgeCluster":
        if any(count <= 0 for count in self.items_per_line):
            raise ValueError("badge line grouping values must be positive")
        if sum(self.items_per_line) != self.badge_count:
            raise ValueError("badge line grouping must sum to badge_count")
        return self


class NormalizedLayoutEvidence(EvidenceModel):
    schema_version: Literal["normalized-layout/1"] = "normalized-layout/1"
    provider: str
    provider_version: str | None = None
    pages: list[NormalizedPage] = Field(default_factory=list)
    page_count: int = Field(ge=0)
    full_text: str
    text_blocks: list[NormalizedTextBlock] = Field(default_factory=list)
    paragraphs: list[NormalizedTextBlock] = Field(default_factory=list)
    rules: list[NormalizedRule] = Field(default_factory=list)
    badge_clusters: list[NormalizedBadgeCluster] = Field(default_factory=list)
    table_count: int = Field(default=0, ge=0)
    figure_count: int = Field(default=0, ge=0)
    graphic_count: int = Field(default=0, ge=0)
    style_record_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def page_inventory_matches(self) -> "NormalizedLayoutEvidence":
        if self.pages and len(self.pages) != self.page_count:
            raise ValueError("page_count must match the normalized page inventory")
        return self
