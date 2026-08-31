"""Offline tests for the design evaluation lane.

These tests never call Claude or any other external provider. The live
claude_designer adapter is verified to be gated (skipped without --live,
not_configured without credentials) but is never executed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.commercial_api import adapters as adapter_module  # noqa: F401 - registers adapters
from tests.commercial_api.corpus import (
    cases_for_lane,
    load_corpus_manifest,
    materialize_case_input,
    sha256_file,
)
from tests.commercial_api.design_lane import candidate_sections_from_case
from tests.commercial_api.registry import (
    AdapterOutcome,
    lookup,
    resolve_adapter,
)
from tests.commercial_api.run import Runner, _provider_sort_key
from tests.commercial_api.scoring import evaluate_design


def test_design_adapters_registered_with_correct_capabilities() -> None:
    mock = resolve_adapter("design", "mock_designer")
    caps = mock.capabilities()
    assert caps.execution == "local"
    assert caps.lanes == ["design"]
    assert caps.required_config == []
    claude = resolve_adapter("design", "claude_designer")
    caps = caps = claude.capabilities()
    assert caps.execution == "external"
    assert caps.lanes == ["design"]
    assert caps.provider == "claude_designer"
    assert caps.pricing_key == "claude_designer"


def test_design_cases_declared_in_corpus() -> None:
    manifest = load_corpus_manifest()
    design_cases = cases_for_lane(manifest, "design")
    assert len(design_cases) >= 10
    expected_ids = {
        "design_plain_one_column",
        "design_styled_two_column",
        "design_ambiguous_labels",
        "design_unknown_target_slot",
        "design_additional_section",
        "design_missing_target_data",
        "design_unsupported_graphical",
        "design_contamination_probe",
        "design_invalid_reference_probe",
        "design_low_confidence_mapping",
    }
    assert expected_ids <= {case.case_id for case in design_cases}


def test_design_cases_materialize_with_pinned_checksums(tmp_path: Path) -> None:
    manifest = load_corpus_manifest()
    for case in cases_for_lane(manifest, "design"):
        path = materialize_case_input(case, tmp_path)
        assert path.is_file()
        assert case.expected_sha256
        assert sha256_file(path) == case.expected_sha256


def test_every_design_case_declares_candidate_sections() -> None:
    manifest = load_corpus_manifest()
    for case in cases_for_lane(manifest, "design"):
        sections = candidate_sections_from_case(case)
        assert sections, f"{case.case_id} declares no candidate sections"
        assert all(section.role and section.item_count >= 0 for section in sections)


def test_mock_designer_is_offline_and_accounts_for_every_section(tmp_path: Path) -> None:
    runner = Runner(
        env_file=tmp_path / "missing.env",
        output_root=tmp_path / "out",
        live=False,
    )
    run_dir = runner.run(lanes={"design"}, providers={"mock_designer"})
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = manifest["results"]
    assert len(results) == 10
    # The offline designer is now a safety baseline, not a semantic-label
    # oracle: corpus mapping scores may fail, but it never calls a provider and
    # every source section is explicitly mapped or preserved.
    assert all(result["usage"]["transactions"] == 1 for result in results)
    assert all(result["attempts"][0]["status"] == "passed" for result in results)
    for result in results:
        normalized = next(
            (artifact for artifact in result.get("artifacts", []) if artifact["kind"] == "normalized"),
            None,
        )
        if normalized is None:
            continue
        payload = json.loads((run_dir / normalized["path"]).read_text(encoding="utf-8"))
        proposal = payload["proposal"]
        roles = {mapping["source_role"] for mapping in proposal["mappings"]}
        expected = {
            section["role"]
            for section in next(
                case for case in load_corpus_manifest().cases
                if case.case_id == result["case_id"]
            ).expected_layout.get("candidate_sections", [])
        }
        assert roles == expected


def test_mock_designer_runs_first_in_provider_order() -> None:
    providers = ["claude_designer", "mock_designer"]
    ordered = sorted(providers, key=lambda p: _provider_sort_key("design", p))
    assert ordered[0] == "mock_designer"


def test_claude_designer_skipped_without_live(tmp_path: Path) -> None:
    runner = Runner(
        env_file=tmp_path / "missing.env",
        output_root=tmp_path / "out",
        live=False,
    )
    run_dir = runner.run(lanes={"design"}, providers={"claude_designer"}, cases={"design_plain_one_column"})
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    result = manifest["results"][0]
    assert result["status"] in {"skipped", "not_configured"}
    if result["status"] == "skipped":
        assert result["error_code"] == "live_calls_disabled"
    # A skipped or unconfigured provider is never a pass.
    assert result["status"] != "passed"


def test_claude_designer_never_executed_during_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards against accidental live Claude calls in the offline suite.

    The Anthropic client class itself is replaced so that any attempt to
    construct a live client — regardless of environment, cached settings, or
    future refactors — raises instead of touching the network.
    """

    class _ExplodingClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "Refusing to construct an Anthropic client during offline pytest."
            )

    monkeypatch.setattr("anthropic.Anthropic", _ExplodingClient)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_DESIGN_API_KEY", raising=False)
    monkeypatch.delenv("TEMPLATE_DESIGN_LIVE_ENABLED", raising=False)
    runner = Runner(
        env_file=tmp_path / "missing.env",
        output_root=tmp_path / "out",
        live=True,  # even with --live, missing credentials must not call out
    )
    run_dir = runner.run(lanes={"design"}, providers={"claude_designer"}, cases={"design_plain_one_column"})
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    result = manifest["results"][0]
    assert result["status"] == "not_configured"


