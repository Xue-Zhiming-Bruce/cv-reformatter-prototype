from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from app.template_analysis.schemas import PageComparison, VisualComparisonReport


COMPARISON_FILENAME = "visual_comparison.json"
RENDER_DPI = 144


class VisualComparisonError(RuntimeError):
    """Raised when PDF visual comparison cannot complete."""


def compare_pdf_layouts(
    target_pdf_path: str | Path,
    generated_pdf_path: str | Path,
    output_dir: str | Path,
    *,
    render_dpi: int = RENDER_DPI,
) -> VisualComparisonReport:
    target_path = Path(target_pdf_path)
    generated_path = Path(generated_pdf_path)
    resolved_output_dir = Path(output_dir)
    _validate_pdf(target_path, "Target")
    _validate_pdf(generated_path, "Generated")
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    target_images = _render_pdf(
        target_path,
        resolved_output_dir,
        filename_prefix="comparison_target_page",
        dpi=render_dpi,
    )
    generated_images = _render_pdf(
        generated_path,
        resolved_output_dir,
        filename_prefix="generated_page",
        dpi=render_dpi,
    )

    compared_count = min(len(target_images), len(generated_images))
    comparisons: list[PageComparison] = []
    for page_index in range(compared_count):
        target_image = Image.open(resolved_output_dir / target_images[page_index]).convert("RGB")
        generated_image = Image.open(resolved_output_dir / generated_images[page_index]).convert("RGB")
        same_dimensions = target_image.size == generated_image.size
        original_generated_size = generated_image.size
        if not same_dimensions:
            generated_for_comparison = generated_image.resize(
                target_image.size,
                Image.Resampling.LANCZOS,
            )
        else:
            generated_for_comparison = generated_image

        metrics, difference_image = _layout_similarity(
            target_image,
            generated_for_comparison,
        )
        dimension_similarity = (
            min(target_image.width, original_generated_size[0])
            / max(target_image.width, original_generated_size[0])
            * min(target_image.height, original_generated_size[1])
            / max(target_image.height, original_generated_size[1])
        )
        layout_similarity = metrics["layout_similarity"] * dimension_similarity
        difference_filename = f"comparison_page_{page_index + 1:03d}_diff.png"
        difference_image.save(resolved_output_dir / difference_filename, format="PNG")
        comparisons.append(
            PageComparison(
                page_number=page_index + 1,
                target_width_px=target_image.width,
                target_height_px=target_image.height,
                generated_width_px=original_generated_size[0],
                generated_height_px=original_generated_size[1],
                same_dimensions=same_dimensions,
                layout_similarity=round(layout_similarity, 4),
                foreground_iou=round(metrics["foreground_iou"], 4),
                density_similarity=round(metrics["density_similarity"], 4),
                projection_similarity=round(metrics["projection_similarity"], 4),
                difference_filename=difference_filename,
            )
        )

    warnings: list[str] = [
        "Similarity combines foreground overlap, page density, text-flow projections, dimensions, and page count.",
        "It is a diagnostic layout signal, not proof of semantic or client-ready equivalence.",
        "Difference PNG legend: red is target-only structure, blue is generated-only, and magenta is overlap.",
    ]
    if len(target_images) != len(generated_images):
        warnings.append(
            "Target and generated PDFs have different page counts; only matching page positions were compared."
        )
    if any(not page.same_dimensions for page in comparisons):
        warnings.append(
            "One or more generated pages were resized for comparison because page dimensions differed."
        )
    largest_page_count = max(len(target_images), len(generated_images))
    page_count_similarity = compared_count / largest_page_count
    # Missing pages contribute zero. This prevents a matching first page from
    # hiding a material pagination mismatch.
    average_similarity = (
        sum(page.layout_similarity for page in comparisons) / largest_page_count
    )
    report = VisualComparisonReport(
        target_page_count=len(target_images),
        generated_page_count=len(generated_images),
        compared_page_count=compared_count,
        page_count_similarity=round(page_count_similarity, 4),
        average_layout_similarity=round(average_similarity, 4),
        target_page_filenames=target_images,
        generated_page_filenames=generated_images,
        pages=comparisons,
        warnings=warnings,
    )
    (resolved_output_dir / COMPARISON_FILENAME).write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report


