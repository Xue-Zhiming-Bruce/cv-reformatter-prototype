"""Failed/not-run matrix cases remain inspectable without fabricated outputs."""
import csv
import json
from pathlib import Path

from scripts import run_abc_live_matrix as matrix
from tests.helpers.synthetic_pdf import build_styled_target_pdf


def test_e_review_retains_failed_and_unrun_pairs(tmp_path: Path, monkeypatch) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for name in ("A", "B", "C"):
        build_styled_target_pdf(inputs / f"resume_{name}.pdf")
    monkeypatch.setattr(matrix, "DATASET_DIR", inputs)
    output = tmp_path / "review"
    output.mkdir()
    matrix._write_e_review(output, {
        "pairs_requested": ["A:B", "A:C"], "llm_provider": "openai",
        "started_at": "2026-10-12T00:00:00Z", "completed_at": "2026-10-12T00:01:00Z",
        "llm_calls": {"extraction_provider": "openai", "extraction_model": "synthetic-model", "pipeline_request_count": 0},
        "pairs": [{"pair": "A→B", "steps": {"generate": {"status": "fail", "error_code": "pipeline_e_preview_blocked", "message": "Missing candidate values"}}}],
    })
    manifest = json.loads((output / "run_manifest.json").read_text())
    rows = list(csv.DictReader((output / "transformation_matrix.csv").open()))
    assert manifest["llm_calls"] == {"extraction_provider": "openai", "extraction_model": "synthetic-model", "pipeline_request_count": 0}
    assert len(rows) == len(manifest["transformations"]) == 2
    assert [row["status"] for row in rows] == ["failed", "failed"]
    assert rows[0]["error_message"] == "Missing candidate values"
    assert rows[1]["error_message"] == "Not run"
    assert all(row["generated_pdf"] == "" for row in rows)
    assert not list((output / "transformations").rglob("*.pdf"))
    assert json.loads((output / "content_preservation_verification.json").read_text())["passed"] == 0
    assert (output / "transformations/A__to__B/result.json").exists()
    assert (output / "transformations/A__to__C/result.json").exists()
    matrix._write_e_review(output, {
        "pairs_requested": ["A:B"], "pairs": [], "llm_provider": "openai",
        "started_at": "2026-10-12T00:00:00Z",
    })
    assert json.loads((output / "run_manifest.json").read_text())["state"] == "running"