def test_mock_designer_outcome_normalized_is_redaction_safe(tmp_path: Path) -> None:
    from tests.commercial_api.redaction import assert_no_secrets

    manifest = load_corpus_manifest()
    case = next(c for c in cases_for_lane(manifest, "design") if c.case_id == "design_contamination_probe")
    case_input = materialize_case_input(case, tmp_path / "inputs")
    adapter = resolve_adapter("design", "mock_designer")
    adapter.set_case(case)
    outcome: AdapterOutcome = adapter.run(case_input, tmp_path / "artifacts")
    assert_no_secrets(outcome.normalized)
    assert not outcome.normalized["target_fact_contamination"]
    assert outcome.normalized["support_state"] == "ready"


def test_evaluate_design_gates_are_independent() -> None:
    manifest = load_corpus_manifest()
    case = next(c for c in cases_for_lane(manifest, "design") if c.case_id == "design_plain_one_column")
    normalized = {
        "support_state": "ready",
        "proposal": {
            "layout_class": "one_column",
            "mappings": [
                {
                    "source_role": "summary",
                    "target_label": "Professional Summary",
                    "action": "map",
                },
                {
                    "source_role": "skills",
                    "target_label": "Core Qualifications",
                    "action": "map",
                },
                {
                    "source_role": "work_experience",
                    "target_label": "Experience",
                    "action": "map",
                },
                {
                    "source_role": "education",
                    "target_label": "Education",
                    "action": "map",
                },
                {
                    "source_role": "contact",
                    "target_label": None,
                    "action": "preserve_as_additional",
                },
                {
                    "source_role": "languages",
                    "target_label": None,
                    "action": "preserve_as_additional",
                },
                {
                    "source_role": "certifications",
                    "target_label": None,
                    "action": "preserve_as_additional",
                },
            ],
        },
        "target_fact_contamination": [],
        "invalid_references": [],
        "review_flags": [],
        "layout_spec_compiled": True,
    }
    metrics, gates = evaluate_design(normalized, case)
    gate_map = {gate.gate: gate.outcome for gate in gates}
    assert gate_map == {
        "mapping_accuracy": "pass",
        "unsupported_content_preservation": "pass",
        "fact_contamination": "pass",
        "invalid_reference_rate": "pass",
        "review_escalation": "pass",
        "compiler_acceptance": "pass",
    }
    # Each gate is independent: contamination alone must flip only its gate.
    contaminated = {**normalized, "target_fact_contamination": ["alex example"]}
    _metrics, contaminated_gates = evaluate_design(contaminated, case)
    contaminated_map = {gate.gate: gate.outcome for gate in contaminated_gates}
    assert contaminated_map["fact_contamination"] == "fail"
    assert contaminated_map["mapping_accuracy"] == "pass"


def test_design_scoring_never_aggregates_to_one_score() -> None:
    manifest = load_corpus_manifest()
    case = next(c for c in cases_for_lane(manifest, "design") if c.case_id == "design_plain_one_column")
    normalized = {
        "support_state": "ready",
        "proposal": {"layout_class": "one_column", "mappings": []},
        "target_fact_contamination": [],
        "invalid_references": [],
        "review_flags": [],
        "layout_spec_compiled": True,
    }
    metrics, gates = evaluate_design(normalized, case)
    assert not any(key in metrics for key in ("total_score", "overall", "aggregate"))
    assert len(gates) == 6
