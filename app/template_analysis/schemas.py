from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


HEX_COLOR_PATTERN = r"^#[0-9A-F]{6}$"


class AnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BoundingBox(AnalysisModel):
    x0: float
    top: float
    x1: float
    bottom: float


class TextBlock(AnalysisModel):
    text: str
    bbox: BoundingBox
    font_family: str
    font_size_pt: float = Field(gt=0)
    bold: bool = False
    italic: bool = False
    color_hex: str = Field(default="#000000", pattern=HEX_COLOR_PATTERN)
    role: Literal["title", "heading", "body"] = "body"


class GraphicElement(AnalysisModel):
    kind: Literal["line", "rectangle", "curve", "image"]
    bbox: BoundingBox
    stroke_color_hex: str | None = Field(default=None, pattern=HEX_COLOR_PATTERN)
    fill_color_hex: str | None = Field(default=None, pattern=HEX_COLOR_PATTERN)


class PageLayout(AnalysisModel):
    page_number: int = Field(ge=1)
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    text_blocks: list[TextBlock] = Field(default_factory=list)
    graphics: list[GraphicElement] = Field(default_factory=list)


class PageStyle(AnalysisModel):
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    orientation: Literal["portrait", "landscape"]
    margin_top_pt: float = Field(ge=0)
    margin_right_pt: float = Field(ge=0)
    margin_bottom_pt: float = Field(ge=0)
    margin_left_pt: float = Field(ge=0)


class ColumnStyle(AnalysisModel):
    count: Literal[1, 2] = 1
    gutter_pt: float = Field(default=18.0, ge=0)
    left_column_ratio: float = Field(default=0.68, gt=0.2, lt=0.8)
    sidebar_side: Literal["left", "right"] | None = None


class TextStyle(AnalysisModel):
    font_family: str = "Arial"
    font_postscript_name: str | None = None
    font_size_pt: float = Field(default=10.0, gt=0)
    bold: bool = False
    italic: bool = False
    color_hex: str = Field(default="#000000", pattern=HEX_COLOR_PATTERN)
    character_spacing_pt: float = Field(default=0.0, ge=-5.0, le=20.0)
    word_spacing_pt: float = Field(default=0.0, ge=-5.0, le=30.0)
    horizontal_scale_percent: float = Field(default=100.0, ge=50.0, le=200.0)
    line_height_pt: float | None = Field(default=None, gt=0, le=72)


class SpacingStyle(AnalysisModel):
    line_spacing: float = Field(default=1.1, ge=0.8, le=2.0)
    line_height_pt: float | None = Field(default=None, gt=0, le=72)
    paragraph_after_pt: float = Field(default=4.0, ge=0, le=36)
    section_before_pt: float = Field(default=10.0, ge=0, le=72)
    section_after_pt: float = Field(default=4.0, ge=0, le=36)


class DecorationStyle(AnalysisModel):
    primary_color_hex: str = Field(default="#203452", pattern=HEX_COLOR_PATTERN)
    accent_color_hex: str = Field(default="#203452", pattern=HEX_COLOR_PATTERN)
    rule_color_hex: str = Field(default="#D9DEE7", pattern=HEX_COLOR_PATTERN)
    sidebar_background_hex: str | None = Field(default=None, pattern=HEX_COLOR_PATTERN)
    header_rule: bool = False
    heading_rule: bool = False
    rule_width_pt: float = Field(default=0.75, ge=0.25, le=6.0)
    header_rule_gap_above_pt: float | None = Field(default=None, ge=0, le=144)
    header_rule_gap_below_pt: float | None = Field(default=None, ge=0, le=144)
    header_rule_length_pt: float | None = Field(default=None, gt=0, le=2000)
    heading_rule_gap_above_pt: float | None = Field(default=None, ge=0, le=144)
    heading_rule_gap_below_pt: float | None = Field(default=None, ge=0, le=144)
    heading_rule_length_pt: float | None = Field(default=None, gt=0, le=2000)


