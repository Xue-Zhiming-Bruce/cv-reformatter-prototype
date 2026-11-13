"""Live target job contract: explicit refusal, immutable offline meanings and evidence separation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.jobs import (LiveExecutionRefused, LiveTargetProofPayload, OPERATION_BY_SCHEMA,
                      PAYLOAD_BY_SCHEMA, JobSubmission, TargetProofPayload, refuse_live_execution)
from app.storage.job_queue import PostgresJobQueue
from app.template_analysis.layout_proof_artifacts import (file_sha256, layout_proofs_dir_for,
                                                         save_variant_evidence, variant_dir_for)
from app.template_analysis.layout_proof_rendering import LayoutProofMismatchError
from app.template_analysis.layout_proof_schemas import (LayoutProofApproval, LayoutProofRenderResult,
                                                       LengthVariant, StructuralValidationResult,
                                                       VariantProofEvidence, VisualComparisonEvidence)
from app.template_analysis.layout_proof_service import require_review_evidence
from app.template_analysis.layout_proof_validation import reusable_variant_evidence
from app.template_analysis.schemas import VisualComparisonReport


def _target_identities() -> dict:
    return {
        "artifact_id": "artifact_live_jobs",
        "profile_revision_id": "revision-1",
        "profile_sha256": "1" * 64,
        "source_draft_sha256": "2" * 64,
        "target_sha256": "3" * 64,
        "target_stored_filename": "target_format_reference.pdf",
        "analysis_sha256": "4" * 64,
        "layout_sha256": "5" * 64,
        "renderer_version": "agentic-a/1",
        "presentation_label_catalog_sha256": None,
        "component_source_sha256": {"synthetic-component": "6" * 64},
    }


def _live_proof_payload() -> LiveTargetProofPayload:
    return LiveTargetProofPayload(
        **_target_identities(),
        authorization={
            "policy_version": "provider-policy/1",
            "policy_sha256": "7" * 64,
            "authorization_id": "authz_0123456789abcdef",
            "execution_envelope_sha256": "8" * 64,
        },
    )


class _NoStorage:
    def __getattr__(self, name: str):
        raise AssertionError(f"live submission touched storage: {name}")


def test_live_payload_requires_a_policy_authorization_object() -> None:
    with pytest.raises(ValidationError):
        LiveTargetProofPayload(**_target_identities())
    payload = _live_proof_payload()
    assert payload.execution == "proof-live/1"
    assert payload.schema_version == "target-proof-job/2"
    assert payload.authorization.authorization_id == "authz_0123456789abcdef"


def test_offline_payload_meanings_stay_immutable() -> None:
    offline = TargetProofPayload(**_target_identities())
    assert (offline.schema_version, offline.execution) == ("target-proof-job/1", "proof-local/1")
    with pytest.raises(ValidationError):
        TargetProofPayload(**_target_identities(), execution="proof-live/1")
    # Only the frozen offline contract is executable or stored.
    assert set(PAYLOAD_BY_SCHEMA) == {"native-generation-job/1", "target-proof-job/1", "target-generation-job/1"}
    assert set(OPERATION_BY_SCHEMA) == {"native-generation-job/1", "target-proof-job/1", "target-generation-job/1"}


def test_live_submission_is_refused_before_any_storage_io() -> None:
    queue = PostgresJobQueue(_NoStorage())
    with pytest.raises(LiveExecutionRefused) as caught:
        queue.submit(JobSubmission(idempotency_key="live-1", payload=_live_proof_payload()))
    assert caught.value.error_code == "live_execution_blocked"
    assert caught.value.reason == "provider_policy_missing"
    assert "credentials alone never authorize" in str(caught.value)


def test_environment_credentials_are_not_an_enablement_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-credential")
    monkeypatch.setenv("LOCAL_JOBS_ENABLED", "1")
    with pytest.raises(LiveExecutionRefused):
        refuse_live_execution()


def _render_result(execution_mode: str, proof_id: str, variant: LengthVariant = LengthVariant.MEDIUM) -> LayoutProofRenderResult:
    return LayoutProofRenderResult(
        proof_id=proof_id,
        artifact_id="artifact_live_jobs",
        variant=variant,
        layout_spec_checksum="5" * 64,
        target_checksum="3" * 64,
        context_checksum="9" * 64,
        renderer_version="agentic-a/1",
        html_filename=f"proof-{variant.value}.html",
        pdf_filename=f"proof-{variant.value}.pdf",
        html_checksum="a" * 64,
        pdf_checksum="b" * 64,
        page_count=0,
        execution_mode=execution_mode,
    )


def _evidence(execution_mode: str, variant: LengthVariant = LengthVariant.MEDIUM) -> VariantProofEvidence:
    return VariantProofEvidence(
        variant=variant,
        render_result=_render_result(execution_mode, f"layout_proof_{variant.value}_0123456789abcdef", variant),
        structural=StructuralValidationResult(passed=True, page_count=0),
        visual=VisualComparisonEvidence(report=VisualComparisonReport(
            target_page_count=1, generated_page_count=1, compared_page_count=0,
            page_count_similarity=1.0, average_layout_similarity=1.0,
            target_page_filenames=[], generated_page_filenames=[], pages=[])),
    )


def _approval(execution_mode: str) -> LayoutProofApproval:
    return LayoutProofApproval(
        approval_id="approval_0123456789abcdef",
        artifact_id="artifact_live_jobs",
        target_checksum="3" * 64,
        layout_spec_checksum="5" * 64,
        renderer_version="agentic-a/1",
        state="pending",
        variants={variant: _evidence(execution_mode, variant) for variant in LengthVariant},
        approved_at=None,
        reviewer_id=None,
    )


def test_offline_evidence_never_satisfies_a_live_review_requirement() -> None:
    approval = _approval("offline-renderer/1")
    # Target, layout and renderer match exactly; only the recorded execution
    # provenance separates the offline artifact from a live-reviewed one.
    require_review_evidence(approval, required_execution="offline-renderer/1")
    with pytest.raises(LayoutProofMismatchError, match="cannot satisfy"):
        require_review_evidence(approval, required_execution="live-provider/1")


def test_stored_offline_evidence_is_reused_only_by_offline_execution(tmp_path: Path) -> None:
    proof_id = "layout_proof_medium_0123456789abcdef"
    proof_dir = layout_proofs_dir_for(tmp_path) / proof_id
    proof_dir.mkdir(parents=True)
    (variant_dir_for(tmp_path, LengthVariant.MEDIUM) / "visual").mkdir(parents=True)
    html, pdf = proof_dir / "proof-medium.html", proof_dir / "proof-medium.pdf"
    html.write_text("<html></html>", encoding="utf-8")
    pdf.write_bytes(b"%PDF-1.4")
    evidence = _evidence("offline-renderer/1").model_copy(update={"render_result": _render_result(
        "offline-renderer/1", proof_id).model_copy(update={
            "html_checksum": file_sha256(html), "pdf_checksum": file_sha256(pdf)})})
    save_variant_evidence(tmp_path, evidence)
    identities = {"target_checksum": "3" * 64, "layout_sha256": "5" * 64, "renderer_version": "agentic-a/1"}
    assert reusable_variant_evidence(tmp_path, LengthVariant.MEDIUM,
                                     execution_mode="offline-renderer/1", **identities) is not None
    assert reusable_variant_evidence(tmp_path, LengthVariant.MEDIUM,
                                     execution_mode="agentic-provider/1", **identities) is None
    assert reusable_variant_evidence(tmp_path, LengthVariant.MEDIUM,
                                     execution_mode="live-provider/1", **identities) is None
