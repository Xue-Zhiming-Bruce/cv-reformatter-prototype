"""Versioned local job contracts; payloads reference facts without copying them."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.generation.artifact_manifest import Digest, FileIdentity
from app.pipeline.run_models import PresentationLabelApproval
from app.template_analysis.layout_proof_schemas import LengthVariant


JobState = Literal["queued", "running", "succeeded", "failed", "cancelled", "needs_attention"]
JobOperation = Literal["generate", "create_proofs", "generate_target"]
JobStage = Literal["queued", "snapshot", "render", "validate", "publish", "review_ready"]
VariantJobState = Literal["pending", "rendering", "rendered", "validated", "reused", "failed"]
JobError = Literal["cancelled", "deadline_exceeded", "attempts_exhausted", "inputs_changed",
                   "execution_failed", "worker_exit", "publication_conflict",
                   "provider_outcome_unknown", "budget_exceeded"]
JobId = Annotated[str, Field(pattern=r"^job_[0-9a-f]{32}$")]

#: Frozen execution envelope for the uploaded-target operations. Basis is the
#: measured 291 s / 28 model request / 7-9-12 per-variant run in issue #38. A
#: resumed attempt re-renders only variants whose evidence no longer matches,
#: and an unknown provider outcome ends the job instead of being retried.
TARGET_TIMEOUT_SECONDS = 900
TARGET_STAGE_TIMEOUT_SECONDS = 300
TARGET_MAX_ATTEMPTS = 3
TARGET_MAX_MODEL_REQUESTS = 36
TARGET_MAX_TOOL_CALLS = 72


class JobModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _variant_states(value):
    """Stored JSON carries variant names; parse them into ``LengthVariant``."""
    return {LengthVariant(name): state for name, state in value.items()} if value else value


class NativeGenerationPayload(JobModel):
    schema_version: Literal["native-generation-job/1"] = "native-generation-job/1"
    execution: Literal["native-local/1"] = "native-local/1"
    artifact_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    profile_revision_id: str = Field(min_length=1, max_length=100)
    profile_sha256: Digest
    source_draft_sha256: Digest
    inputs: dict[str, FileIdentity] = Field(min_length=2, max_length=32)
    layout_sha256: Digest
    component_source_sha256: dict[str, Digest] = Field(min_length=1, max_length=32)
    blind_profile: bool = False
    timeout_seconds: int = Field(default=180, ge=1, le=600)
    max_attempts: int = Field(default=2, ge=1, le=3)

    @model_validator(mode="after")
    def required_inputs(self) -> "NativeGenerationPayload":
        source, revision = self.inputs.get("source_draft"), self.inputs.get("profile_revision")
        if (source is None or source.path != "candidate_profile.json" or source.sha256 != self.source_draft_sha256
                or revision is None or revision.path != f"profiles/approved_profile_{self.profile_revision_id}.json"):
            raise ValueError("Job inputs must identify the saved draft and immutable profile revision")
        return self


class _TargetPayload(JobModel):
    """Uploaded-target identities, never candidate facts or document bytes."""

    execution: Literal["proof-local/1", "target-local/1"]
    artifact_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    profile_revision_id: str = Field(min_length=1, max_length=100)
    profile_sha256: Digest
    source_draft_sha256: Digest
    target_sha256: Digest
    target_stored_filename: Literal["target_format_reference.pdf"]
    analysis_sha256: Digest
    layout_sha256: Digest
    design_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    presentation_label_catalog_sha256: Digest | None
    presentation_label_approval: PresentationLabelApproval | None = None
    renderer_version: str = Field(min_length=1, max_length=100)
    component_source_sha256: dict[str, Digest] = Field(min_length=1, max_length=32)
    timeout_seconds: int = Field(default=TARGET_TIMEOUT_SECONDS, ge=60, le=3600)
    variant_timeout_seconds: int = Field(default=TARGET_STAGE_TIMEOUT_SECONDS, ge=30, le=1800)
    max_attempts: int = Field(default=TARGET_MAX_ATTEMPTS, ge=1, le=3)
    max_model_requests: int = Field(default=TARGET_MAX_MODEL_REQUESTS, ge=1, le=120)
    max_tool_calls: int = Field(default=TARGET_MAX_TOOL_CALLS, ge=1, le=200)

    @model_validator(mode="after")
    def frozen_identity_agrees(self):
        if self.variant_timeout_seconds > self.timeout_seconds:
            raise ValueError("A stage deadline cannot exceed the overall job deadline")
        approval = self.presentation_label_approval
        if approval is not None and (approval.target_sha256 != self.target_sha256
                                     or approval.catalog_sha256 != self.presentation_label_catalog_sha256):
            raise ValueError("Presentation-label approval must cover this exact target and catalog")
        return self


class TargetProofPayload(_TargetPayload):
    schema_version: Literal["target-proof-job/1"] = "target-proof-job/1"
    execution: Literal["proof-local/1"] = "proof-local/1"


class TargetGenerationPayload(_TargetPayload):
    schema_version: Literal["target-generation-job/1"] = "target-generation-job/1"
    execution: Literal["target-local/1"] = "target-local/1"
    proof_approval_id: str = Field(pattern=r"^approval_[0-9a-f]{16}$")
    proof_set_sha256: Digest
    inputs: dict[str, FileIdentity] = Field(min_length=2, max_length=32)
    blind_profile: bool = False

    @model_validator(mode="after")
    def required_inputs(self) -> "TargetGenerationPayload":
        target = self.inputs.get("target_document")
        approval = self.inputs.get("layout_proof_approval")
        if (target is None or target.path != self.target_stored_filename or target.sha256 != self.target_sha256
                or approval is None or approval.path != "layout_proofs/approval.json"
                or approval.sha256 != self.proof_set_sha256):
            raise ValueError("Generation inputs must identify the frozen target and proof set")
        return self


JobPayload = NativeGenerationPayload | TargetProofPayload | TargetGenerationPayload
#: The queue operation each payload schema version belongs to.
OPERATION_BY_SCHEMA: dict[str, JobOperation] = {
    "native-generation-job/1": "generate",
    "target-proof-job/1": "create_proofs",
    "target-generation-job/1": "generate_target",
}
PAYLOAD_BY_SCHEMA = {
    "native-generation-job/1": NativeGenerationPayload,
    "target-proof-job/1": TargetProofPayload,
    "target-generation-job/1": TargetGenerationPayload,
}


class JobSubmission(JobModel):
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,128}$")
    payload: JobPayload


class LocalSyntheticJobCreate(JobModel):
    local_synthetic: Literal[True]
    profile_revision_id: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,128}$")

    @field_validator("local_synthetic", mode="before")
    @classmethod
    def explicit_synthetic_confirmation(cls, value):
        if value is not True:
            raise ValueError("Local synthetic execution requires the boolean true")
        return value


class NativeJobCreate(LocalSyntheticJobCreate):
    blind_profile: bool = False


class TargetJobCreate(LocalSyntheticJobCreate):
    """A local synthetic uploaded-target request against an existing target."""

    design_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,100}$")


class TargetProofJobCreate(TargetJobCreate):
    presentation_label_approval: PresentationLabelApproval | None = None


class TargetGenerationJobCreate(TargetJobCreate):
    proof_approval_id: str = Field(pattern=r"^approval_[0-9a-f]{16}$")
    blind_profile: bool = False


class JobRecord(JobModel):
    schema_version: Literal["job/1"] = "job/1"
    job_id: JobId
    artifact_id: str
    operation: JobOperation
    state: JobState
    progress: int = Field(ge=0, le=100)
    stage: JobStage | None = None
    variants: dict[LengthVariant, VariantJobState] | None = None
    attempt_count: int = Field(ge=0, le=3)
    cancel_requested: bool
    error_code: JobError | None
    result_generation_id: str | None
    result_proof_approval_id: str | None
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime

    _variants = field_validator("variants", mode="before")(_variant_states)


class JobProgress(JobModel):
    """The attempt bundle's ``progress.json``: bounded stage and variant state.

    Written by the child between provider/render stages and copied into the
    job's ``progress_detail`` by the worker's heartbeat, so a reconnect reads
    the same state from storage. ``provider_call_in_flight`` distinguishes a
    crashed attempt with a possibly-charged request from a clean restart.
    """

    schema_version: Literal["job-progress/1"] = "job-progress/1"
    stage: JobStage
    variants: dict[LengthVariant, VariantJobState] = Field(default_factory=dict)
    provider_call_in_flight: bool = False
    model_requests: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    error_code: JobError | None = None

    _variants = field_validator("variants", mode="before")(_variant_states)


class JobClaim(JobModel):
    job: JobRecord
    payload: JobPayload
    lease_token: str
    lease_expires_at: datetime
    bundle_path: str


class NativeChildExit(JobModel):
    schema_version: Literal["native-child-exit/1"] = "native-child-exit/1"
    job_id: JobId
    attempt_number: int = Field(ge=1, le=3)
    returncode: int
    finished_at: datetime
