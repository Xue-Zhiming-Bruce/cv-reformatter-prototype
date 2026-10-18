"""The retained pipeline runs from explicit inputs, without experiment imports."""

import json
import subprocess
import sys
from pathlib import Path

import pdfplumber
import pytest

from app.pipeline.service import run_e5
from tests.helpers.pipeline import synthetic_pipeline_inputs as _inputs, synthetic_pipeline_candidate as _candidate


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("name", ["Candidate Alpha", "Candidate Beta"])
def test_cli_uses_explicit_content_without_test_dependencies(tmp_path: Path, name: str) -> None:
    inputs = _inputs(tmp_path)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(_candidate(name).model_dump_json())
    out_dir = tmp_path / "result"
    # Production must stay independent of repository test fixtures.
    script = """
import sys, runpy
class RuntimeDependencies:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'tests', 'reportlab', 'aspose', 'apryse_sdk'}:
            raise AssertionError('runtime imported a test-only dependency: ' + fullname)
sys.meta_path.insert(0, RuntimeDependencies())
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
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["pipeline_phase"] == "e5"
    assert manifest["versions"] and all(item["sha256"] for item in manifest["versions"])
    assert manifest["state_history"][-1]["state"] == "ready_for_owner_review"
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
