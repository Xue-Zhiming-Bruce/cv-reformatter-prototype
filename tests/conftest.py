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
