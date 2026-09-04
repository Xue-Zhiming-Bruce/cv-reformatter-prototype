from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field

from app.extraction.candidate_document_analyzer import SourceCoverageLedger
from app.generation.template_mapper import (
    AVAILABLE_UPON_REQUEST_LABEL,
    ClientFacingRenderContext,
    PENDING_CONFIRMATION_LABEL,
    RenderAdditionalSection,
)
from app.template_analysis.schemas import LayoutTemplateSpec


class ContentValidationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OutputContentCheck(ContentValidationModel):
    format: Literal["pdf"]
    passed: bool
    missing_items: list[str] = Field(default_factory=list)


class ContentValidationReport(ContentValidationModel):
    schema_version: Literal["content-validation/1.0"] = "content-validation/1.0"
    passed: bool
    expected_item_count: int = Field(ge=0)
    checks: list[OutputContentCheck]


def validate_generated_content(
    context: ClientFacingRenderContext,
    *,
    pdf_path: str | Path,
    layout_spec: LayoutTemplateSpec | None = None,
) -> ContentValidationReport:
    """Fail closed when approved render-context text vanishes from an output."""

    expected = _expected_items(context, layout_spec)
    checks = [
        _check("pdf", _read_pdf_text(Path(pdf_path)), expected),
    ]
    return ContentValidationReport(
        passed=all(check.passed for check in checks),
        expected_item_count=len(expected),
        checks=checks,
    )


def _expected_items(
    context: ClientFacingRenderContext,
    layout_spec: LayoutTemplateSpec | None = None,
) -> list[str]:
    items: list[str] = [context.candidate_heading]
    if context.candidate_subheading and (
        layout_spec is None or layout_spec.header.show_candidate_subheading
    ):
        items.append(context.candidate_subheading)
    if layout_spec is not None and context.contact_items:
        # Only expect contact items whose field is in the spec's contact_fields;
        # the renderer only emits those fields.
        spec_fields = set(layout_spec.header.contact_fields) if layout_spec.header.contact_fields else None
        items.extend(
            item.value
            for item in context.contact_items
            if spec_fields is None or item.field in spec_fields
        )
    else:
        items.extend(context.contact_lines)
    if context.professional_summary:
        items.append(context.professional_summary)
    items.extend(context.skills)
    for group in context.skill_groups:
        items.extend([group.label, *group.skills])
    items.extend(context.languages)
    for entry in context.work_experience:
        items.extend(
            value
            for value in (
                entry.company,
                entry.title,
                entry.location,
                entry.date_range,
            )
            if value
        )
        items.extend(entry.description)
    for entry in context.education:
        items.extend(
            value
            for value in (
                entry.institution,
                entry.degree,
                entry.field_of_study,
                entry.date_range,
            )
            if value
        )
    items.extend(context.certifications)
    for detail in context.additional_details:
        if detail.value in {
            PENDING_CONFIRMATION_LABEL,
            AVAILABLE_UPON_REQUEST_LABEL,
        }:
            continue
        items.extend([detail.label, detail.value])
    contact_slot_labels = {
        section.label.casefold()
        for section in (layout_spec.sections if layout_spec is not None else [])
        if section.source == "additional_details" and section.contact_fields
    }
    for section in context.additional_sections:
        if section.heading.casefold() in contact_slot_labels:
            continue
        items.append(section.heading)
        for entry in section.entries:
            items.extend(
                value
                for value in (entry.title, *entry.links, *entry.description)
                if value
            )
    return [item for item in items if _normalize(item)]


def _check(
    output_format: Literal["pdf"],
    text: str,
    expected: list[str],
) -> OutputContentCheck:
    normalized_text = _normalize(text)
    expected_counts = Counter(_normalize(item) for item in expected)
    missing: list[str] = []
    for normalized_item, required_count in expected_counts.items():
        if normalized_text.count(normalized_item) < required_count:
            missing.extend(
                item for item in expected if _normalize(item) == normalized_item
            )
    missing = list(dict.fromkeys(missing))
    return OutputContentCheck(
        format=output_format,
        passed=not missing,
        missing_items=missing,
    )