def _layout_similarity(
    target: Image.Image,
    generated: Image.Image,
) -> tuple[dict[str, float], Image.Image]:
    target_structure = _structure_image(target)
    generated_structure = _structure_image(generated)
    target_mask = _foreground_mask(target_structure)
    generated_mask = _foreground_mask(generated_structure)
    target_coarse = _coarse_layout_mask(target_mask)
    generated_coarse = _coarse_layout_mask(generated_mask)

    foreground_iou = _mask_iou(target_coarse, generated_coarse)
    density_similarity = _density_similarity(target_mask, generated_mask)
    projection_similarity = _projection_similarity(target_coarse, generated_coarse)
    similarity = (
        foreground_iou * 0.35
        + density_similarity * 0.20
        + projection_similarity * 0.45
    )

    overlap = ImageChops.darker(target_mask, generated_mask)
    target_only = ImageChops.subtract(target_mask, overlap)
    generated_only = ImageChops.subtract(generated_mask, overlap)
    any_structure = ImageChops.lighter(target_mask, generated_mask)
    white = Image.new("L", target_mask.size, 255)
    red_layer = ImageChops.subtract(white, generated_only)
    green_layer = ImageChops.subtract(white, any_structure)
    blue_layer = ImageChops.subtract(white, target_only)
    colored_difference = Image.merge("RGB", (red_layer, green_layer, blue_layer))
    return (
        {
            "layout_similarity": max(0.0, min(1.0, similarity)),
            "foreground_iou": foreground_iou,
            "density_similarity": density_similarity,
            "projection_similarity": projection_similarity,
        },
        colored_difference,
    )


def _structure_image(image: Image.Image) -> Image.Image:
    grayscale = image.convert("L")
    grayscale = ImageChops.invert(grayscale)
    grayscale = grayscale.filter(ImageFilter.GaussianBlur(radius=1.5))
    return grayscale


def _foreground_mask(structure: Image.Image) -> Image.Image:
    return structure.point(lambda value: 255 if value >= 10 else 0, mode="L")


def _coarse_layout_mask(mask: Image.Image) -> Image.Image:
    expanded = mask.filter(ImageFilter.MaxFilter(size=13))
    coarse = expanded.resize((96, 128), Image.Resampling.BOX)
    return coarse.point(lambda value: 255 if value >= 20 else 0, mode="L")


def _mask_iou(target_mask: Image.Image, generated_mask: Image.Image) -> float:
    intersection = ImageChops.darker(target_mask, generated_mask).histogram()[255]
    union = ImageChops.lighter(target_mask, generated_mask).histogram()[255]
    if union == 0:
        return 1.0
    return intersection / union


def _density_similarity(target_mask: Image.Image, generated_mask: Image.Image) -> float:
    target_count = target_mask.histogram()[255]
    generated_count = generated_mask.histogram()[255]
    largest = max(target_count, generated_count)
    if largest == 0:
        return 1.0
    return min(target_count, generated_count) / largest


def _projection_similarity(target_mask: Image.Image, generated_mask: Image.Image) -> float:
    target_pixels = list(target_mask.get_flattened_data())
    generated_pixels = list(generated_mask.get_flattened_data())
    width, height = target_mask.size

    target_rows = [
        sum(target_pixels[row * width : (row + 1) * width]) / 255
        for row in range(height)
    ]
    generated_rows = [
        sum(generated_pixels[row * width : (row + 1) * width]) / 255
        for row in range(height)
    ]
    target_columns = [
        sum(target_pixels[row * width + column] for row in range(height)) / 255
        for column in range(width)
    ]
    generated_columns = [
        sum(generated_pixels[row * width + column] for row in range(height)) / 255
        for column in range(width)
    ]
    return (
        _distribution_similarity(target_rows, generated_rows)
        + _distribution_similarity(target_columns, generated_columns)
    ) / 2


