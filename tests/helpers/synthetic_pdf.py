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


def build_flow_target_pdf(path: Path) -> Path:
    """Write a one-column target with compressed type and a local Highlights grid."""
    operations = [
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 34 760 Td (SYSTEM ADMINISTRATOR) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 34 748 Td (Summary) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 34 724 Td (Engineer supporting reliable infrastructure.) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 34 700 Td (Highlights) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 52 688 Td (Networking) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 52 676 Td (Troubleshooting) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 260 688 Td (AWS) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 260 676 Td (Windows Server) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 34 648 Td (Education) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 34 636 Td (Example University | Information Technology) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 34 608 Td (Experience) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 34 596 Td (System Administrator | Example Company) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 52 584 Td (Managed infrastructure.) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 52 572 Td (Resolved technical issues.) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 34 544 Td (Additional Information) Tj ET",
        "BT 0 0 0 rg /F1 11.4 Tf -0.4 Tc 34 532 Td (Availability to be confirmed.) Tj ET",
    ]
    return _write_single_page_pdf(
        path,
        operations,
        regular_font="Times-Roman",
        bold_font="Times-Bold",
    )


def build_right_aligned_metadata_target_pdf(path: Path) -> Path:
    """Write a one-column resume whose experience dates are right-aligned."""
    operations = [
        "BT 0 0 0 rg /F2 20 Tf 48 752 Td (Alex Example) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 48 730 Td (alex@example.test | Example City) Tj ET",
        "BT 0 0 0 rg /F2 12 Tf 48 696 Td (SUMMARY) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 48 678 Td (Engineer building reliable applications and services.) Tj ET",
        "BT 0 0 0 rg /F2 12 Tf 48 642 Td (EXPERIENCE) Tj ET",
        "BT 0 0 0 rg /F2 10 Tf 48 622 Td (Senior Engineer | Example Systems) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 472 622 Td (2022 - Present) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 60 604 Td (Built APIs and data pipelines for internal teams.) Tj ET",
        "BT 0 0 0 rg /F2 10 Tf 48 574 Td (Software Engineer | Sample Labs) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 478 574 Td (2020 - 2022) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 60 556 Td (Improved deployment reliability and observability.) Tj ET",
        "BT 0 0 0 rg /F2 10 Tf 48 526 Td (Developer | Demo Studio) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 478 526 Td (2018 - 2020) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 60 508 Td (Delivered accessible web interfaces.) Tj ET",
        "BT 0 0 0 rg /F2 10 Tf 48 478 Td (Analyst | Test Company) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 478 478 Td (2016 - 2018) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 60 460 Td (Automated recurring operational reports.) Tj ET",
        "BT 0 0 0 rg /F2 12 Tf 48 424 Td (SKILLS) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 48 406 Td (Python SQL FastAPI Docker Git) Tj ET",
        "BT 0 0 0 rg /F2 12 Tf 48 370 Td (EDUCATION) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 48 352 Td (Example University | Computer Science) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)


def build_position_first_target_pdf(path: Path) -> Path:
    """Write an ATS-style entry with title/date before company/location."""
    operations = [
        "BT 0 0 0 rg /F2 24 Tf 66 752 Td (Alex Example) Tj ET",
        "BT 0 0 0 rg /F1 9 Tf 66 730 Td (alex@example.test | Example City) Tj ET",
        "BT 0 0 0 rg /F2 11 Tf 66 696 Td (EXPERIENCE) Tj ET",
        "BT 0 0 0 rg /F2 11 Tf 66 680 Td (Senior Engineer) Tj ET",
        "BT 0 0 0 rg /F1 9 Tf 460 680 Td (2022 - Present) Tj ET",
        "BT 0 0 0 rg /F2 10 Tf 66 664 Td (Example Systems) Tj ET",
        "BT 0 0 0 rg /F1 9 Tf 480 664 Td (Example City) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 76 648 Td (Delivered reliable applications.) Tj ET",
        "BT 0 0 0 rg /F2 11 Tf 66 620 Td (SKILLS) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 66 604 Td (Python SQL Docker) Tj ET",
        "BT 0 0 0 rg /F2 11 Tf 66 576 Td (EDUCATION) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 66 560 Td (Example University) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)


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


def build_design_plain_target_pdf(path: Path) -> Path:
    """Conventional one-column target with typographically distinct title and
    section headings (for the design lane)."""
    operations = [
        "BT 0 0 0 rg /F2 16 Tf 34 754 Td (ACCOUNTANT) Tj ET",
        "BT 0 0 0 rg /F2 12 Tf 34 728 Td (Professional Summary) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 34 710 Td (Experienced accountant supporting fast-paced business teams.) Tj ET",
        "BT 0 0 0 rg /F2 12 Tf 34 682 Td (Core Qualifications) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 34 664 Td (Excel QuickBooks Accounts Receivable Customer Service) Tj ET",
        "BT 0 0 0 rg /F2 12 Tf 34 636 Td (Experience) Tj ET",
        "BT 0 0 0 rg /F2 10 Tf 34 618 Td (Accountant | Example Company) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 34 600 Td (January 2018 to Present | Example City) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 52 574 Td (Processed accounts receivable payments and reconciliations.) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 52 554 Td (Prepared quarterly sales and commission reports.) Tj ET",
        "BT 0 0 0 rg /F2 12 Tf 34 522 Td (Education) Tj ET",
        "BT 0 0 0 rg /F1 10 Tf 34 504 Td (Example University | Accounting) Tj ET",
    ]
    return _write_single_page_pdf(
        path,
        operations,
        regular_font="Times-Roman",
        bold_font="Times-Bold",
    )


def build_ambiguous_target_pdf(path: Path) -> Path:
    """One-column target whose section labels are ambiguous (multi-alias)."""
    operations = [
        "BT 0 0 0 rg /F2 14 Tf 40 760 Td (SUMMARY & HIGHLIGHTS) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 738 Td (Engineer focused on reliable systems.) Tj ET",
        "BT 0 0 0 rg /F2 14 Tf 40 700 Td (WORK EXPERIENCE) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 678 Td (Engineer | Example Co) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 656 Td (Built and operated services.) Tj ET",
        "BT 0 0 0 rg /F2 14 Tf 40 618 Td (EDUCATION) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 596 Td (Example University | Computer Science) Tj ET",
    ]
    return _write_single_page_pdf(
        path, operations, regular_font="Times-Roman", bold_font="Times-Bold"
    )


def build_unknown_slot_target_pdf(path: Path) -> Path:
    """One-column target that includes a slot with no canonical source."""
    operations = [
        "BT 0 0 0 rg /F2 14 Tf 40 760 Td (PUBLICATIONS) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 738 Td (Selected peer-reviewed publications.) Tj ET",
        "BT 0 0 0 rg /F2 14 Tf 40 700 Td (EXPERIENCE) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 678 Td (Engineer | Example Co) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 656 Td (Delivered services and tooling.) Tj ET",
        "BT 0 0 0 rg /F2 14 Tf 40 618 Td (SKILLS) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 596 Td (Python SQL Docker) Tj ET",
    ]
    return _write_single_page_pdf(
        path, operations, regular_font="Times-Roman", bold_font="Times-Bold"
    )


def build_low_confidence_target_pdf(path: Path) -> Path:
    """One-column target whose primary heading matches multiple aliases."""
    operations = [
        "BT 0 0 0 rg /F2 14 Tf 40 760 Td (EXPERIENCE & EMPLOYMENT HISTORY) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 738 Td (Engineer | Example Co) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 716 Td (Operated production services.) Tj ET",
        "BT 0 0 0 rg /F2 14 Tf 40 678 Td (SKILLS) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 656 Td (Python SQL AWS) Tj ET",
        "BT 0 0 0 rg /F2 14 Tf 40 618 Td (EDUCATION) Tj ET",
        "BT 0 0 0 rg /F1 11 Tf 40 596 Td (Example University | Computer Science) Tj ET",
    ]
    return _write_single_page_pdf(
        path, operations, regular_font="Times-Roman", bold_font="Times-Bold"
    )


def build_graphical_target_pdf(path: Path) -> Path:
    """Brochure-style target with a large embedded image (>30% of page area).
    The design lane must declare this target unsupported."""
    import zlib

    width, height = 500, 320
    raw = bytes([200]) * (width * height * 3)
    compressed = zlib.compress(raw)
    content = (
        "BT 0 0 0 rg /F1 20 Tf 40 750 Td (PORTFOLIO) Tj ET\n"
        "BT 0 0 0 rg /F1 12 Tf 40 720 Td "
        "(Selected client deliverables and visual identity work.) Tj ET\n"
        "q 0.9 0.9 0.9 rg 0 0 500 320 re f Q\n"
        f"q {width} 0 0 {height} 0 0 cm /Im1 Do Q\n"
    ).encode("ascii")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        (
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> /XObject << /Im1 5 0 R >> "
            b">> /Contents 6 0 R >> endobj\n"
        ),
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        (
            b"5 0 obj << /Type /XObject /Subtype /Image /Width 500 /Height 320 "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
            b"/Length "
            + str(len(compressed)).encode("ascii")
            + b" >> stream\n"
            + compressed
            + b"\nendstream endobj\n"
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


def _write_single_page_pdf_with_size(
    path: Path,
    operations: list[str],
    *,
    width_pt: float,
    height_pt: float,
) -> Path:
    """Write a single-page text PDF at the given page size (Helvetica)."""
    content = "\n".join(operations).encode("ascii")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        (
            f"3 0 obj << /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {width_pt:.2f} {height_pt:.2f}] "
            f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
        ).encode("ascii"),
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        (
            f"5 0 obj << /Length {len(content)} >> stream\n".encode("ascii")
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


def _write_single_page_pdf(
    path: Path,
    operations: list[str],
    *,
    regular_font: str = "Helvetica",
    bold_font: str = "Helvetica-Bold",
) -> Path:
    content = "\n".join(operations).encode("ascii")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        (
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >> endobj\n"
        ),
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
