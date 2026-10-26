"""Repository-wide offline provider guardrails."""

import pytest

from app import main
from tests.helpers.adobe_evidence import mock_run_adobe_layout


@pytest.fixture(autouse=True)
def _offline_adobe_target_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """API tests exercise Adobe-shaped evidence without external calls."""
    monkeypatch.setattr(main, "run_adobe_layout", mock_run_adobe_layout)
    monkeypatch.setattr(main, "enrich_colors_from_local_pdf", lambda evidence, _path: evidence)
    monkeypatch.setattr(main, "enrich_badges_from_local_pdf", lambda evidence, _path: evidence)
    monkeypatch.setattr(main, "enrich_rules_from_local_pdf", lambda evidence, _path: evidence)


@pytest.fixture
def a_generation_without_model_loop(monkeypatch: pytest.MonkeyPatch):
    """Lifecycle checks use real A HTML; complete-loop tests restore the adapter.

    Approval/revision/checksum validation and production file staging stay real.
    These cases do not assert model-loop behavior, execution provenance or
    exported layout fidelity. Native manifests still use the production writer.
    """
    from app.generation import agentic, chrome_html_to_pdf
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.html_renderer import render_layout_template
    from pathlib import Path
    import hashlib
    from pypdf import PdfReader

    original = agentic.render_agentic
    original_manifest_writer = main.write_artifact_manifest
    monkeypatch.setattr(main, "write_artifact_manifest", lambda *args, **kwargs:
        None if kwargs["uses_agentic"] else original_manifest_writer(*args, **kwargs))
    monkeypatch.setenv("GENERATION_STRATEGY", "agentic")

    def render(*, context, layout_spec, target_pdf, evidence_dir, output_dir):
        _, _, html = render_layout_template(layout_spec,
            candidate_document_from_context(context, candidate_id="offline-lifecycle",
                include_subheading=layout_spec.header.show_candidate_subheading),
            target_sha256=hashlib.sha256(target_pdf.read_bytes()).hexdigest(),
            page_count=len(PdfReader(target_pdf).pages))
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=False)
        source = directory / "render.html"
        source.write_text(html, encoding="utf-8")
        pdf = chrome_html_to_pdf.export_html_to_pdf(source, directory / "render.pdf")
        return source, pdf, {"test_scope": "A compilation/export; model loop stubbed"}

    monkeypatch.setattr(agentic, "render_agentic", render)
    return original
