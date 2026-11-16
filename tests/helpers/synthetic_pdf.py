from __future__ import annotations

from pathlib import Path


def build_styled_target_pdf(path: Path) -> Path:
    """Write a synthetic, text-based two-column resume PDF without real personal data."""
    operations = [
        "q 0.10 0.20 0.35 rg 0 0 180 792 re f Q",
        "BT 0.12 0.20 0.35 rg /F2 24 Tf 220 738 Td (Alex Example) Tj ET",
        "BT 0.20 0.35 0.55 rg /F1 12 Tf 220 714 Td (Platform Engineer) Tj ET",
        "0.20 0.35 0.55 RG 1.5 w 220 700 m 560 700 l S",
        "BT 0.12 0.20 0.35 rg /F2 13 Tf 220 675 Td (PROFILE) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 220 654 Td (Engineer building reliable data systems.) Tj ET",
        "BT 0.12 0.20 0.35 rg /F2 13 Tf 220 612 Td (EXPERIENCE) Tj ET",
        "BT 0 0 0 rg /F2 10 Tf 220 590 Td (Senior Engineer | Example Systems) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 220 572 Td (2021 - Present) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 220 550 Td (Built resilient APIs and data pipelines.) Tj ET",
        "BT 0.12 0.20 0.35 rg /F2 13 Tf 220 506 Td (EDUCATION) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 220 484 Td (Example University | Computer Science) Tj ET",
        "BT 1 1 1 rg /F2 13 Tf 24 690 Td (CONTACT) Tj ET",
        "BT 1 1 1 rg /F1 9 Tf 24 666 Td (Example City) Tj ET",
        "BT 1 1 1 rg /F1 9 Tf 24 648 Td (alex@example.test) Tj ET",
        "BT 1 1 1 rg /F2 13 Tf 24 600 Td (SKILLS) Tj ET",
        "BT 1 1 1 rg /F1 9 Tf 24 576 Td (Python) Tj ET",
        "BT 1 1 1 rg /F1 9 Tf 24 558 Td (SQL) Tj ET",
        "BT 1 1 1 rg /F1 9 Tf 24 540 Td (FastAPI) Tj ET",
        "BT 1 1 1 rg /F2 13 Tf 24 492 Td (LANGUAGES) Tj ET",
        "BT 1 1 1 rg /F1 9 Tf 24 468 Td (English | Fluent) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)


def build_plain_target_pdf(path: Path) -> Path:
    """Write a dense, monochrome, single-column target PDF."""
    operations = [
        "BT 0 0 0 rg /F2 11 Tf 34 754 Td (ACCOUNTANT) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 34 736 Td (Professional Summary) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 34 718 Td (Experienced accountant supporting fast-paced business teams.) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 34 690 Td (Core Qualifications) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 34 672 Td (Excel QuickBooks Accounts Receivable Accounts Payable Customer Service) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 34 644 Td (Experience) Tj ET",
        "BT 0 0 0 rg /F2 11 Tf 34 626 Td (Accountant | Example Company) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 34 608 Td (January 2018 to Present | Example City) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 52 582 Td (Processed accounts receivable payments and reconciliations.) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 52 562 Td (Maintained monthly banking and customer account reports.) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 52 542 Td (Prepared quarterly sales and commission reports.) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 34 510 Td (Education) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 34 492 Td (Example University | Accounting) Tj ET",
    ]
    return _write_single_page_pdf(
        path,
        operations,
        regular_font="Times-Roman",
        bold_font="Times-Bold",
    )





def build_pipe_contact_header_target_pdf(path: Path) -> Path:
    """Write a classic header with plain pipe separators and a rule below it."""
    operations = [
        "BT 0 0 0 rg /F2 26 Tf 66 752 Td (Alex Example) Tj ET",
        "BT 0 0 0 rg /F1 9.5 Tf 66 726 Td (alex@example.test | +1 000-000-0000 | Example City | linkedin.com/in/example) Tj ET",
        "0 0 0 RG 0.75 w 66 716 m 529 716 l S",
        "BT 0 0 0 rg /F1 10.5 Tf 66 698 Td (Engineer building reliable applications and services.) Tj ET",
        "BT 0 0 0 rg /F2 12 Tf 66 670 Td (SKILLS) Tj ET",
        "0 0 0 RG 0.75 w 66 666 m 529 666 l S",
        "BT 0 0 0 rg /F1 10.5 Tf 66 650 Td (Python SQL Docker) Tj ET",
    ]
    return _write_single_page_pdf(path, operations, regular_font="Times-Roman", bold_font="Times-Bold")


def build_partial_heading_rules_target_pdf(path: Path) -> Path:
    """Write a target whose first and third headings, but not second, have rules."""
    operations = [
        "BT 0 0 0 rg /F2 26 Tf 66 752 Td (Alex Example) Tj ET",
        "0 0 0 RG 1.25 w 66 716 m 529 716 l S",
        "BT 0 0 0 rg /F2 12 Tf 66 670 Td (SECTION ONE) Tj ET",
        "0 0 0 RG 0.75 w 66 666 m 529 666 l S",
        "BT 0 0 0 rg /F1 10 Tf 66 644 Td (Synthetic body one.) Tj ET",
        "BT 0 0 0 rg /F2 12 Tf 66 590 Td (SECTION TWO) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 66 564 Td (Synthetic body two.) Tj ET",
        "BT 0 0 0 rg /F2 12 Tf 66 510 Td (SECTION THREE) Tj ET",
        "0 0 0 RG 0.75 w 66 506 m 529 506 l S",
        "BT 0 0 0 rg /F1 10 Tf 66 484 Td (Synthetic body three.) Tj ET",
    ]
    return _write_single_page_pdf(
        path,
        operations,
        regular_font="Times-Roman",
        bold_font="Times-Bold",
    )


def build_tech_teal_palette_target_pdf(path: Path) -> Path:
    """Synthetic teal-chip target with independently measurable color roles."""
    teal = "0.05098 0.58039 0.53333"
    navy = "0.05882 0.09020 0.16471"
    body = "0.2 0.25490 0.33333"
    muted = "0.39216 0.45490 0.54510"
    chip = "0.6 0.96471 0.89412"
    operations = [
        f"BT {navy} rg /F2 24 Tf 66 752 Td (Alex Example) Tj ET",
        f"BT {muted} rg /F1 9 Tf 66 730 Td (alex@example.test | Example City) Tj ET",
        f"{teal} RG 0.75 w 66 718 m 529 718 l S",
        f"BT {body} rg /F1 10 Tf 66 696 Td (Engineer building reliable applications.) Tj ET",
        f"BT {navy} rg /F2 12 Tf 66 666 Td (SKILLS) Tj ET",
        f"{teal} RG 0.75 w 66 660 m 529 660 l S",
        f"BT {muted} rg /F1 9 Tf 66 640 Td (Frontend) Tj ET",
    ]
    for index in range(10):
        x = 66 + (index % 5) * 58
        y = 612 - (index // 5) * 34
        operations.extend(
            [
                f"q {chip} rg {x} {y} 50 20 re f Q",
                f"BT {body} rg /F1 9 Tf {x + 6} {y + 6} Td (Skill {index + 1}) Tj ET",
            ]
        )
    operations.extend(
        [
            f"BT {navy} rg /F2 12 Tf 66 536 Td (EXPERIENCE) Tj ET",
            f"{teal} RG 0.75 w 66 530 m 529 530 l S",
            f"BT {body} rg /F2 10 Tf 66 510 Td (Position A) Tj ET",
            f"BT {teal} rg /F1 10 Tf 66 494 Td (Company A) Tj ET",
            f"BT {body} rg /F1 10 Tf 76 476 Td (Delivered reliable applications.) Tj ET",
        ]
    )
    return _write_single_page_pdf(path, operations)


def _pill_path(x: float, y: float, width: float, height: float) -> str:
    """Rounded-rectangle subpath at PDF coordinates, unpainted (r = height/2)."""
    radius = height / 2
    handle = 0.5523 * radius
    x1, y1 = x + width, y + height
    return "\n".join(
        [
            f"{x + radius} {y} m",
            f"{x1 - radius} {y} l",
            f"{x1 - radius + handle} {y} {x1} {y + radius - handle} {x1} {y + radius} c",
            f"{x1} {y1 - radius} l",
            f"{x1} {y1 - radius + handle} {x1 - radius + handle} {y1} {x1 - radius} {y1} c",
            f"{x + radius} {y1} l",
            f"{x + radius - handle} {y1} {x} {y1 - radius + handle} {x} {y1 - radius} c",
            f"{x} {y + radius} l",
            f"{x} {y + radius - handle} {x + radius - handle} {y} {x + radius} {y} c",
        ]
    )


def build_rounded_chip_target_pdf(
    path: Path,
    *,
    chip_count: int = 18,
    per_row: int = 6,
    fill: str = "0.6 0.96471 0.89412",
    text: str = "0.2 0.25490 0.33333",
) -> Path:
    """Target whose badges are filled rounded paths, as the HTML renderer draws them.

    The first chip's identical fill path is emitted twice, mirroring the live
    renderer's PDF output; the duplicate must still measure as one chip.
    """
    operations: list[str] = []
    for index in range(chip_count):
        row, column = divmod(index, per_row)
        x = 66 + column * 90
        y = 620 - row * 44
        pill = _pill_path(x, y, 80, 18)
        operations.append(f"q {fill} rg\n{pill}\nf Q")
        if index == 0:
            operations.append(f"q {fill} rg\n{pill}\nf Q")
        operations.append(
            f"BT {text} rg /F1 9 Tf {x + 9} {y + 5} Td (Skill {index + 1}) Tj ET"
        )
    return _write_single_page_pdf(path, operations)


def build_rounded_decoration_target_pdf(path: Path) -> Path:
    """Filled rounded decoration that must stay unmeasured instead of becoming badges.

    A wide banner with one short text run, a rounded blob taller than the badge
    band, a stroked curve rule, and one rounded shape holding two text lines.
    """
    banner = "0.05098 0.58039 0.53333"
    chip = "0.6 0.96471 0.89412"
    body = "0.2 0.25490 0.33333"
    operations = [
        f"q {banner} rg\n{_pill_path(66, 700, 400, 24)}\nf Q",
        f"BT 1 1 1 rg /F2 11 Tf 150 708 Td (Skills overview) Tj ET",
        f"q {chip} rg\n{_pill_path(66, 620, 120, 60)}\nf Q",
        f"BT {body} rg /F1 10 Tf 80 640 Td (Tall rounded blob) Tj ET",
        "0 0 0 RG 0.75 w\n66 600 m\n120 600 120 580 220 580 c S",
        f"q {chip} rg\n{_pill_path(66, 540, 140, 18)}\nf Q",
        f"BT {body} rg /F1 9 Tf 72 550 Td (First text line) Tj ET",
        f"BT {body} rg /F1 9 Tf 72 541 Td (Second line) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)


def build_banner_and_triangle_target_pdf(path: Path) -> Path:
    """Rounded and rectangular section banners plus filled triangles: decoration, not chips.

    Each banner is 400 x 24 pt with its label inset 84 pt from the left edge, so the
    measured inset cannot fit the badge cluster schema (0..72 pt). Each triangle is
    30 x 24 pt, filling half of its bounding box, and carries one short text run.
    """
    banner = "0.05098 0.58039 0.53333"
    triangle = "0.6 0.96471 0.89412"
    body = "0.2 0.25490 0.33333"
    operations = [
        f"q {banner} rg\n{_pill_path(66, 700, 400, 24)}\nf Q",
        f"BT 1 1 1 rg /F2 11 Tf 150 708 Td (Skills overview) Tj ET",
        f"q {banner} rg\n{_pill_path(66, 660, 400, 24)}\nf Q",
        f"BT 1 1 1 rg /F2 11 Tf 150 668 Td (Experience overview) Tj ET",
        f"q {banner} rg 66 620 400 24 re f Q",
        f"BT 1 1 1 rg /F2 11 Tf 150 628 Td (Education overview) Tj ET",
        f"q {triangle} rg\n66 580 m 96 580 l 81 604 l h f Q",
        f"BT {body} rg /F1 9 Tf 76 585 Td (Tri) Tj ET",
        f"q {triangle} rg\n166 580 m 196 580 l 181 604 l h f Q",
        f"BT {body} rg /F1 9 Tf 176 585 Td (Tri) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)


def build_multiline_rounded_shape_target_pdf(path: Path) -> Path:
    """One filled rounded shape holding two text lines: a single unclassifiable shape."""
    chip = "0.6 0.96471 0.89412"
    body = "0.2 0.25490 0.33333"
    operations = [
        f"q {chip} rg\n{_pill_path(66, 540, 140, 24)}\nf Q",
        f"BT {body} rg /F1 8 Tf 72 546 Td (First text line) Tj ET",
        f"BT {body} rg /F1 8 Tf 72 556 Td (Second line) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)







def build_two_page_overflow_pdf(path: Path) -> Path:
    """Write a two-page text PDF whose second page overflows the left margin.

    Page 1 carries normal in-margin content; page 2 places text at x=10pt,
    outside the 54pt left margin of the built-in A4 layout spec, so
    per-page structural validation must flag page 2 overflow while page 1
    stays clean.
    """
    page_operations = [
        [
            "BT 0 0 0 rg /F1 11 Tf 70 750 Td (SYNTHETIC_HEADING_PAGE_1) Tj ET",
            "BT 0 0 0 rg /F1 10 Tf 70 730 Td (SYNTHETIC_BODY_PAGE_1) Tj ET",
        ],
        [
            "BT 0 0 0 rg /F1 11 Tf 10 750 Td (SYNTHETIC_HEADING_PAGE_2) Tj ET",
        ],
    ]
    return _write_multi_page_pdf(path, page_operations)


def _write_multi_page_pdf(path: Path, page_operations: list[list[str]]) -> Path:
    """Write a multi-page text PDF with shared Helvetica fonts."""
    contents = ["\n".join(ops).encode("ascii") for ops in page_operations]
    page_count = len(contents)
    kids = " ".join(f"{3 + index} 0 R" for index in range(page_count))
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        (
            f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {page_count} >> endobj\n"
        ).encode("ascii"),
    ]
    # Object numbering: 1 catalog, 2 pages, 3..3+page_count-1 pages, then the
    # two fonts (5, 6), then one content stream per page (5 + page_count + i).
    content_object_numbers: list[int] = []
    for index in range(page_count):
        page_number = 3 + index
        content_object_number = 5 + page_count + index
        content_object_numbers.append(content_object_number)
        objects.append(
            (
                f"{page_number} 0 obj << /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R "
                f"/F2 6 0 R >> >> /Contents {content_object_number} 0 R >> endobj\n"
            ).encode("ascii")
        )
    objects.append(
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    )
    objects.append(
        b"6 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >> endobj\n"
    )
    for index, content in enumerate(contents):
        objects.append(
            (
                f"{content_object_numbers[index]} 0 obj << /Length "
                f"{len(content)} >> stream\n".encode("ascii")
                + content
                + b"\nendstream endobj\n"
            )
        )
    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf += obj
    xref_start = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(pdf)
    return path


def build_text_pdf(
    path: Path,
    lines: list[str],
    *,
    width_pt: float,
    height_pt: float,
    margin_top_pt: float,
    margin_left_pt: float,
    margin_bottom_pt: float,
    margin_right_pt: float,
    font_size_pt: float = 9.0,
    line_height_pt: float = 12.0,
) -> Path:
    """Write a valid single-page text PDF with the given lines.

    Text is placed inside the provided page margins so deterministic
    structural validation (dimensions, content-within-margins, non-empty,
    headings) passes for a genuinely valid proof render.
    """
    return build_text_pdf_pages(
        path,
        lines,
        width_pt=width_pt,
        height_pt=height_pt,
        margin_top_pt=margin_top_pt,
        margin_left_pt=margin_left_pt,
        margin_bottom_pt=margin_bottom_pt,
        margin_right_pt=margin_right_pt,
        font_size_pt=font_size_pt,
        line_height_pt=line_height_pt,
    )


def _wrap_line(
    line: str,
    *,
    max_chars: int,
) -> list[str]:
    """Wrap a line into chunks that fit the available text width without
    splitting ``SYNTHETIC_*`` tokens (words are kept whole), mirroring how a
    real renderer wraps paragraphs while keeping marker multiplicity intact."""
    if max_chars <= 0 or len(line) <= max_chars:
        return [line]
    words = line.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return chunks


def build_text_pdf_pages(
    path: Path,
    lines: list[str],
    *,
    width_pt: float,
    height_pt: float,
    margin_top_pt: float,
    margin_left_pt: float,
    margin_bottom_pt: float,
    margin_right_pt: float,
    header_lines: tuple[str, ...] = (),
    footer_lines: tuple[str, ...] = (),
    font_size_pt: float = 9.0,
    line_height_pt: float = 12.0,
) -> Path:
    """Write a valid multi-page text PDF with the given lines.

    Lines are chunked onto as many pages as needed, with each page's text
    placed inside the provided margins and long lines wrapped to the available
    width. ``header_lines`` are placed in the top strip (above the body top
    margin) of the first page; ``footer_lines`` in the bottom strip (below the
    body bottom margin) of the last page — mirroring declared header/footer
    regions so region-aware validation can be exercised.
    """
    available_height = height_pt - margin_top_pt - margin_bottom_pt - 16.0
    lines_per_page = max(1, int(available_height / line_height_pt))
    available_width = width_pt - margin_left_pt - margin_right_pt - 12.0
    max_chars = max(1, int(available_width / (font_size_pt * 0.75)))
    wrapped_lines: list[str] = []
    for line in lines:
        wrapped_lines.extend(_wrap_line(line, max_chars=max_chars))
    page_operations: list[list[str]] = []
    for start in range(0, len(wrapped_lines), lines_per_page):
        chunk = wrapped_lines[start : start + lines_per_page]
        operations: list[str] = []
        baseline_y = height_pt - margin_top_pt - 6.0 - font_size_pt
        for line in chunk:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            operations.append(
                f"BT 0 0 0 rg /F1 {font_size_pt} Tf "
                f"{margin_left_pt + 6.0:.1f} {baseline_y:.1f} Td ({escaped}) Tj ET"
            )
            baseline_y -= line_height_pt
        page_operations.append(operations)
    if not page_operations:
        page_operations.append([])
    # Header strip on the first page (top of the page, above the body margin).
    if header_lines:
        header_ops: list[str] = []
        baseline_y = height_pt - 8.0 - font_size_pt
        for line in header_lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            header_ops.append(
                f"BT 0 0 0 rg /F1 {font_size_pt} Tf "
                f"{margin_left_pt + 6.0:.1f} {baseline_y:.1f} Td ({escaped}) Tj ET"
            )
            baseline_y -= line_height_pt
        page_operations[0] = header_ops + page_operations[0]
    # Footer strip on the last page (bottom of the page, below the body margin).
    if footer_lines:
        footer_ops: list[str] = []
        baseline_y = 10.0 + font_size_pt
        for line in footer_lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            footer_ops.append(
                f"BT 0 0 0 rg /F1 {font_size_pt} Tf "
                f"{margin_left_pt + 6.0:.1f} {baseline_y:.1f} Td ({escaped}) Tj ET"
            )
            baseline_y += line_height_pt
        page_operations[-1] = page_operations[-1] + footer_ops
    return _write_multi_page_text_pdf(
        path,
        page_operations,
        width_pt=width_pt,
        height_pt=height_pt,
    )


def _write_multi_page_text_pdf(
    path: Path,
    page_operations: list[list[str]],
    *,
    width_pt: float,
    height_pt: float,
) -> Path:
    """Write a multi-page text PDF at the given page size (Helvetica)."""
    contents = ["\n".join(ops).encode("ascii") for ops in page_operations]
    page_count = len(contents)
    kids = " ".join(f"{3 + index} 0 R" for index in range(page_count))
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        (
            f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {page_count} >> endobj\n"
        ).encode("ascii"),
    ]
    content_object_numbers: list[int] = []
    for index in range(page_count):
        page_number = 3 + index
        content_object_number = 5 + page_count + index
        content_object_numbers.append(content_object_number)
        objects.append(
            (
                f"{page_number} 0 obj << /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {width_pt:.2f} {height_pt:.2f}] "
                f"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> "
                f"/Contents {content_object_number} 0 R >> endobj\n"
            ).encode("ascii")
        )
    objects.append(
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    )
    objects.append(
        b"6 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >> endobj\n"
    )
    for index, content in enumerate(contents):
        objects.append(
            (
                f"{content_object_numbers[index]} 0 obj << /Length "
                f"{len(content)} >> stream\n".encode("ascii")
                + content
                + b"\nendstream endobj\n"
            )
        )
    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf += obj
    xref_start = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(pdf)
    return path



def _write_single_page_pdf(
    path: Path,
    operations: list[str],
    *,
    regular_font: str = "Helvetica",
    bold_font: str = "Helvetica-Bold",
    width_pt: float = 612,
    height_pt: float = 792,
    media_box: str | None = None,
) -> Path:
    """Write a single-page text PDF, optionally declaring an exact `/MediaBox`."""
    content = "\n".join(operations).encode("ascii")
    box = media_box or f"0 0 {width_pt:g} {height_pt:g}"
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        (
            f"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [{box}] "
            "/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >> endobj\n"
        ).encode("ascii"),
        (
            b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /"
            + regular_font.encode("ascii")
            + b" >> endobj\n"
        ),
        (
            b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /"
            + bold_font.encode("ascii")
            + b" >> endobj\n"
        ),
        (
            b"6 0 obj << /Length "
            + str(len(content)).encode("ascii")
            + b" >> stream\n"
            + content
            + b"\nendstream endobj\n"
        ),
    ]
    pdf = b"%PDF-1.4\n"
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf += obj
    xref_start = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(pdf)
    return path
