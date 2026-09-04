from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from app.extraction.candidate_schema import CandidateProfile
from app.generation.template_mapper import ClientFacingRenderContext, build_client_render_context
from app.template_analysis.artifacts import load_layout_template_spec
from app.template_analysis.schemas import (
    LayoutTemplateSpec,
    SectionLayoutSpec,
    TextStyle,
)
from app.template_analysis.visual_comparator import compare_pdf_layouts
from app.template_analysis.commercial.models import NormalizedLayoutEvidence, NormalizedTextBlock
from tests.commercial_api.providers import render_html_with_chromium, run_baseline_layout


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PROFILE = PROJECT_ROOT / "tests" / "commercial_api" / "corpus" / "expected_candidate_profile.json"


def compile_evidence_to_mvp_style(
    base_spec: LayoutTemplateSpec,
    evidence: NormalizedLayoutEvidence,
    provider: str,
) -> LayoutTemplateSpec:
    """Apply analyzer typography to deterministic local geometry for review.

    This is evaluation-only. It deliberately keeps page/column/section geometry
    fixed so the generated PDFs isolate analyzer typography differences.
    """

    base_layout = base_spec
    semantic_blocks = [*evidence.text_blocks, *evidence.paragraphs]
    body_candidates = [
        block
        for block in evidence.text_blocks
        if _role(block) not in {"title", "heading", "head", "sectionheading", "h1", "h2", "h3"}
    ]
    title_candidates = [
        block for block in semantic_blocks if _role(block) in {"title", "h1"}
    ]
    heading_candidates = [
        block
        for block in semantic_blocks
        if _role(block) in {"heading", "head", "sectionheading", "h1", "h2", "h3"}
    ]
    title_block = _largest_style_block(title_candidates or evidence.text_blocks)
    if not heading_candidates:
        heading_candidates = [
            block
            for block in evidence.text_blocks
            if block.bold and block is not title_block
        ]
    warnings = [
        *base_layout.warnings,
        f"{provider} typography was applied by the evaluation-only outcome compiler.",
        "Page geometry and semantic placements use the same deterministic local baseline.",
    ]
    return base_layout.model_copy(
        deep=True,
        update={
            "template_name": f"{provider}_outcome_review",
            "body": _merge_text_style(
                base_layout.body,
                _weighted_representative(body_candidates or evidence.text_blocks),
                role="body",
            ),
            "title": _merge_text_style(base_layout.title, title_block, role="title"),
            "heading": _merge_text_style(
                base_layout.heading,
                _weighted_representative(heading_candidates),
                role="heading",
            ),
            "warnings": warnings,
        },
    )


def _merge_text_style(
    base: TextStyle,
    block: NormalizedTextBlock | None,
    *,
    role: str,
) -> TextStyle:
    if block is None:
        return base.model_copy(deep=True)
    update: dict[str, Any] = {}
    font_family = _word_font_name(block.font_family)
    if font_family:
        update["font_family"] = font_family
    bounds = {"body": (6.0, 18.0), "heading": (8.0, 30.0), "title": (10.0, 60.0)}
    low, high = bounds[role]
    if block.font_size is not None and low <= block.font_size <= high:
        update["font_size_pt"] = block.font_size
    if block.bold is not None:
        update["bold"] = block.bold
    if block.italic is not None:
        update["italic"] = block.italic
    color = _normalized_hex_color(block.color)
    if color:
        update["color_hex"] = color
    return base.model_copy(deep=True, update=update)


def _weighted_representative(
    blocks: list[NormalizedTextBlock],
) -> NormalizedTextBlock | None:
    candidates = [block for block in blocks if block.text.strip()]
    if not candidates:
        return None
    counts: Counter[tuple[Any, ...]] = Counter()
    for block in candidates:
        key = (
            block.font_family,
            round(block.font_size, 2) if block.font_size else None,
            block.bold,
            block.italic,
            block.color,
        )
        counts[key] += max(len(block.text), 1)
    selected = counts.most_common(1)[0][0]
    return next(
        block
        for block in candidates
        if (
            block.font_family,
            round(block.font_size, 2) if block.font_size else None,
            block.bold,
            block.italic,
            block.color,
        )
        == selected
    )