class BadgeStyle(AnalysisModel):
    """Renderer-neutral presentation for short items in repeated filled shapes."""

    badge_fill_hex: str = Field(pattern=HEX_COLOR_PATTERN)
    badge_text_hex: str = Field(pattern=HEX_COLOR_PATTERN)
    badge_height_pt: float = Field(ge=10, le=30)
    badge_horizontal_padding_pt: float = Field(default=0, ge=0, le=72)
    badge_items_per_line: list[int] = Field(min_length=1)
    badge_border_hex: str | None = Field(default=None, pattern=HEX_COLOR_PATTERN)

    @model_validator(mode="after")
    def positive_line_grouping(self) -> "BadgeStyle":
        if any(count <= 0 for count in self.badge_items_per_line):
            raise ValueError("badge line grouping values must be positive")
        return self


class ListStyle(AnalysisModel):
    skills_layout: Literal["bullets", "stacked", "inline", "two_column_bullets"] = "bullets"
    languages_layout: Literal["bullets", "stacked", "inline", "two_column_bullets"] = "bullets"
    certifications_layout: Literal["bullets", "stacked", "inline", "two_column_bullets"] = "bullets"
    inline_separator: str = "|"


class SectionLabels(AnalysisModel):
    contact: str = "Contact"
    summary: str = "Professional Summary"
    skills: str = "Skills"
    languages: str = "Languages"
    work_experience: str = "Work Experience"
    education: str = "Education"
    certifications: str = "Certifications"
    additional_details: str = "Additional Details"


class HeaderLayoutStyle(AnalysisModel):
    show_candidate_subheading: bool = True
    contact_fields: list[
        Literal["email", "phone", "location", "linkedin_url", "portfolio_url"]
    ] = Field(default_factory=list)
    contact_separator: str = " | "
    contact_style: TextStyle | None = None
    contact_line_height_pt: float | None = Field(default=None, gt=0, le=72)
    name_gap_pt: float | None = Field(default=None, ge=0, le=72)