def _read_pdf_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _normalize(value: str) -> str:
    value = re.sub(r"-[ \t]*\r?\n[ \t]*", "-", value)
    return re.sub(r"\s+", " ", value).strip().casefold()


class EntryStructureExpectation(ContentValidationModel):
    """One expected entry: title at the entry-title tier, links at the
    metadata tier, description bullets at body size (in row order)."""

    title: str | None = None
    link_count: int = Field(default=0, ge=0)
    bullet_count: int = Field(default=0, ge=0)


class SectionStructureExpectation(ContentValidationModel):
    heading: str
    entry_title_pt: float | None = None
    entry_title_bold: bool | None = None
    entry_metadata_pt: float | None = None
    body_pt: float | None = None
    entries: list[EntryStructureExpectation] = Field(default_factory=list)


class StructureGateCheck(ContentValidationModel):
    section_heading: str
    matched: bool
    issues: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class StructureGateReport(ContentValidationModel):
    """Entry-geometry structure gate: tier fonts, bullet state, ordering, and
    bullet counts per entry, plus optional source-coverage safety."""

    schema_version: Literal["structure-gate/1.0"] = "structure-gate/1.0"
    passed: bool
    checks: list[StructureGateCheck] = Field(default_factory=list)


def validate_output_structure(
    context: ClientFacingRenderContext,
    *,
    html_path: str | Path,
    layout_spec: LayoutTemplateSpec | None = None,
    coverage_ledger: SourceCoverageLedger | None = None,
    pdf_path: str | Path | None = None,
    target_pdf_path: str | Path | None = None,
) -> StructureGateReport:
    """Assert entry geometry against expected tiers from the approved render
    context and the compiled layout spec.

    Each additional section is located by semantic HTML section identity; its
    articles must reproduce, per entry and in order: the entry title at
    the entry-title tier (bold, no bullet), the metadata links at the
    metadata tier (no bullet), and the description bullets at body size with
    the expected bullet counts. ``coverage_ledger`` adds a coverage-safety
    check when supplied.
    """

    body_pt = layout_spec.body.font_size_pt if layout_spec is not None else 10.0
    html = Path(html_path).read_text(encoding="utf-8")
    parser = _StructureHtmlParser()
    parser.feed(html)
    contact_slot_labels = {
        section.label.casefold()
        for section in (layout_spec.sections if layout_spec is not None else [])
        if section.source == "additional_details" and section.contact_fields
    }
    expectations = [
        _section_structure_expectation(section, layout_spec, body_pt)
        for section in context.additional_sections
        if section.heading.casefold() not in contact_slot_labels
    ]
    checks: list[StructureGateCheck] = []
    for expectation in expectations:
        checks.append(_check_section_structure(parser.sections, expectation, body_pt))
    if layout_spec is not None:
        checks.extend(_check_measured_renderer_semantics(context, layout_spec, html))
        if pdf_path is not None:
            checks.append(
                _check_pdf_page_geometry(
                    Path(pdf_path), layout_spec,
                    Path(target_pdf_path) if target_pdf_path else None,
                    set(re.findall(r'data-section-label="([^"]+)"', html)),
                )
            )
    if coverage_ledger is not None:
        coverage_issues = (
            []
            if coverage_ledger.approvable
            else [
                "source-coverage blocks approval and generation: "
                f"{coverage_ledger.blocked_block_ids}"
            ]
        )
        checks.append(
            StructureGateCheck(
                section_heading="source-coverage",
                matched=coverage_ledger.approvable,
                issues=coverage_issues,
            )
        )
    return StructureGateReport(
        passed=all(not check.issues and check.matched for check in checks),
        checks=checks,
    )