def _largest_style_block(
    blocks: list[NormalizedTextBlock],
) -> NormalizedTextBlock | None:
    candidates = [block for block in blocks if block.text.strip()]
    return max(candidates, key=lambda block: block.font_size or 0) if candidates else None


def _role(block: NormalizedTextBlock) -> str:
    return (block.role or "").lower().split("[", 1)[0]


def _word_font_name(value: str | None) -> str | None:
    if not value:
        return None
    name = value.split(",", 1)[0].strip().strip("'\"")
    cleaned = re.sub(r"(?:Bold|Italic|Oblique|Regular)$", "", name, flags=re.I).strip()
    normalized = re.sub(r"[^a-z0-9]", "", cleaned.lower())
    if normalized in {"times", "timesroman", "timesnewroman", "timesnewromanpsmt"}:
        return "Times New Roman"
    if normalized in {"helvetica", "arial", "arialmt"}:
        return "Arial"
    return cleaned


def _normalized_hex_color(value: str | None) -> str | None:
    if not value:
        return None
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return value.upper()
    match = re.fullmatch(r"DeviceRGB\(([^)]+)\)", value)
    if not match:
        return None
    try:
        channels = [float(item) for item in match.group(1).split(",")]
    except ValueError:
        return None
    if len(channels) != 3:
        return None
    values = [round(max(0.0, min(1.0, channel)) * 255) for channel in channels]
    return "#" + "".join(f"{channel:02X}" for channel in values)


