"""Hand-written PDF probes for badge classification.

Each builder spells out the page operators, so the matching test's expectation
comes from the geometry it asked for and never from the reader's own output.
"""

from __future__ import annotations

from pathlib import Path

from tests.helpers.synthetic_pdf import _pill_path, _write_single_page_pdf

CHIP_FILL = "0.6 0.96471 0.89412"  # #99F6E4
CHIP_TEXT = "0.2 0.25490 0.33333"  # #334155
BANNER_FILL = "0.05098 0.58039 0.53333"  # #0D9488


def build_wide_padded_and_square_chip_pdf(path: Path) -> Path:
    """Three genuine chips in one row that the retired thresholds rejected.

    200x18 pt pill whose 9 pt inset leaves its label filling the box; 100x18 pt
    pill whose label starts 25 pt in, so its padding exceeds its height; 18x18 pt
    pill labelled R, whose rounded square fills pi/4 (79%) of its box.
    """
    operations = [
        f"q {CHIP_FILL} rg\n{_pill_path(40, 600, 200, 18)}\nf Q",
        f"BT {CHIP_TEXT} rg /F1 9 Tf 49 605 Td (Kubernetes Terraform Ansible Helm) Tj ET",
        f"q {CHIP_FILL} rg\n{_pill_path(260, 600, 100, 18)}\nf Q",
        f"BT {CHIP_TEXT} rg /F1 9 Tf 285 605 Td (PostgreSQL) Tj ET",
        f"q {CHIP_FILL} rg\n{_pill_path(420, 600, 18, 18)}\nf Q",
        f"BT {CHIP_TEXT} rg /F1 9 Tf 426 605 Td (R) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)


def build_section_banner_pdf(path: Path) -> Path:
    """Two 200x24 pt column banners labelled EXPERIENCE, label inset 9 pt."""
    operations = [
        f"q {BANNER_FILL} rg\n{_pill_path(66, 700, 200, 24)}\nf Q",
        "BT 1 1 1 rg /F2 10 Tf 75 708 Td (EXPERIENCE) Tj ET",
        f"q {BANNER_FILL} rg\n{_pill_path(66, 640, 200, 24)}\nf Q",
        "BT 1 1 1 rg /F2 10 Tf 75 648 Td (EXPERIENCE) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)


def build_overflowing_chip_pdf(path: Path) -> Path:
    """Two 60x18 pt pills whose label starts 2 pt left of the pill's own edge."""
    operations = [
        f"q {CHIP_FILL} rg\n{_pill_path(66, 600, 60, 18)}\nf Q",
        f"BT {CHIP_TEXT} rg /F1 9 Tf 64 605 Td (Overflow) Tj ET",
        f"q {CHIP_FILL} rg\n{_pill_path(166, 600, 60, 18)}\nf Q",
        f"BT {CHIP_TEXT} rg /F1 9 Tf 164 605 Td (Overflow) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)


def build_deep_label_inset_bars_pdf(path: Path) -> Path:
    """Two 300x24 pt filled bars whose centred label spans 51% of the width.

    That leaves a 73.3 pt inset, so the bars are no containers but sit past the
    72 pt badge padding ceiling that the cluster schema bounds.
    """
    operations = [
        f"q {BANNER_FILL} rg\n66 600 300 24 re f Q",
        "BT 1 1 1 rg /F2 10.5 Tf 139.3 608 Td (PROFESSIONAL EXPERIENCE) Tj ET",
        f"q {BANNER_FILL} rg\n66 540 300 24 re f Q",
        "BT 1 1 1 rg /F2 10.5 Tf 139.3 548 Td (PROFESSIONAL EXPERIENCE) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)


def build_multiword_label_pills_pdf(path: Path) -> Path:
    """Two 160x20 pt pills whose 10 pt "Data Science SQL" label spans 51% of the width.

    The label's ink runs 82.26 pt wide (51.4% of 160 pt), just past the container
    cutoff, while its three word boxes alone add up to 76.7 pt (47.9%).
    """
    operations = [
        f"q {CHIP_FILL} rg\n{_pill_path(66, 600, 160, 20)}\nf Q",
        f"BT {CHIP_TEXT} rg /F1 10 Tf 105 606 Td (Data Science SQL) Tj ET",
        f"q {CHIP_FILL} rg\n{_pill_path(266, 600, 160, 20)}\nf Q",
        f"BT {CHIP_TEXT} rg /F1 10 Tf 305 606 Td (Data Science SQL) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)


def build_two_singleton_pills_pdf(path: Path) -> Path:
    """Two well-formed 80x20 pt pills in different fills: two lone chips, not a row."""
    operations = [
        f"q {CHIP_FILL} rg\n{_pill_path(66, 600, 80, 20)}\nf Q",
        f"BT {CHIP_TEXT} rg /F1 10 Tf 72 606 Td (Python) Tj ET",
        f"q {BANNER_FILL} rg\n{_pill_path(200, 600, 80, 20)}\nf Q",
        "BT 1 1 1 rg /F1 10 Tf 206 606 Td (Go) Tj ET",
    ]
    return _write_single_page_pdf(path, operations)


def build_outlined_chip_pdf(path: Path) -> Path:
    """One 120x20 pt rounded outline painted with a stroke and no fill."""
    operations = [f"{BANNER_FILL} RG 1 w\n{_pill_path(66, 600, 120, 20)}\nS"]
    return _write_single_page_pdf(path, operations)
