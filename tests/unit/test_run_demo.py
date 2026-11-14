import json
import sys
from pathlib import Path

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from pytest import MonkeyPatch

from scripts import run_demo


def test_demo_forwards_unread_source_content_to_its_coverage_audit(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The demo audits the same way the API does: a gap keeps it off passed."""
    monkeypatch.setattr(run_demo, "OUTPUT_DIR", tmp_path / "output")
    resume_path = tmp_path / "resume.docx"
    document = Document()
    document.add_paragraph("Jane Candidate")
    document.add_paragraph("jane@example.com")
    phone = document.add_paragraph("Phone: ")
    phone._p.append(
        parse_xml(
            f'<w:sdt {nsdecls("w")}><w:sdtContent>'
            "<w:r><w:t>555 0199</w:t></w:r></w:sdtContent></w:sdt>"
        )
    )
    document.save(resume_path)
    monkeypatch.setattr(
        sys, "argv", ["run_demo", "--input", str(resume_path), "--mock"]
    )

    run_demo.main()

    audit = json.loads(
        (run_demo.OUTPUT_DIR / "segmentation_coverage.json").read_text(encoding="utf-8")
    )
    assert audit["status"] == "incomplete"
    assert len(audit["unread_source_content"]) == 1
    assert "content control" in audit["unread_source_content"][0]
    assert "555 0199" not in (
        run_demo.OUTPUT_DIR / "raw_extracted_text.txt"
    ).read_text(encoding="utf-8")