def _check_pdf_page_geometry(
    pdf_path: Path,
    layout_spec: LayoutTemplateSpec,
    target_pdf_path: Path | None,
    rendered_labels: set[str],
) -> StructureGateCheck:
    issues: list[str] = []
    notes: list[str] = []
    tolerance_pt = 3.0
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            chars = page.chars
            if not chars:
                continue
            top = min(float(char["top"]) for char in chars)
            bottom = max(float(char["bottom"]) for char in chars)
            if top < layout_spec.page.margin_top_pt - tolerance_pt:
                issues.append(
                    f"page {page_number} content top {top:.1f}pt violates measured "
                    f"{layout_spec.page.margin_top_pt:.1f}pt top margin"
                )
            bottom_limit = page.height - layout_spec.page.margin_bottom_pt
            if bottom > bottom_limit + tolerance_pt:
                issues.append(
                    f"page {page_number} content bottom {bottom:.1f}pt exceeds measured "
                    f"{bottom_limit:.1f}pt bottom threshold"
                )
        generated_break = _last_page_one_heading(pdf.pages[0], layout_spec, rendered_labels)
    if target_pdf_path is not None and target_pdf_path.exists():
        with pdfplumber.open(target_pdf_path) as target_pdf:
            target_text = _normalize("\n".join(page.extract_text() or "" for page in target_pdf.pages))
            comparable = all(_normalize(label) in target_text for label in rendered_labels)
            target_break = _last_page_one_heading(target_pdf.pages[0], layout_spec, rendered_labels)
        if comparable:
            if target_break and generated_break != target_break:
                issues.append(
                    f"page-1 break ends at {generated_break!r}; target ends at {target_break!r}"
                )
        else:
            notes.append(
                "page-1 break comparison skipped because the target does not expose "
                "every rendered section label"
            )
    return StructureGateCheck(
        section_heading="page-geometry",
        matched=True,
        issues=issues,
        notes=notes,
    )


def _last_page_one_heading(
    page: Any,
    layout_spec: LayoutTemplateSpec,
    rendered_labels: set[str],
) -> str | None:
    text = _normalize(page.extract_text() or "")
    matches = [
        (text.rfind(_normalize(section.label)), section.label)
        for section in layout_spec.sections
        if section.show_heading
        and section.label in rendered_labels
        and _normalize(section.label) in text
    ]
    return max(matches, default=(-1, None))[1]


def _check_measured_renderer_semantics(
    context: ClientFacingRenderContext,
    layout_spec: LayoutTemplateSpec,
    html: str,
) -> list[StructureGateCheck]:
    """Verify semantic HTML shape before Chrome turns it into page geometry."""

    checks: list[StructureGateCheck] = []
    for section in layout_spec.sections:
        match = re.search(
            rf'<section[^>]*data-section-source="{re.escape(section.source)}"[^>]*'
            rf'data-section-label="{re.escape(section.label)}"[^>]*>(.*?)</section>',
            html,
            flags=re.DOTALL,
        )
        if match is None:
            continue
        body = match.group(1)
        issues: list[str] = []
        notes: list[str] = []
        if section.source == "work_experience" and context.work_experience:
            if section.split_entry_rows:
                if body.count('data-entry-row="primary"') != len(context.work_experience):
                    issues.append("work entries do not each expose a measured primary row")
                if body.count('data-entry-row="secondary"') != len(context.work_experience):
                    issues.append("work entries do not each expose a measured secondary row")
                if " | " in re.sub(r"<[^>]+>", "", body):
                    issues.append("work entry rows contain an unmeasured pipe delimiter")
            if section.entry_secondary_style is not None:
                expected = section.entry_secondary_style.color_hex.upper()
                colors = [value.upper() for value in re.findall(r'class="entry-row entry-secondary"[^>]*data-color="(#[0-9A-Fa-f]{6})"', body)]
                if not colors or any(value != expected for value in colors):
                    issues.append(f"company color does not equal measured {expected}")
            elif section.split_entry_rows:
                notes.append("company style measurement unavailable; color assertion skipped")
        elif section.source == "skills" and (context.skill_groups or context.skills):
            expected_layout = "chips" if section.badge_style is not None else section.layout
            layouts = set(re.findall(r'data-skill-layout="([a-z_]+)"', body))
            if expected_layout == "inline" and layouts != {"inline"}:
                issues.append(f"skill representation {sorted(layouts)} != measured inline layout")
            if expected_layout == "chips" and "chips" not in layouts:
                issues.append("skill representation does not use measured chips")
        elif section.source == "education" and context.education:
            if body.count('class="education-institution" data-bold="true"') != len(context.education):
                issues.append("education institution is not bold for every entry")
            if " | " in re.sub(r"<[^>]+>", "", body):
                issues.append("education rows contain an unmeasured pipe delimiter")
        elif section.source == "additional_details":
            if "portfolio_url" in section.contact_fields and any(
                item.field == "portfolio_url" for item in context.contact_items
            ) and 'data-contact-field="portfolio_url"' not in body:
                issues.append("portfolio URL is absent from the measured links section")
            if section.entry_metadata_style is not None and 'data-role="project-link"' in body:
                expected = section.entry_metadata_style.color_hex.upper()
                colors = [value.upper() for value in re.findall(r'class="entry-metadata"[^>]*data-role="project-link"[^>]*data-color="(#[0-9A-Fa-f]{6})"', body)]
                if not colors or any(value != expected for value in colors):
                    issues.append(f"project URL color does not equal measured {expected}")
        if issues or notes or section.source in {"work_experience", "skills", "education", "additional_details"}:
            checks.append(
                StructureGateCheck(
                    section_heading=section.label,
                    matched=True,
                    issues=issues,
                    notes=notes,
                )
            )
    return checks