def _distribution_similarity(target: list[float], generated: list[float]) -> float:
    target_total = sum(target)
    generated_total = sum(generated)
    if target_total == 0 or generated_total == 0:
        return 1.0 if target_total == generated_total else 0.0
    distance = sum(
        abs(target_value / target_total - generated_value / generated_total)
        for target_value, generated_value in zip(target, generated)
    )
    return max(0.0, 1 - distance / 2)


def _render_pdf(
    path: Path,
    output_dir: Path,
    *,
    filename_prefix: str,
    dpi: int,
) -> list[str]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - dependency installation issue.
        raise VisualComparisonError(
            "pypdfium2 is required for generated-vs-target PDF comparison."
        ) from exc

    filenames: list[str] = []
    try:
        document = pdfium.PdfDocument(str(path))
        scale = dpi / 72
        for page_index in range(len(document)):
            page = document[page_index]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil().convert("RGB")
            filename = f"{filename_prefix}_{page_index + 1:03d}.png"
            image.save(output_dir / filename, format="PNG")
            filenames.append(filename)
            bitmap.close()
            page.close()
        document.close()
    except Exception as exc:
        raise VisualComparisonError(f"PDF pages could not be rendered for comparison: {path}") from exc
    return filenames


def _validate_pdf(path: Path, label: str) -> None:
    if not path.exists() or not path.is_file() or path.suffix.lower() != ".pdf":
        raise VisualComparisonError(f"{label} PDF is missing or invalid: {path}")


def build_side_by_side_comparison(
    target_pdf_path: str | Path,
    generated_pdf_path: str | Path,
    output_dir: str | Path,
    *,
    render_dpi: int = RENDER_DPI,
) -> list[str]:
    """Render target and generated PDFs and stack every matching page pair
    horizontally into ``comparison_page_<NNN>_side_by_side.png`` (target left,
    generated right, thin divider) for direct human inspection.

    Returns the created filenames. Missing pages on either side are padded
    with a blank tile so the stack always covers the larger page count.
    """
    target_path = Path(target_pdf_path)
    generated_path = Path(generated_pdf_path)
    resolved_output_dir = Path(output_dir)
    _validate_pdf(target_path, "Target")
    _validate_pdf(generated_path, "Generated")
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    target_images = _render_pdf(
        target_path,
        resolved_output_dir,
        filename_prefix="comparison_target_page",
        dpi=render_dpi,
    )
    generated_images = _render_pdf(
        generated_path,
        resolved_output_dir,
        filename_prefix="generated_page",
        dpi=render_dpi,
    )

    filenames: list[str] = []
    page_count = max(len(target_images), len(generated_images))
    blank = Image.new("RGB", (100, 100), "white")
    for page_index in range(page_count):
        target_image = (
            Image.open(resolved_output_dir / target_images[page_index]).convert("RGB")
            if page_index < len(target_images)
            else blank
        )
        generated_image = (
            Image.open(resolved_output_dir / generated_images[page_index]).convert("RGB")
            if page_index < len(generated_images)
            else blank
        )
        gap = 12
        width = target_image.width + gap + generated_image.width
        height = max(target_image.height, generated_image.height)
        canvas = Image.new("RGB", (width, height), "white")
        canvas.paste(target_image, (0, 0))
        canvas.paste(generated_image, (target_image.width + gap, 0))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle(
            [target_image.width, 0, target_image.width + gap - 1, height],
            fill=(200, 200, 200),
        )
        try:
            draw.text((4, 4), "TARGET", fill=(0, 0, 0))
            draw.text((target_image.width + gap + 4, 4), "GENERATED", fill=(0, 0, 0))
        except Exception:  # pragma: no cover - label is decorative only.
            pass
        filename = f"comparison_page_{page_index + 1:03d}_side_by_side.png"
        canvas.save(resolved_output_dir / filename, format="PNG")
        filenames.append(filename)
    return filenames
