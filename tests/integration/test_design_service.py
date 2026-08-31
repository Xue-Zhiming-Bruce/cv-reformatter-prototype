"""Design service: strategy selection, caching, approval, and failure states."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.extraction.candidate_schema import CandidateProfile
from app.template_analysis.design_cache import DesignCache, DesignCacheKey
from app.template_analysis.design_evidence import (
    build_design_request,
    build_target_layout_evidence,
    candidate_sections_from_profile,
    inventory_sha256,
    target_checksum_from_bytes,
)
from app.template_analysis.design_service import (
    DESIGN_STRATEGY_CLAUDE,
    DESIGN_STRATEGY_EXISTING,
    DESIGN_STRATEGY_MEASURED,
    DESIGNER_CLAUDE,
    DESIGNER_DETERMINISTIC,
    DesignService,
    DesignServiceError,
    design_dir_for,
    get_design_strategy,
    load_design_state,
)
from app.template_analysis.designer import (
    DesignerLiveDisabled,
    DesignerProviderCallError,
    MockTemplateDesigner,
    TemplateDesigner,
)
from tests.helpers.adobe_evidence import build_synthetic_target_analysis
from tests.helpers.synthetic_pdf import build_styled_target_pdf


class _FailingDesigner(TemplateDesigner):
    provider = "failing"

    def design(self, request):
        raise DesignerProviderCallError("synthetic provider failure")


def _setup():
    tmp = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp.name)
    artifact_dir = tmp_path / "artifact_1"
    artifact_dir.mkdir(parents=True)
    pdf = build_styled_target_pdf(artifact_dir / "styled.pdf")
    analysis = build_synthetic_target_analysis(pdf, artifact_dir)
    evidence = build_target_layout_evidence(
        analysis,
        target_checksum=target_checksum_from_bytes(pdf.read_bytes()),
        target_format="pdf",
    )
    profile = CandidateProfile(
        full_name="Jane",
        email="jane@example.com",
        professional_summary="Engineer.",
        skills=["Python"],
        languages=[{"name": "English"}],
        work_experience=[{"company": "X", "title": "E"}],
        education=[{"institution": "U"}],
    )
    sections = candidate_sections_from_profile(profile)
    return tmp, tmp_path, artifact_dir, evidence, analysis.style_spec, sections, profile


def test_default_strategy_is_measured_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEMPLATE_DESIGN_STRATEGY", raising=False)
    assert get_design_strategy() == DESIGN_STRATEGY_MEASURED


def test_claude_strategy_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMPLATE_DESIGN_STRATEGY", "claude_designer")
    assert get_design_strategy() == DESIGN_STRATEGY_CLAUDE


def test_invalid_strategy_value_falls_back_to_measured_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEMPLATE_DESIGN_STRATEGY", "random_thing")
    assert get_design_strategy() == DESIGN_STRATEGY_MEASURED


def test_deterministic_design_is_reproducible() -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    cache_dir = tmp_path / "cache"
    service = DesignService(strategy=DESIGN_STRATEGY_EXISTING, cache=DesignCache(cache_dir))
    state_a = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
        profile_sha256="sha-a",
    )
    state_b = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
        profile_sha256="sha-b",
    )
    assert state_a.state == "ready" or state_a.state == "ready_with_review"
    assert state_a.proposal is not None
    assert (
        state_a.proposal.model_dump(mode="json")
        == state_b.proposal.model_dump(mode="json")
    )
    tmp.cleanup()


def test_cache_key_includes_all_versions() -> None:
    key = DesignCacheKey(
        strategy=DESIGN_STRATEGY_EXISTING,
        target_checksum="t1",
        evidence_version="1.0",
        evidence_checksum="e1",
        designer_provider="mock",
        designer_model=None,
        prompt_version="designer/system-v1",
        compiler_version="1",
        inventory_sha256="i1",
    )
    payload = key.as_json()
    for field in (
        "target_checksum",
        "evidence_schema_version",
        "evidence_version",
        "evidence_checksum",
        "designer_provider",
        "designer_model",
        "prompt_version",
        "request_schema_version",
        "compiler_version",
        "inventory_sha256",
    ):
        assert field in payload
    # Any single version change must change the cache id.
    variants = [
        {**payload, "designer_model": "claude-3-5-sonnet"},
        {**payload, "prompt_version": "designer/system-v2"},
        {**payload, "compiler_version": "2"},
        {**payload, "evidence_version": "2.0"},
        {**payload, "request_schema_version": "design/request/4"},
    ]
    base_id = key.cache_id()
    for variant in variants:
        changed = DesignCacheKey(
            strategy=variant["strategy"],
            target_checksum=variant["target_checksum"],
            evidence_version=variant["evidence_version"],
            evidence_checksum=variant["evidence_checksum"],
            designer_provider=variant["designer_provider"],
            designer_model=variant["designer_model"],
            prompt_version=variant["prompt_version"],
            request_schema_version=variant["request_schema_version"],
            compiler_version=variant["compiler_version"],
            inventory_sha256=variant["inventory_sha256"],
        )
        assert changed.cache_id() != base_id


def test_cache_hit_reuses_proposal_without_second_designer_call() -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    cache_dir = tmp_path / "cache"

    class _CountingDesigner(MockTemplateDesigner):
        calls = 0

        def design(self, request):
            type(self).calls += 1
            return super().design(request)

    service = DesignService(
        strategy=DESIGN_STRATEGY_EXISTING,
        cache=DesignCache(cache_dir),
        designer=_CountingDesigner(),
    )
    service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
    )
    assert _CountingDesigner.calls == 1
    service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
    )
    assert _CountingDesigner.calls == 1  # cached, no second call
    tmp.cleanup()


def test_approval_requires_validated_state() -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    service = DesignService(strategy=DESIGN_STRATEGY_EXISTING, cache=DesignCache(tmp_path / "cache"))
    state = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
    )
    assert state.state in {"ready", "ready_with_review"}
    approved = service.approve_design(
        design_dir=design_dir_for(artifact_dir, state.design_id),
        artifact_dir=artifact_dir,
        reviewer_note="recruiter reviewed",
    )
    assert approved.state == "approved"
    assert approved.template_version == state.template_version
    assert approved.approved_at
    # Re-approving the same design is rejected.
    with pytest.raises(DesignServiceError):
        service.approve_design(
            design_dir=design_dir_for(artifact_dir, state.design_id),
            artifact_dir=artifact_dir,
        )
    tmp.cleanup()


def test_provider_failure_produces_structured_state() -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    service = DesignService(
        strategy=DESIGN_STRATEGY_CLAUDE,
        cache=DesignCache(tmp_path / "cache"),
        designer=_FailingDesigner(),
    )
    state = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
    )
    assert state.state == "provider_failed"
    assert state.trace is not None
    assert state.trace.safe_error_code == "provider_call_failed"
    assert state.proposal is None
    with pytest.raises(DesignServiceError):
        service.approve_design(
            design_dir=design_dir_for(artifact_dir, state.design_id),
            artifact_dir=artifact_dir,
        )
    tmp.cleanup()


def test_claude_designer_is_live_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    monkeypatch.delenv("TEMPLATE_DESIGN_LIVE_ENABLED", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    service = DesignService(
        strategy=DESIGN_STRATEGY_CLAUDE,
        cache=DesignCache(tmp_path / "cache"),
    )
    state = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
        designer=DESIGNER_CLAUDE,
    )
    assert state.state == "provider_failed"
    assert state.trace is not None
    assert state.trace.safe_error_code == "live_design_disabled"
    tmp.cleanup()


def test_supersede_previous_approved_design() -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    service = DesignService(strategy=DESIGN_STRATEGY_EXISTING, cache=DesignCache(tmp_path / "cache"))
    first = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
    )
    service.approve_design(
        design_dir=design_dir_for(artifact_dir, first.design_id),
        artifact_dir=artifact_dir,
    )
    second = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
    )
    # Second call hits the cache and produces a *new* design id with the same
    # proposal. Approving it supersedes the first approved design.
    service.approve_design(
        design_dir=design_dir_for(artifact_dir, second.design_id),
        artifact_dir=artifact_dir,
    )
    first_after = load_design_state(design_dir_for(artifact_dir, first.design_id))
    assert first_after.state == "superseded"
    tmp.cleanup()


def test_inventory_change_changes_design() -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    service = DesignService(strategy=DESIGN_STRATEGY_EXISTING, cache=DesignCache(tmp_path / "cache"))
    state = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
    )
    assert state.inventory_sha256 == inventory_sha256(sections)
    # Adding a certifications section changes the inventory.
    extra = [
        *sections,
        __import__("app.template_analysis.design_schemas", fromlist=["CandidateSectionRole"]).CandidateSectionRole(
            role="certifications", section_label="Certifications", item_count=1
        ),
    ]
    assert inventory_sha256(extra) != inventory_sha256(sections)
    tmp.cleanup()


def test_unknown_designer_rejected() -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    service = DesignService(strategy=DESIGN_STRATEGY_EXISTING, cache=DesignCache(tmp_path / "cache"))
    with pytest.raises(DesignServiceError):
        service.create_design(
            artifact_id="artifact_1",
            artifact_dir=artifact_dir,
            evidence=evidence,
            candidate_sections=sections,
            style_spec=style_spec,
            designer="deepseek",
        )
    tmp.cleanup()


def test_unrepresentable_placement_is_explicit_unsupported_state() -> None:
    """A validated proposal whose placement the current renderer cannot
    represent produces an explicit `unsupported` state, never a silent
    default placement."""
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    base = MockTemplateDesigner().design(build_design_request(evidence, sections))

    class _NonePlacementDesigner(MockTemplateDesigner):
        def design(self, request):
            return base.model_copy(
                update={
                    "mappings": [
                        mapping.model_copy(
                            update={"target_semantic_role": "none"}
                        )
                        for mapping in base.mappings
                    ]
                }
            )

    service = DesignService(
        strategy=DESIGN_STRATEGY_EXISTING,
        cache=DesignCache(tmp_path / "cache"),
        designer=_NonePlacementDesigner(),
    )
    state = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
    )
    assert state.state == "unsupported"
    assert state.layout_spec_json is None
    assert any("cannot be represented" in error for error in state.validation_errors)
    tmp.cleanup()


class _ControlledDesigner(MockTemplateDesigner):
    """Deterministic designer that reports controlled usage/request metadata."""

    provider = "controlled"

    def __init__(
        self,
        *,
        tokens: dict[str, int | None] | None = None,
        request_count: int = 0,
    ) -> None:
        super().__init__()
        self.last_usage = dict(tokens or {})
        self.last_request_count = request_count


def test_successful_design_records_token_usage_and_attempts() -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    designer = _ControlledDesigner(
        tokens={"input_tokens": 100, "output_tokens": 50}, request_count=1
    )
    service = DesignService(
        strategy=DESIGN_STRATEGY_EXISTING,
        cache=DesignCache(tmp_path / "cache"),
        designer=designer,
    )
    state = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
        retry_count=2,
    )
    assert state.trace is not None
    assert state.trace.cached is False
    assert state.trace.usage == {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "provider_request_count": 1,
    }
    assert state.trace.retry_count == 2
    # The persisted artifact carries the same accurate metadata.
    persisted = json.loads(
        (design_dir_for(artifact_dir, state.design_id) / "design_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["trace"]["usage"] == state.trace.usage
    assert persisted["trace"]["retry_count"] == 2
    tmp.cleanup()


def test_failed_call_records_attempt_count_and_error_code() -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    service = DesignService(
        strategy=DESIGN_STRATEGY_EXISTING,
        cache=DesignCache(tmp_path / "cache"),
        designer=_FailingDesigner(),
    )
    state = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
        retry_count=3,
    )
    assert state.state == "provider_failed"
    assert state.trace is not None
    assert state.trace.safe_error_code == "provider_call_failed"
    assert state.trace.usage["provider_request_count"] == 1
    assert state.trace.retry_count == 3
    assert state.trace.cached is False
    tmp.cleanup()


def test_cache_hit_records_zero_provider_calls_and_keeps_cached_usage() -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    designer = _ControlledDesigner(
        tokens={"input_tokens": 100, "output_tokens": 50}, request_count=1
    )
    service = DesignService(
        strategy=DESIGN_STRATEGY_EXISTING,
        cache=DesignCache(tmp_path / "cache"),
        designer=designer,
    )
    first = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
    )
    assert first.trace is not None
    assert first.trace.usage["provider_request_count"] == 1
    # The designer would now report different live usage; a cache hit must not
    # overwrite the cached token counts and must record zero new calls.
    designer.last_usage = {"input_tokens": 999, "output_tokens": 999}
    designer.last_request_count = 1
    second = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
    )
    assert second.trace is not None
    assert second.trace.cached is True
    assert second.trace.usage["input_tokens"] == 100
    assert second.trace.usage["output_tokens"] == 50
    assert second.trace.usage["total_tokens"] == 150
    assert second.trace.usage["provider_request_count"] == 0
    assert second.proposal.proposal_id == first.proposal.proposal_id
    assert designer.last_usage == {"input_tokens": 999, "output_tokens": 999}  # untouched
    tmp.cleanup()


def test_trace_artifacts_contain_no_secrets() -> None:
    tmp, tmp_path, artifact_dir, evidence, style_spec, sections, profile = _setup()
    designer = _ControlledDesigner(
        tokens={"input_tokens": 5, "output_tokens": 7}, request_count=1
    )
    service = DesignService(
        strategy=DESIGN_STRATEGY_EXISTING,
        cache=DesignCache(tmp_path / "cache"),
        designer=designer,
    )
    state = service.create_design(
        artifact_id="artifact_1",
        artifact_dir=artifact_dir,
        evidence=evidence,
        candidate_sections=sections,
        style_spec=style_spec,
    )
    design_dir = design_dir_for(artifact_dir, state.design_id)
    for filename in ("design_state.json", "design_trace.json", "design_request.json"):
        payload = (design_dir / filename).read_text(encoding="utf-8")
        for marker in ("sk-", "api_key", "authorization", "Bearer "):
            assert marker not in payload, f"secret marker {marker!r} in {filename}"
    tmp.cleanup()