def _section_structure_expectation(
    section: RenderAdditionalSection,
    layout_spec: LayoutTemplateSpec | None,
    body_pt: float,
) -> SectionStructureExpectation:
    entry_title_pt: float | None = None
    entry_title_bold: bool | None = None
    entry_metadata_pt: float | None = None
    if layout_spec is not None:
        for candidate in layout_spec.sections:
            if (
                candidate.additional_section_heading
                and candidate.additional_section_heading.casefold()
                == section.heading.casefold()
            ):
                if candidate.entry_title_style is not None:
                    entry_title_pt = candidate.entry_title_style.font_size_pt
                    entry_title_bold = bool(candidate.entry_title_style.bold)
                if candidate.entry_metadata_style is not None:
                    entry_metadata_pt = candidate.entry_metadata_style.font_size_pt
    return SectionStructureExpectation(
        heading=section.heading,
        entry_title_pt=entry_title_pt,
        entry_title_bold=entry_title_bold,
        entry_metadata_pt=entry_metadata_pt,
        body_pt=body_pt,
        entries=[
            EntryStructureExpectation(
                title=entry.title,
                link_count=len(entry.links),
                bullet_count=len(entry.description),
            )
            for entry in section.entries
        ],
    )


def _check_section_structure(
    sections: list[dict[str, Any]],
    expectation: SectionStructureExpectation,
    body_pt: float,
) -> StructureGateCheck:
    issues: list[str] = []
    notes = _unmeasured_tier_notes(expectation)
    try:
        section = next(
            item for item in sections
            if item["label"].casefold() == expectation.heading.casefold()
        )
    except StopIteration:
        return StructureGateCheck(
            section_heading=expectation.heading,
            matched=False,
            issues=["section heading not found in generated output"],
            notes=notes,
        )
    rows = section["entries"]
    for position, entry in enumerate(expectation.entries, start=1):
        row = rows[position - 1] if position <= len(rows) else None
        if row is None:
            issues.append(f"entry {position}: expected entry, found no row")
            continue
        if entry.title is not None:
            if row["title"] != entry.title:
                found = row["title"] or "no row"
                issues.append(
                    f"entry {position}: expected title {entry.title!r}, found {found!r}"
                )
            else:
                if expectation.entry_title_pt is not None:
                    size = row["title_pt"] or body_pt
                    if not _sizes_close(size, expectation.entry_title_pt):
                        issues.append(
                            f"entry {position}: title tier {size}pt != expected "
                            f"{expectation.entry_title_pt}pt"
                        )
                    if expectation.entry_title_bold and not row["title_bold"]:
                        issues.append(
                            f"entry {position}: title is not bold at the entry-title tier"
                        )
        if len(row["links"]) != entry.link_count:
            issues.append(
                f"entry {position}: expected {entry.link_count} metadata links, "
                f"found {len(row['links'])}"
            )
        for link in row["links"]:
            if expectation.entry_metadata_pt is not None:
                size = link["font_size_pt"] or body_pt
                if not _sizes_close(size, expectation.entry_metadata_pt):
                    issues.append(
                        f"entry {position}: link tier {size}pt != expected "
                        f"{expectation.entry_metadata_pt}pt"
                    )
        if len(row["bullets"]) != entry.bullet_count:
            issues.append(
                f"entry {position}: expected {entry.bullet_count} description bullets, "
                f"found {len(row['bullets'])}"
            )
    if len(rows) > len(expectation.entries):
        issues.append(
            f"unexpected extra rows after entry {len(expectation.entries)}: "
            f"{rows[len(expectation.entries)]['title']!r}"
        )
    return StructureGateCheck(
        section_heading=expectation.heading,
        matched=True,
        issues=issues,
        notes=notes,
    )