def build_outcome_review(run_dir: Path) -> Path:
    run_dir = run_dir.resolve()
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    selected_cases = manifest.get("configuration", {}).get("cases") or []
    if not selected_cases:
        raise ValueError("Outcome review requires at least one completed layout case.")
    outcome_dir = run_dir / "outcome_review"
    if outcome_dir.exists():
        shutil.rmtree(outcome_dir)
    outcome_dir.mkdir(parents=True)

    profile = CandidateProfile.model_validate_json(EXPECTED_PROFILE.read_text(encoding="utf-8"))
    context = build_client_render_context(profile)
    (outcome_dir / "candidate_profile.json").write_text(
        json.dumps(profile.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    rows: list[dict[str, Any]] = []
    for case_id in selected_cases:
        target_source = run_dir / "inputs" / f"{case_id}.pdf"
        if not target_source.is_file():
            raise FileNotFoundError(f"Target PDF is missing: {target_source}")
        case_dir = outcome_dir / case_id
        case_dir.mkdir()
        target_pdf = case_dir / "target_format.pdf"
        shutil.copy2(target_source, target_pdf)

        local_dir = case_dir / "local_analysis"
        baseline_evidence, _ = run_baseline_layout(target_pdf, local_dir)
        base_spec = load_layout_template_spec(local_dir / "layout_template_spec.json")
        evidence_by_provider = {"baseline": baseline_evidence}
        cases_root = run_dir / "cases" / "layout" / case_id
        if cases_root.is_dir():
            for source_dir in sorted(path for path in cases_root.iterdir() if path.is_dir()):
                normalized_path = source_dir / "normalized.json"
                if normalized_path.is_file():
                    evidence_by_provider[source_dir.name] = NormalizedLayoutEvidence.model_validate_json(
                        normalized_path.read_text(encoding="utf-8")
                    )

        for provider, evidence in evidence_by_provider.items():
            provider_dir = case_dir / provider
            provider_dir.mkdir()
            spec = (
                base_spec.model_copy(deep=True, update={"template_name": "local_baseline"})
                if provider == "baseline"
                else compile_evidence_to_mvp_style(base_spec, evidence, provider)
            )
            spec_path = provider_dir / "layout_template_spec.json"
            spec_path.write_text(
                json.dumps(spec.model_dump(mode="json"), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            html_path = provider_dir / "candidate_profile.html"
            html_path.write_text(render_context_to_html(context, spec), encoding="utf-8")
            pdf_path = render_html_with_chromium(html_path, provider_dir / f"{provider}.pdf")
            comparison = compare_pdf_layouts(target_pdf, pdf_path, provider_dir / "comparison")
            rows.append(
                {
                    "case_id": case_id,
                    "provider": provider,
                    "target": target_pdf.relative_to(outcome_dir).as_posix(),
                    "pdf": pdf_path.relative_to(outcome_dir).as_posix(),
                    "html": html_path.relative_to(outcome_dir).as_posix(),
                    "spec": spec_path.relative_to(outcome_dir).as_posix(),
                    "preview": (provider_dir / "comparison" / "generated_page_001.png").relative_to(outcome_dir).as_posix(),
                    "diff": (provider_dir / "comparison" / "comparison_page_001_diff.png").relative_to(outcome_dir).as_posix(),
                    "layout_similarity": comparison.average_layout_similarity,
                    "generated_pages": comparison.generated_page_count,
                }
            )

    review_manifest = {
        "schema_version": "commercial_api/outcome-review/1",
        "source_run": run_dir.name,
        "targets": [f"{case_id}/target_format.pdf" for case_id in selected_cases],
        "candidate_profile": "candidate_profile.json",
        "renderer": "chromium",
        "rows": rows,
        "decision_rule": (
            "Metrics are documentation only. The product owner judges quality "
            "by inspecting the generated PDFs and comparison images."
        ),
    }
    (outcome_dir / "outcome_manifest.json").write_text(
        json.dumps(review_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (outcome_dir / "REVIEW_INDEX.md").write_text(
        _review_index(rows), encoding="utf-8"
    )
    return outcome_dir


def render_context_to_html(
    context: ClientFacingRenderContext, spec: LayoutTemplateSpec
) -> str:
    sections = spec.sections or []
    sidebar = [section for section in sections if section.placement == "sidebar"]
    main = [section for section in sections if section.placement != "sidebar"]
    if spec.columns.sidebar_side == "right":
        grid = f"{spec.columns.left_column_ratio:.4f}fr 1fr"
        columns = f"<main>{_sections_html(main, context, spec)}</main><aside>{_sections_html(sidebar, context, spec)}</aside>"
    else:
        sidebar_ratio = max(1.0 - spec.columns.left_column_ratio, 0.2)
        grid = f"{sidebar_ratio:.4f}fr 1fr"
        columns = f"<aside>{_sections_html(sidebar, context, spec)}</aside><main>{_sections_html(main, context, spec)}</main>"
    if spec.columns.count == 1:
        grid = "1fr"
        columns = f"<main>{_sections_html(sections, context, spec)}</main>"
    sidebar_contrast = (
        ".layout aside, .layout aside .section, .layout aside .section h2 "
        "{ color: #FFFFFF !important; }"
        if spec.decoration.sidebar_background_hex
        else ""
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
@page {{ size: {spec.page.width_pt}pt {spec.page.height_pt}pt; margin: {spec.page.margin_top_pt}pt {spec.page.margin_right_pt}pt {spec.page.margin_bottom_pt}pt {spec.page.margin_left_pt}pt; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ {_text_css(spec.body)} line-height: {_line_height(spec)}; }}
header {{ margin-bottom: {spec.spacing.section_after_pt + 4}pt; {f'border-bottom: {spec.decoration.rule_width_pt}pt solid {spec.decoration.rule_color_hex};' if spec.decoration.header_rule else ''} }}
h1 {{ margin: 0; {_text_css(spec.title)} }}
.subtitle {{ margin-top: 2pt; {_text_css(spec.heading)} }}
.layout {{ display: grid; grid-template-columns: {grid}; gap: {spec.columns.gutter_pt}pt; align-items: start; }}
aside {{ background: {spec.decoration.sidebar_background_hex or 'transparent'}; padding: {8 if spec.decoration.sidebar_background_hex else 0}pt; }}
{sidebar_contrast}
.section {{ margin: 0 0 {spec.spacing.section_after_pt + 4}pt 0; break-inside: avoid; }}
.section h2 {{ margin: {spec.spacing.section_before_pt}pt 0 {spec.spacing.section_after_pt}pt; {_text_css(spec.heading)} {f'border-bottom: {spec.decoration.rule_width_pt}pt solid {spec.decoration.rule_color_hex}; padding-bottom: 2pt;' if spec.decoration.heading_rule else ''} }}
.section p {{ margin: 0 0 {spec.spacing.paragraph_after_pt}pt; }}
ul {{ margin: 0; padding-left: 14pt; }}
li {{ margin-bottom: 2pt; }}
.entry {{ margin-bottom: {spec.spacing.paragraph_after_pt + 2}pt; break-inside: avoid; }}
.entry-title {{ font-weight: 700; }}
.meta {{ font-style: italic; }}
</style></head><body>
<header><h1>{html.escape(context.candidate_heading)}</h1>{f'<div class="subtitle">{html.escape(context.candidate_subheading)}</div>' if context.candidate_subheading else ''}</header>
<div class="layout">{columns}</div>
</body></html>"""


def _sections_html(
    sections: list[SectionLayoutSpec],
    context: ClientFacingRenderContext,
    spec: LayoutTemplateSpec,
) -> str:
    return "".join(_section_html(section, context, spec) for section in sections)


def _section_html(
    section: SectionLayoutSpec,
    context: ClientFacingRenderContext,
    spec: LayoutTemplateSpec,
) -> str:
    content = _section_content(section.source, context)
    if not content:
        return ""
    heading_style = _text_css(section.heading_style or spec.heading)
    body_style = _text_css(section.body_style or spec.body)
    return (
        f'<section class="section" style="{body_style}">'
        f'<h2 style="{heading_style}">{html.escape(section.label)}</h2>{content}</section>'
    )


def _section_content(source: str, context: ClientFacingRenderContext) -> str:
    if source == "contact":
        return "".join(f"<p>{html.escape(line)}</p>" for line in context.contact_lines)
    if source == "summary":
        return f"<p>{html.escape(context.professional_summary or '')}</p>"
    if source in {"skills", "languages", "certifications"}:
        items = getattr(context, source)
        return _list_html(items)
    if source == "work_experience":
        parts = []
        for entry in context.work_experience:
            title = " — ".join(filter(None, (entry.title, entry.company)))
            parts.append(
                '<div class="entry">'
                f'<div class="entry-title">{html.escape(title)}</div>'
                f'<div class="meta">{html.escape(" · ".join(filter(None, (entry.location, entry.date_range))))}</div>'
                f'{_list_html(entry.description)}</div>'
            )
        return "".join(parts)
    if source == "education":
        parts = []
        for entry in context.education:
            detail = ", ".join(filter(None, (entry.degree, entry.field_of_study)))
            parts.append(
                '<div class="entry">'
                f'<div class="entry-title">{html.escape(entry.institution or "")}</div>'
                f'<div>{html.escape(detail)}</div><div class="meta">{html.escape(entry.date_range or "")}</div></div>'
            )
        return "".join(parts)
    if source == "additional_details":
        return "".join(
            f"<p><strong>{html.escape(item.label)}:</strong> {html.escape(item.value)}</p>"
            for item in context.additional_details
        )
    return ""


def _list_html(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"


def _text_css(style: TextStyle) -> str:
    font_family = style.font_family.replace("\\", "\\\\").replace("'", "\\'")
    return (
        f"font-family: '{font_family}', sans-serif; "
        f"font-size: {style.font_size_pt}pt; font-weight: {'700' if style.bold else '400'}; "
        f"font-style: {'italic' if style.italic else 'normal'}; color: {style.color_hex}; "
        f"letter-spacing: {style.character_spacing_pt}pt; word-spacing: {style.word_spacing_pt}pt;"
    )


def _line_height(spec: LayoutTemplateSpec) -> str:
    if spec.spacing.line_height_pt:
        return f"{spec.spacing.line_height_pt}pt"
    return str(spec.spacing.line_spacing)


def _review_index(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Owner PDF Review",
        "",
        "Reports and scores are documentation only. Judge the workflows by opening the generated PDFs.",
        "",
        "- [Review candidate profile](candidate_profile.json)",
        "",
        "| Review | Target | Analyzer | Generated PDF | Preview | Difference | Diagnostic similarity | Pages |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| [ ] | [{row['case_id']}]({row['target']}) | {row['provider']} | [Open PDF]({row['pdf']}) | "
            f"[Preview]({row['preview']}) | [Diff]({row['diff']}) | "
            f"{row['layout_similarity']:.4f} | {row['generated_pages']} |"
        )
    lines.extend(
        [
            "",
            "For each target, all outcomes use the same review candidate content and Chromium renderer. Only the target analyzer evidence changes.",
            "Target candidate facts are not used in generated candidate content.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build owner-reviewable PDFs from a commercial analyzer run.")
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args(argv)
    output = build_outcome_review(args.run)
    print(f"Outcome review: {output}")
    print(f"Open: {output / 'REVIEW_INDEX.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