class TemplateStyleSpec(AnalysisModel):
    schema_version: Literal["1.0"] = "1.0"
    source_type: Literal["built_in", "pdf_analysis", "docx_analysis"] = "pdf_analysis"
    template_name: str = "analyzed_pdf"
    page: PageStyle
    columns: ColumnStyle = Field(default_factory=ColumnStyle)
    body: TextStyle = Field(default_factory=TextStyle)
    title: TextStyle = Field(
        default_factory=lambda: TextStyle(
            font_family="Arial",
            font_size_pt=20.0,
            bold=True,
            color_hex="#203452",
        )
    )
    heading: TextStyle = Field(
        default_factory=lambda: TextStyle(
            font_family="Arial",
            font_size_pt=12.0,
            bold=True,
            color_hex="#203452",
        )
    )
    spacing: SpacingStyle = Field(default_factory=SpacingStyle)
    decoration: DecorationStyle = Field(default_factory=DecorationStyle)
    lists: ListStyle = Field(default_factory=ListStyle)
    # Target analyzers must not manufacture the historical English label set.
    # Built-in and genuinely legacy specs may still carry it explicitly.
    section_labels: SectionLabels | None = None
    header: HeaderLayoutStyle = Field(default_factory=HeaderLayoutStyle)
    show_document_title: bool = False
    confidence: float = Field(default=0.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_target_analysis_default_fill(self) -> "TemplateStyleSpec":
        """Forbid the legacy fallback signature on target-derived styles.

        Individual measured values may legitimately be Arial, 10pt, or black;
        only the combined historical default-fill signature is rejected.
        This validator is inherited by ``LayoutTemplateSpec``.
        """
        if self.source_type == "built_in":
            if self.section_labels is None:
                self.section_labels = SectionLabels()
            return self
        for role_name in ("body", "title", "heading"):
            role = getattr(self, role_name)
            if (
                role.font_family.strip().lower() == "arial"
                and role.font_size_pt == 10.0
                and role.color_hex.upper() == "#000000"
            ):
                raise ValueError(
                    f"target-derived {role_name} typography carries the banned "
                    "Arial/10pt/#000000 default-fill signature"
                )
        return self


class SectionLayoutSpec(AnalysisModel):
    """Provider-neutral mapping from one candidate data source to one flowing block.

    ``placement`` is the semantic placement compiled from validated design
    decisions (full width, main column, sidebar, or page header/footer). It
    is provider-neutral: renderer-specific geometry is never stored here.
    """

    section_id: str
    source: Literal[
        "contact",
        "summary",
        "skills",
        "languages",
        "work_experience",
        "education",
        "certifications",
        "additional_details",
    ]
    label: str
    layout: Literal[
        "full_width",
        "bullets",
        "stacked",
        "inline",
        "two_column_bullets",
    ] = "full_width"
    placement: Literal[
        "full_width",
        "main_column",
        "sidebar",
        "header",
        "footer",
    ] = "full_width"
    optional: bool = True
    column_count: Literal[1, 2] = 1
    column_gap_pt: float = Field(default=18.0, ge=0, le=144)
    bullet_indent_pt: float = Field(default=18.0, ge=0, le=72)
    heading_style: TextStyle | None = None
    body_style: TextStyle | None = None
    spacing: SpacingStyle | None = None
    entry_title_bold: bool | None = None
    entry_title_style: TextStyle | None = None
    entry_metadata_style: TextStyle | None = None
    entry_secondary_style: TextStyle | None = None
    skill_label_style: TextStyle | None = None
    education_institution_style: TextStyle | None = None
    inline_separator: str = ", "
    education_primary_separator: str = " - "
    education_secondary_separator: str = ", "
    entry_gap_pt: float | None = Field(default=None, ge=0, le=72)
    entry_title_to_metadata_gap_pt: float | None = Field(default=None, ge=0, le=72)
    skill_group_gap_pt: float | None = Field(default=None, ge=0, le=72)
    skill_first_row_adjustment_pt: float | None = Field(default=None, ge=-72, le=72)
    split_entry_rows: bool = False
    page_break_before: bool = False
    show_heading: bool = True
    contact_fields: list[
        Literal["email", "phone", "location", "linkedin_url", "portfolio_url"]
    ] = Field(default_factory=list)
    additional_section_heading: str | None = None
    mapping_action: Literal["map", "preserve_as_additional"] = "map"
    badge_style: BadgeStyle | None = None


class LayoutTemplateSpec(TemplateStyleSpec):
    """Versioned flow-aware successor to the MVP ``TemplateStyleSpec``."""

    schema_version: Literal["2.0"] = "2.0"
    template_version: str = "1"
    structure_contract: Literal["measured", "limited_capability", "legacy"] = (
        "legacy"
    )
    sections: list[SectionLayoutSpec] = Field(default_factory=list)
    unsupported_features: list[str] = Field(default_factory=list)


class AnalysisArtifactManifest(AnalysisModel):
    raw_layout_filename: str
    style_spec_filename: str
    layout_spec_filename: str | None = None
    page_png_filenames: list[str] = Field(default_factory=list)
    overlay_png_filenames: list[str] = Field(default_factory=list)


class TargetPdfAnalysis(AnalysisModel):
    analysis_version: Literal["1.0"] = "1.0"
    page_count: int = Field(ge=1)
    text_character_count: int = Field(ge=0)
    pages: list[PageLayout]
    style_spec: TemplateStyleSpec
    layout_spec: LayoutTemplateSpec | None = None
    artifacts: AnalysisArtifactManifest
    warnings: list[str] = Field(default_factory=list)


class PageComparison(AnalysisModel):
    page_number: int = Field(ge=1)
    target_width_px: int = Field(gt=0)
    target_height_px: int = Field(gt=0)
    generated_width_px: int = Field(gt=0)
    generated_height_px: int = Field(gt=0)
    same_dimensions: bool
    layout_similarity: float = Field(ge=0, le=1)
    foreground_iou: float = Field(ge=0, le=1)
    density_similarity: float = Field(ge=0, le=1)
    projection_similarity: float = Field(ge=0, le=1)
    difference_filename: str


class VisualComparisonReport(AnalysisModel):
    comparison_version: Literal["1.0"] = "1.0"
    target_page_count: int = Field(ge=1)
    generated_page_count: int = Field(ge=1)
    compared_page_count: int = Field(ge=0)
    page_count_similarity: float = Field(ge=0, le=1)
    average_layout_similarity: float = Field(ge=0, le=1)
    target_page_filenames: list[str] = Field(default_factory=list)
    generated_page_filenames: list[str] = Field(default_factory=list)
    pages: list[PageComparison] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def built_in_template_style_spec() -> TemplateStyleSpec:
    return TemplateStyleSpec(
        source_type="built_in",
        template_name="default",
        page=PageStyle(
            width_pt=595.28,
            height_pt=841.89,
            orientation="portrait",
            margin_top_pt=54.0,
            margin_right_pt=54.0,
            margin_bottom_pt=54.0,
            margin_left_pt=54.0,
        ),
        section_labels=SectionLabels(),
        show_document_title=True,
        confidence=1.0,
    )


def built_in_layout_template_spec() -> LayoutTemplateSpec:
    """Native v2 built-in template; this is a normal no-target choice."""
    spec = built_in_template_style_spec()
    labels = spec.section_labels
    assert labels is not None
    sources = (
        ("contact", labels.contact),
        ("summary", labels.summary),
        ("skills", labels.skills),
        ("languages", labels.languages),
        ("work_experience", labels.work_experience),
        ("education", labels.education),
        ("certifications", labels.certifications),
        ("additional_details", labels.additional_details),
    )
    return LayoutTemplateSpec(
        **spec.model_dump(exclude={"schema_version", "template_name"}),
        template_name="default_v2",
        template_version="built-in-2",
        structure_contract="measured",
        sections=[
            SectionLayoutSpec(
                section_id=source,
                source=source,
                label=label,
                layout=(
                    getattr(spec.lists, f"{source}_layout")
                    if source in {"skills", "languages", "certifications"}
                    else "full_width"
                ),
            )
            for source, label in sources
        ],
    )


def migrate_template_style_spec(spec: TemplateStyleSpec) -> LayoutTemplateSpec:
    """Quarantined reader for genuinely legacy stored v1 artifacts.

    New target analysis and generation code must never call this function.
    The hard-coded sidebar convention exists only so old local artifacts remain
    inspectable and is marked in the resulting spec.
    """

    two_column = spec.columns.count == 2
    labels = spec.section_labels or SectionLabels()

    def placement_for(source: str) -> str:
        if not two_column:
            return "full_width"
        if source in {
            "contact",
            "skills",
            "languages",
            "certifications",
            "additional_details",
        }:
            return "sidebar"
        return "main_column"

    return LayoutTemplateSpec(
        **spec.model_dump(exclude={"schema_version", "warnings"}),
        structure_contract="legacy",
        warnings=[
            *spec.warnings,
            "LEGACY COMPATIBILITY: v1 TemplateStyleSpec was migrated with "
            "hard-coded section order and sidebar placement; do not use this "
            "spec for a newly uploaded target.",
        ],
        unsupported_features=["legacy_v1_structure_not_measured"],
        sections=[
            SectionLayoutSpec(
                section_id="contact",
                source="contact",
                label=labels.contact,
                placement=placement_for("contact"),
            ),
            SectionLayoutSpec(
                section_id="summary",
                source="summary",
                label=labels.summary,
                placement=placement_for("summary"),
            ),
            SectionLayoutSpec(
                section_id="skills",
                source="skills",
                label=labels.skills,
                layout=spec.lists.skills_layout,
                column_count=2 if spec.lists.skills_layout == "two_column_bullets" else 1,
                placement=placement_for("skills"),
            ),
            SectionLayoutSpec(
                section_id="languages",
                source="languages",
                label=labels.languages,
                layout=spec.lists.languages_layout,
                placement=placement_for("languages"),
            ),
            SectionLayoutSpec(
                section_id="education",
                source="education",
                label=labels.education,
                placement=placement_for("education"),
            ),
            SectionLayoutSpec(
                section_id="work_experience",
                source="work_experience",
                label=labels.work_experience,
                placement=placement_for("work_experience"),
            ),
            SectionLayoutSpec(
                section_id="certifications",
                source="certifications",
                label=labels.certifications,
                layout=spec.lists.certifications_layout,
                placement=placement_for("certifications"),
            ),
            SectionLayoutSpec(
                section_id="additional_details",
                source="additional_details",
                label=labels.additional_details,
                placement=placement_for("additional_details"),
            ),
        ],
    )
