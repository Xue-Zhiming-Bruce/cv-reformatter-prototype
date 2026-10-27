"""Versioned local job contracts; payloads reference facts without copying them."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.generation.artifact_manifest import Digest, FileIdentity


JobState = Literal["queued", "running", "succeeded", "failed", "cancelled", "needs_attention"]
JobError = Literal["cancelled", "deadline_exceeded", "attempts_exhausted", "inputs_changed",
                   "execution_failed", "worker_exit", "publication_conflict"]
JobId = Annotated[str, Field(pattern=r"^job_[0-9a-f]{32}$")]


class JobModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


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


class JobSubmission(JobModel):
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,128}$")
    payload: NativeGenerationPayload


class JobRecord(JobModel):
    schema_version: Literal["job/1"] = "job/1"
    job_id: JobId
    artifact_id: str
    state: JobState
    progress: int = Field(ge=0, le=100)
    attempt_count: int = Field(ge=0, le=3)
    cancel_requested: bool
    error_code: JobError | None
    result_generation_id: str | None
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime


class JobClaim(JobModel):
    job: JobRecord
    payload: NativeGenerationPayload
    lease_token: str
    lease_expires_at: datetime
    bundle_path: str
