from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = PROJECT_ROOT / "tests"


def test_no_automated_tests_live_at_tests_root() -> None:
    root_tests = sorted(path.name for path in TESTS_ROOT.glob("test_*.py"))
    assert root_tests == [], (
        "Root-level tests create an unowned parallel structure. Classify them "
        f"under unit/, integration/, live/, or commercial_api/tests/: {root_tests}"
    )


def test_current_test_source_packages_do_not_contain_generated_outputs() -> None:
    forbidden = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for source_root in (
            TESTS_ROOT / "unit",
            TESTS_ROOT / "integration",
            TESTS_ROOT / "live",
            TESTS_ROOT / "helpers",
            TESTS_ROOT / "commercial_api",
        )
        for path in source_root.rglob("outputs")
        if path.is_dir()
    ]
    assert forbidden == [], (
        "Generated results belong in tests/test_results, not test source: "
        f"{forbidden}"
    )


def test_legacy_commercial_bakeoff_has_no_current_python_source() -> None:
    legacy_root = TESTS_ROOT / "commercial_bakeoff"
    source_files = sorted(path.name for path in legacy_root.glob("*.py"))
    assert source_files == []


def test_normalized_commercial_evidence_is_production_owned() -> None:
    from app.template_analysis.commercial import (
        NormalizedLayoutEvidence,
        normalize_adobe_layout,
    )

    assert NormalizedLayoutEvidence.__module__.startswith(
        "app.template_analysis.commercial"
    )
    assert normalize_adobe_layout.__module__.startswith(
        "app.template_analysis.commercial"
    )


def test_uploaded_target_generation_has_no_v1_spec_reader_or_migration() -> None:
    main_source = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    generation_source = main_source[
        main_source.index("def generate_outputs(") : main_source.index(
            '@app.post("/api/followup"'
        )
    ]
    assert "load_template_style_spec" not in generation_source
    assert "migrate_template_style_spec" not in generation_source
    assert "STYLE_SPEC_FILENAME" not in generation_source
    assert "load_layout_template_spec" in generation_source
    assert "structure_contract" in generation_source


def test_no_semantic_classification_logic_in_app_extraction() -> None:
    """ADR 0005 single-path guard: the deterministic semantic-classification
    machinery (alias tables, heading-shape gates, section classifier) must not
    reappear in app/extraction/."""

    forbidden = re.compile(
        r"aliases\s*=|_CANONICAL_SECTION_ALIASES|_OPTIONAL_SECTION_ALIASES|"
        r"_NON_HEADING_PATTERNS|_looks_like_heading|_follows_content_line|"
        r"_classify_heading|SectionClassifier|LLMSectionClassifier|"
        r"SectionClassificationOutput|SectionClassificationError|SegmentationStats|alias_hits"
    )
    extraction_root = PROJECT_ROOT / "app" / "extraction"
    offenders = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in extraction_root.glob("*.py")
        if forbidden.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "Deterministic semantic-classification machinery must not reappear in "
        f"app/extraction/ (ADR 0005 single path): {offenders}"
    )