def _unmeasured_tier_notes(
    expectation: SectionStructureExpectation,
) -> list[str]:
    """Record tier measurements the compiled spec cannot provide instead of
    silently skipping the assertion (fail-visible, non-blocking)."""
    notes: list[str] = []
    if expectation.entry_title_pt is None and any(
        entry.title is not None for entry in expectation.entries
    ):
        notes.append(
            "entry-title tier measurement unavailable: the layout spec exposes "
            "no entry_title_style for this section; size assertion skipped"
        )
    if expectation.entry_metadata_pt is None and any(
        entry.link_count > 0 for entry in expectation.entries
    ):
        notes.append(
            "link tier measurement unavailable: the layout spec exposes no "
            "entry_metadata_style for this section; size assertion skipped"
        )
    return notes


class _StructureHtmlParser(HTMLParser):
    """Collect renderer-authored semantic sections without text boundaries."""

    def __init__(self) -> None:
        super().__init__()
        self.sections: list[dict[str, Any]] = []
        self._section: dict[str, Any] | None = None
        self._entry: dict[str, Any] | None = None
        self._capture: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "section" and "section" in classes:
            self._section = {"label": values.get("data-section-label") or "", "entries": []}
            self.sections.append(self._section)
        elif tag == "article" and "additional-entry" in classes and self._section is not None:
            self._entry = {"title": None, "title_pt": None, "title_bold": False, "links": [], "bullets": []}
            heading_attr = values.get("data-additional-heading")
            if heading_attr:
                target = next((item for item in self.sections if item["label"] == heading_attr), None)
                if target is None:
                    target = {"label": heading_attr, "entries": []}
                    self.sections.append(target)
            else:
                target = self._section
            target["entries"].append(self._entry)
        elif self._entry is not None and "entry-title" in classes:
            self._capture, self._text = "title", []
            self._entry["title_pt"] = _float_or_none(values.get("data-font-size-pt"))
            self._entry["title_bold"] = values.get("data-bold") == "true"
        elif self._entry is not None and "entry-metadata" in classes:
            self._capture, self._text = "link", []
            self._entry["link_pt"] = _float_or_none(values.get("data-font-size-pt"))
        elif self._entry is not None and tag == "li":
            self._capture, self._text = "bullet", []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._entry is not None and self._capture and tag in {"p", "li"}:
            text = "".join(self._text).strip()
            if self._capture == "title":
                self._entry["title"] = text
            elif self._capture == "link":
                self._entry["links"].append({"text": text, "font_size_pt": self._entry.pop("link_pt", None)})
            else:
                self._entry["bullets"].append(text)
            self._capture, self._text = None, []
        if tag == "article":
            self._entry = None
        elif tag == "section":
            self._section = None


def _float_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _sizes_close(actual: float, expected: float) -> bool:
    return abs(actual - expected) < 0.05
