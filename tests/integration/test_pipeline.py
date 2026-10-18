"""The retained pipeline runs from explicit inputs, without experiment imports."""

import json
import subprocess
import sys
from pathlib import Path

import pdfplumber
import pytest
from reportlab.pdfbase.pdfmetrics import stringWidth

from app.pipeline.candidate_content import CandidateDocument, CandidateLeaf, CandidateSection
from app.pipeline.evidence import TargetStructureDraft, SelfReportedStatus
from app.pipeline.service import run_e5
from app.template_analysis.commercial.models import NormalizedLayoutEvidence, NormalizedPage, NormalizedBox, NormalizedTextBlock, NormalizedRule, MeasurementProvenance
from tests.helpers.synthetic_pdf import _write_single_page_pdf


pytestmark = pytest.mark.integration


def _inputs(tmp_path: Path) -> dict:
    operations = ["BT /F2 20 Tf 180 740 Td (Target Person) Tj ET"]
    rules = []
    for heading, y in (("SUMMARY", 650), ("EXPERIENCE", 550)):
        x = 140 - stringWidth(heading, "Helvetica-Bold", 12)
        operations.extend([
            f"BT /F2 12 Tf {x} {y} Td ({heading}) Tj ET",
            f"BT /F1 10 Tf 180 {y - 25} Td (Target facts only) Tj ET",
            f"0.5 w 180 {y - 5} m 560 {y - 5} l S",
        ])
        rules.append(NormalizedRule(
            element_id=f"rule.{heading.lower()}", page_number=1,
            bbox=NormalizedBox(x0=180 / 612, x1=560 / 612,
                               top=(792 - y + 5) / 792, bottom=(792 - y + 5) / 792),
            stroke_width_pt=0.5, color_hex="#000000",
            provenance=MeasurementProvenance(source="local_pdf", provider="synthetic", method="fixture"),
        ))
    target = _write_single_page_pdf(tmp_path / "target.pdf", operations,
                                    regular_font="Helvetica", bold_font="Helvetica-Bold")
    blocks = []
    elements = []
    with pdfplumber.open(target) as pdf:
        words = pdf.pages[0].extract_words(extra_attrs=["fontname", "size"])
    for index, word in enumerate(words):
        bold = "Bold" in word["fontname"]
        blocks.append(NormalizedTextBlock(
            element_id=f"adobe.page.1.element.{index}", text=word["text"], page_number=1,
            reading_order=index,
            bbox=NormalizedBox(x0=word["x0"] / 612, x1=word["x1"] / 612,
                               top=word["top"] / 792, bottom=word["bottom"] / 792),
            font_family="Helvetica", font_size_pt=word["size"], bold=bold,
            line_height_pt=word["size"] * 1.2,
            color_hex="#000000",
        ))
        elements.append({"Path": f"//Document/Sect/P[{index}]", "Text": word["text"],
                         "Page": 0, "ObjectID": index,
                         "Bounds": [word["x0"], 792 - word["bottom"], word["x1"], 792 - word["top"]]})
    raw = tmp_path / "adobe.json"
    raw.write_text(json.dumps({"structured_data": {
        "pages": [{"page_number": 0, "width": 612, "height": 792}], "elements": elements,
    }}))
    normalized = tmp_path / "normalized.json"
    normalized.write_text(NormalizedLayoutEvidence(
        provider="synthetic", page_count=1,
        pages=[NormalizedPage(page_number=1, width_pt=612, height_pt=792)],
        full_text="\n".join(word["text"] for word in words), text_blocks=blocks, rules=rules,
    ).model_dump_json())
    draft = tmp_path / "draft.json"
    draft.write_text(TargetStructureDraft(
        target_id="target-target-v1", investigator="scripted",
        self_reported=SelfReportedStatus(status="ok"),
    ).model_dump_json())
    return {"target_pdf": target, "adobe_json": raw,
            "normalized_evidence": normalized, "structure_draft": draft}


def _candidate(name: str) -> CandidateDocument:
    return CandidateDocument(
        candidate_id="reviewed-candidate",
        leaves=[CandidateLeaf(leaf_id="header.name", kind="header_field", slot="name", text=name),
                CandidateLeaf(leaf_id="summary.item", kind="summary_paragraph", source="summary",
                              text="Builds reliable systems")],
        sections=[CandidateSection(section_id="summary", source="summary", content_kind="paragraph",
                                   leaf_ids=["summary.item"])],
    )


@pytest.mark.parametrize("name", ["Candidate Alpha", "Candidate Beta"])
def test_cli_uses_explicit_content_without_experiment_imports(tmp_path: Path, name: str) -> None:
    inputs = _inputs(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(_candidate(name).model_dump_json())
    out_dir = tmp_path / "result"
    # A forbidden import must fail even when the checkout still contains the old files.
    script = """
import sys, runpy
class NoExperiments:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'tests' or fullname.startswith('tests.'):
            raise AssertionError('runtime imported test code: ' + fullname)
sys.meta_path.insert(0, NoExperiments())
sys.argv = ['pipeline', *sys.argv[1:]]
runpy.run_module('app.pipeline', run_name='__main__')
"""
    command = [sys.executable, "-B", "-c", script,
               "--target", str(inputs["target_pdf"]), "--candidate", str(candidate_path),
               "--adobe-json", str(inputs["adobe_json"]), "--normalized", str(inputs["normalized_evidence"]),
               "--structure-draft", str(inputs["structure_draft"]), "--out", str(out_dir), "--lane", "b"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (result.stdout + result.stderr +
                                   (out_dir / "e5_state.json").read_text())
    state = json.loads((out_dir / "e5_state.json").read_text())
    assert state["summary"]["terminal_state"] == "ready_for_owner_review"
    with pdfplumber.open(out_dir / "lane_b/render_1.pdf") as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert name in text and "Builds reliable systems" in text
    assert "Target Person" not in text and "Target facts only" not in text
    assert state["lanes"]["b"]["summary"]["probe_mode"] == "not_requested"
    assert state["lanes"]["b"]["content_shape_probes_passed"] is False


@pytest.mark.parametrize("field", ["adobe_json", "normalized_evidence", "structure_draft"])
def test_malformed_input_aborts_before_building(tmp_path: Path, monkeypatch, field: str) -> None:
    inputs = _inputs(tmp_path)
    inputs[field].write_text("not valid JSON")
    def unexpected_build(*args, **kwargs):
        pytest.fail("invalid inputs reached layout compilation")
    monkeypatch.setattr("app.pipeline.service.compile_two_column_state", unexpected_build)
    _out, terminal, record = run_e5(**inputs, out_dir=tmp_path / "result", candidate=_candidate("Candidate Alpha"))
    assert terminal == "operational_abort"
    assert record["summary"]["abort"]["operation"] == "pipeline_input"


@pytest.mark.parametrize("options", [{"lanes": ("a", "a")}, {"probe_candidates": {"../escape": None}}])
def test_invalid_dispatch_cannot_create_output_files(tmp_path: Path, options: dict) -> None:
    out_dir = tmp_path / "result"
    with pytest.raises(ValueError):
        run_e5(tmp_path / "unused.pdf", out_dir,
               candidate=_candidate("Candidate Alpha"), adobe_json=tmp_path / "raw.json",
               normalized_evidence=tmp_path / "normalized.json", structure_draft=tmp_path / "draft.json",
               **options)
    assert not out_dir.exists()
