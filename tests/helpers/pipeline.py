"""Shared local-corpus fixtures for retained pipeline regression tests."""

from __future__ import annotations

from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]

RESUME_I = ROOT / "tests/local_datasets/resume_matrix/resume_I.pdf"

RESUME_I_RAW = (
    ROOT / "tests/experiments/runs/c2_resume_i_blind_20260917T115551Z/adobe_raw.json"
)

RESUME_I_NORMALIZED = (
    ROOT / "tests/experiments/runs/c2_resume_i_blind_20260917T115551Z/enriched_evidence.json"
)

PIPELINE_E_SOURCE_FILES = ("service.py", "evidence.py", "review.py")

_TARGET_SECTION_TITLES = (
    "CONTACT INFO",
    "ABOUT ME",
    "EXPERIENCE",
    "EDUCATION",
    "ACHIEVEMENTS",
    "PUBLICATIONS",
    "CONFERENCES",
    "SKILLS",
    "REFERENCES",
)

_TARGET_SPECIFIC_LITERALS = (
    *_TARGET_SECTION_TITLES,
    "JOB TITLE",
    "VERSTAPPEN",
)

_APPROVAL_NAME_MARKERS = ("APPROV", "LABEL", "TITLE")

_FORBIDDEN_APPROVAL_NAMES = frozenset(
    {"OWNER_APPROVED_PRESENTATION_LABEL_TEXTS", "_owner_approved_label_text"}
)

def _identifier_names(tree: Any) -> set[str]:
    """Every identifier NAME the module binds or mentions (not string text)."""
    import ast

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name)
    return names

def _scan_source_for_hardcoded_target_data(
    filename: str, source: str
) -> dict[str, list[str]]:
    """Semantic replacement for the old `title not in source` substring scan.

    The substring scan flagged the words "FILE REFERENCES" inside an unrelated
    agent-message-audit docstring as the Resume-I title `REFERENCES`. This
    check looks at what the source DOES with a target-specific literal instead
    of whether the character sequence occurs anywhere:

    - `hardcoded_literal_collections` — a list/tuple/set/dict literal in
      executable data holding TWO OR MORE distinct target literals (a generic
      role-name map that happens to reuse one title word is NOT a target list);
    - `hardcoded_literal_scalars` — a target literal used as a scalar constant
      value (`NAME = "REFERENCES"`);
    - `hardcoded_literal_approvals` — a target literal reachable from a value
      bound to an approval / label / title constant, scalar or collection, e.g.
      `OWNER_APPROVED_... = ("SKILLS",)`;
    - `executable_target_name_literals` — a real string constant naming the
      walkthrough target (comments and docstrings are not code data);
    - `forbidden_names` — a binding using a forbidden approval-constant name.

    Docstrings, comments, log/error prose and f-strings are not executable
    DATA and never match — while a re-introduced hardcoded target list still
    does."""
    import ast

    literals = set(_TARGET_SPECIFIC_LITERALS)
    findings: dict[str, list[str]] = {
        "hardcoded_literal_collections": [],
        "hardcoded_literal_scalars": [],
        "hardcoded_literal_approvals": [],
        "executable_target_name_literals": [],
        "forbidden_names": [],
    }

    def literal_of(node: Any) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            return text if text in literals else None
        return None

    def in_value(value: Any) -> list[str]:
        return sorted({t for sub in ast.walk(value) if (t := literal_of(sub))})

    tree = ast.parse(source, filename=filename)
    if _FORBIDDEN_APPROVAL_NAMES & _identifier_names(tree):
        findings["forbidden_names"].append(filename)
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            hit = sorted({t for element in node.elts if (t := literal_of(element))})
            if len(hit) >= 2:
                findings["hardcoded_literal_collections"].append(
                    f"{filename}:{node.lineno}: {hit}"
                )
        elif isinstance(node, ast.Dict):
            hit = sorted(
                {t for element in (*node.keys, *node.values) if (t := literal_of(element))}
            )
            if len(hit) >= 2:
                findings["hardcoded_literal_collections"].append(
                    f"{filename}:{node.lineno}: {hit}"
                )
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if not names or node.value is None:
                continue
            scalar = literal_of(node.value)
            if scalar is not None:
                findings["hardcoded_literal_scalars"].append(
                    f"{filename}:{node.lineno}: {names[0]} = {scalar!r}"
                )
            claims_approval = any(
                any(marker in name.upper() for marker in _APPROVAL_NAME_MARKERS)
                for name in names
            )
            if claims_approval:
                hit = in_value(node.value)
                if hit:
                    findings["hardcoded_literal_approvals"].append(
                        f"{filename}:{node.lineno}: {hit}"
                    )
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "resume_I" in node.value
        ):
            findings["executable_target_name_literals"].append(
                f"{filename}:{node.lineno}: {node.value[:60]!r}"
            )
    return findings

def _pipeline_e_semantic_findings() -> dict[str, list[str]]:
    """`_scan_source_for_hardcoded_target_data` over every Pipeline E runtime
    module (the pre-split single-file scan surface)."""
    findings: dict[str, list[str]] = {
        "hardcoded_literal_collections": [],
        "hardcoded_literal_scalars": [],
        "hardcoded_literal_approvals": [],
        "executable_target_name_literals": [],
        "forbidden_names": [],
    }
    base = ROOT / "app/pipeline"
    for filename in PIPELINE_E_SOURCE_FILES:
        for key, hits in _scan_source_for_hardcoded_target_data(
            filename, (base / filename).read_text(encoding="utf-8")
        ).items():
            findings[key].extend(hits)
    return findings

TARGET_F = ROOT / "tests/local_datasets/resume_matrix/resume_F.pdf"

TARGET_F_CACHE = (
    ROOT / "tests/experiments/runs/target_cache/6916698b4280b9c244e7d2c5a453e8a48d2bf7edea3419becb9b924eda26603d"
)

def _e2_available() -> bool:
    return TARGET_F.exists() and (TARGET_F_CACHE / "adobe_raw.json").exists()

e2_skip = pytest.mark.skipif(
    not _e2_available(),
    reason="authorized resume_F corpus and its cached Adobe response are not present",
)


def run_pipeline_fixture(target, out_dir, **kwargs):
    """Adapt historical corpus tests to the explicit runtime input boundary."""
    from app.pipeline.service import run_e5, _sha256_file
    from tests.experiments.c2_candidates import candidate_resume_E, independent_candidate_fixtures

    cache = ROOT / "tests/experiments/runs/target_cache" / _sha256_file(target)
    return run_e5(
        target, out_dir,
        candidate=candidate_resume_E(),
        adobe_json=cache / "adobe_raw.json",
        normalized_evidence=cache / "enriched_evidence.json",
        structure_draft=ROOT / "tests/experiments/runs/e_pipeline_e4_20260920T134946Z/structure_draft.json",
        probe_candidates=independent_candidate_fixtures(),
        **kwargs,
    )
