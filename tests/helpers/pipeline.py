"""Synthetic target inputs and source guards shared by pipeline regressions."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import pdfplumber
import pytest
from reportlab.pdfbase.pdfmetrics import stringWidth
from app.pipeline.candidate_content import CandidateDocument, CandidateLeaf, CandidateSection
from app.pipeline.evidence import EvidencePod, TargetStructureDraft, SelfReportedStatus
from app.template_analysis.commercial.models import NormalizedLayoutEvidence, NormalizedPage, NormalizedBox, NormalizedTextBlock, NormalizedRule, MeasurementProvenance
from tests.helpers.synthetic_pdf import _write_single_page_pdf

ROOT = Path(__file__).resolve().parents[2]
LOCAL_INPUTS = ROOT / "tests/local_datasets/resume_matrix"
LOCAL_TARGET = LOCAL_INPUTS / "resume_I.pdf"
local_pipeline = pytest.mark.skipif(
    not all((LOCAL_INPUTS / name).is_file() for name in ("resume_I.pdf", "resume_I.adobe.json", "resume_I.normalized.json", "resume_I.draft.json")),
    reason="explicit local pipeline input bundle is not present (see tests/README.md)",
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



def synthetic_pipeline_inputs(tmp_path: Path, *, one_column: bool = False,
                              page_size: tuple[float, float] = (612, 792),
                              entry_headings: tuple[str, str] | None = None) -> dict:
    width, height = page_size
    body_x = 54 if one_column else 180
    operations = [f"BT /F2 20 Tf {body_x} 740 Td (Target Person) Tj ET"]
    rules = []
    for heading, y in (("SUMMARY", 650), ("SKILLS" if one_column else "EXPERIENCE", 550)):
        x = body_x if one_column else 140 - stringWidth(heading, "Helvetica-Bold", 12)
        operations.extend([
            f"BT /F2 12 Tf {x} {y} Td ({heading}) Tj ET",
            f"BT /F1 10 Tf {body_x} {y - 25} Td (Target facts only) Tj ET",
            *([] if one_column else [f"0.5 w 180 {y - 5} m 560 {y - 5} l S"]),
        ])
        if not one_column:
            rules.append(NormalizedRule(
                element_id=f"rule.{heading.lower()}", page_number=1,
                bbox=NormalizedBox(x0=180 / width, x1=560 / width,
                                   top=(height - y + 5) / height, bottom=(height - y + 5) / height),
                stroke_width_pt=0.5, color_hex="#000000",
                provenance=MeasurementProvenance(source="local_pdf", provider="synthetic", method="fixture"),
            ))
        if heading == "EXPERIENCE" and entry_headings is not None:
            operations[-2] = f"BT /F1 10 Tf {body_x} {y - 25} Td ({entry_headings[0]}) Tj ET"
            operations.append(f"BT /F1 10 Tf {body_x} {y - 65} Td ({entry_headings[1]}) Tj ET")
    target = _write_single_page_pdf(tmp_path / "target.pdf", operations,
                                    regular_font="Helvetica", bold_font="Helvetica-Bold", width_pt=width, height_pt=height)
    return measured_pipeline_inputs(target, tmp_path, rules=rules)


def measured_pipeline_inputs(target: Path, tmp_path: Path, *, rules: list[NormalizedRule] | None = None) -> dict:
    """Measure a single-page synthetic PDF into the canonical test evidence bundle."""
    blocks = []
    elements = []
    with pdfplumber.open(target) as pdf:
        if len(pdf.pages) != 1:
            raise ValueError("synthetic evidence helper requires one page")
        width, height = pdf.pages[0].width, pdf.pages[0].height
        words = pdf.pages[0].extract_words(extra_attrs=["fontname", "size"])
    for index, word in enumerate(words):
        bold = "Bold" in word["fontname"]
        blocks.append(NormalizedTextBlock(
            element_id=f"adobe.page.1.element.{index}", text=word["text"], page_number=1,
            reading_order=index,
            bbox=NormalizedBox(x0=word["x0"] / width, x1=word["x1"] / width,
                               top=word["top"] / height, bottom=word["bottom"] / height),
            font_family="Helvetica", font_size_pt=word["size"], bold=bold,
            line_height_pt=word["size"] * 1.2,
            color_hex="#000000",
        ))
        elements.append({"Path": f"//Document/Sect/P[{index}]", "Text": word["text"],
                         "Page": 0, "ObjectID": index,
                         "Bounds": [word["x0"], height - word["bottom"], word["x1"], height - word["top"]]})
    raw = tmp_path / "adobe.json"
    raw.write_text(json.dumps({"structured_data": {
        "pages": [{"page_number": 0, "width": width, "height": height}], "elements": elements,
    }}))
    normalized = tmp_path / "normalized.json"
    normalized.write_text(NormalizedLayoutEvidence(
        provider="synthetic", page_count=1,
        pages=[NormalizedPage(page_number=1, width_pt=width, height_pt=height)],
        full_text="\n".join(word["text"] for word in words), text_blocks=blocks, rules=rules or [],
    ).model_dump_json())
    draft = tmp_path / "draft.json"
    draft.write_text(TargetStructureDraft(
        target_id="target-target-v1", investigator="scripted",
        self_reported=SelfReportedStatus(status="ok"),
    ).model_dump_json())
    return {"target_pdf": target, "adobe_json": raw,
            "normalized_evidence": normalized, "structure_draft": draft}


def synthetic_pipeline_candidate(name: str) -> CandidateDocument:
    return CandidateDocument(
        candidate_id="reviewed-candidate",
        leaves=[CandidateLeaf(leaf_id="header.name", kind="header_field", slot="name", text=name),
                CandidateLeaf(leaf_id="summary.item", kind="summary_paragraph", source="summary",
                              text="Builds reliable systems")],
        sections=[CandidateSection(section_id="summary", source="summary", content_kind="paragraph",
                                   leaf_ids=["summary.item"])],
    )


def _pod(tmp_path: Path, *, with_normalized: bool = True, raw: dict | None = None):
    inputs = synthetic_pipeline_inputs(tmp_path)
    raw_path = inputs["adobe_json"]
    if raw is not None:
        raw_path.write_text(json.dumps(raw), encoding="utf-8")
    normalized = NormalizedLayoutEvidence.model_validate_json(inputs["normalized_evidence"].read_text()) if with_normalized else None
    return EvidencePod(inputs["target_pdf"], raw_path, tmp_path, normalized=normalized)


def run_pipeline_fixture(target: Path, out_dir: Path, **kwargs):
    """Optional corpus loop checks use explicit inputs, never stored-run caches."""
    from app.pipeline.service import run_e5
    from tests.helpers.pipeline_candidates import candidate_resume_E, independent_candidate_fixtures

    return run_e5(
        target, out_dir, candidate=candidate_resume_E(),
        adobe_json=LOCAL_INPUTS / "resume_I.adobe.json",
        normalized_evidence=LOCAL_INPUTS / "resume_I.normalized.json",
        structure_draft=LOCAL_INPUTS / "resume_I.draft.json",
        probe_candidates=independent_candidate_fixtures(), **kwargs,
    )


def agentic_repair_inputs(tmp_path: Path, *, target_entry_gap: float = 18) -> tuple[dict, Any, Any, Any]:
    """Distinct synthetic target/candidate entries with measurable wrapping."""
    from app.extraction.candidate_schema import CandidateProfile
    from app.generation.chrome_html_to_pdf import export_html_to_pdf
    from app.generation.template_mapper import build_client_render_context
    from app.pipeline import service
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.html_renderer import render_layout_template, render_html
    from tests.helpers.pipeline_layout import product_entry_template

    spec = product_entry_template(split_work=True, map_custom=False)
    spec.header.contact_fields = []
    profile = CandidateProfile(full_name="Candidate Alpha", work_experience=[
        {"company": "FirstCandidateCompany", "title": "Platform Engineer for Candidate Systems", "start_date": "2020", "end_date": "2022",
         "description": ["Built reliable services"]},
        {"company": "SecondCandidateCompany", "title": "Senior Engineer for Candidate Platforms", "start_date": "2022", "end_date": "2024",
         "description": ["Maintained useful systems"]},
    ])
    target_profile = CandidateProfile(full_name="Target Person", work_experience=[
        {"company": "FirstTargetCompany", "title": "Platform Engineer for Target Systems", "start_date": "2014", "end_date": "2016",
         "description": ["Created target demo tools"]},
        {"company": "SecondTargetCompany", "title": "Senior Engineer for Target Platforms", "start_date": "2016", "end_date": "2018",
         "description": ["Supported target demo services"]},
    ])
    target_spec = spec.model_copy(deep=True, update={"source_type": "built_in"})
    target_spec.sections[1].entry_gap_pt = target_entry_gap
    state, plan, _ = render_layout_template(target_spec, candidate_document_from_context(
        build_client_render_context(target_profile), candidate_id="synthetic-target"))
    reference = service.LaneAStructureProposal(proposal_id="synthetic-target-rail", agent="scripted", sections=[
        service.E5LaneASection(section_node_id="section.02", heading_in_rail=True, rail_label_width_pt=80),
    ])
    target_html = tmp_path / "target.html"
    target_html.write_text(render_html(state, service._apply_lane_a_structure(plan, reference)))
    target_pdf = export_html_to_pdf(target_html, tmp_path / "target.pdf")
    inputs = measured_pipeline_inputs(target_pdf, tmp_path)
    return inputs, spec, profile, target_profile
