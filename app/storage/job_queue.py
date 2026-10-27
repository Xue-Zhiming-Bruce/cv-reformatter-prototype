"""PostgreSQL local queue: short claims, bounded recovery and fenced completion."""

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
from typing import Any, Iterator, Literal
import uuid

from pydantic import ValidationError
from sqlalchemy import Connection, Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.jobs import JobClaim, JobError, JobRecord, JobSubmission, NativeGenerationPayload
from app.storage.artifact_store import ArtifactStoreConflict, ArtifactStoreError
from app.storage.postgres import artifact_sessions, job_attempts, profile_revisions, workflow_jobs


class JobLeaseLost(ArtifactStoreConflict):
    """This attempt no longer owns an unexpired execution lease."""


def _digest(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _payload(row: Mapping[str, Any]) -> NativeGenerationPayload:
    payload = NativeGenerationPayload.model_validate_json(json.dumps(row["payload"]))
    if (_digest(row["payload"]) != row["request_sha256"]
            or payload.artifact_id != row["artifact_id"]
            or payload.profile_revision_id != row["profile_revision_id"]
            or payload.max_attempts != row["max_attempts"]):
        raise ValueError("Job payload identity mismatch")
    return payload


def _record(row: Mapping[str, Any]) -> JobRecord:
    return JobRecord.model_validate({key: row[key] for key in JobRecord.model_fields if key in row})


class PostgresJobQueue:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @contextmanager
    def _transaction(self) -> Iterator[Connection]:
        try:
            with self.engine.begin() as connection:
                yield connection
        except (SQLAlchemyError, ValidationError, ValueError) as exc:
            raise ArtifactStoreError("Job storage failed; verify PostgreSQL availability, migrations and saved identities.") from exc

    def submit(self, submission: JobSubmission) -> JobRecord:
        payload = submission.payload
        body = payload.model_dump(mode="json")
        digest = _digest(body)
        with self._transaction() as connection:
            artifact = connection.execute(select(artifact_sessions).where(
                artifact_sessions.c.artifact_id == payload.artifact_id).with_for_update()).mappings().first()
            existing = connection.execute(select(workflow_jobs).where(
                workflow_jobs.c.scope_id == "local-synthetic", workflow_jobs.c.operation == "generate",
                workflow_jobs.c.artifact_id == payload.artifact_id,
                workflow_jobs.c.idempotency_key == submission.idempotency_key)).mappings().first()
            if existing is not None:
                _payload(existing)
                if existing["request_sha256"] != digest:
                    raise ArtifactStoreConflict("The idempotency key already refers to different inputs or execution settings.")
                return _record(existing)
            revision = connection.execute(select(profile_revisions).where(
                profile_revisions.c.artifact_id == payload.artifact_id,
                profile_revisions.c.profile_revision_id == payload.profile_revision_id)).mappings().first()
            if (artifact is None or revision is None
                    or artifact["current_profile_revision_id"] != payload.profile_revision_id
                    or revision["profile_sha256"] != payload.profile_sha256
                    or revision["source_draft_sha256"] != payload.source_draft_sha256):
                raise ArtifactStoreConflict("Submit a job against the current saved candidate revision.")
            now = connection.scalar(select(func.clock_timestamp()))
            row = connection.execute(insert(workflow_jobs).values(
                job_id="job_" + uuid.uuid4().hex, artifact_id=payload.artifact_id,
                profile_revision_id=payload.profile_revision_id, scope_id="local-synthetic", operation="generate",
                idempotency_key=submission.idempotency_key, request_sha256=digest, payload=body,
                state="queued", max_attempts=payload.max_attempts,
                deadline_at=now + timedelta(seconds=payload.timeout_seconds),
            ).returning(workflow_jobs)).mappings().one()
            return _record(row)

    def get(self, artifact_id: str, job_id: str) -> JobRecord | None:
        with self._transaction() as connection:
            row = connection.execute(select(workflow_jobs).where(
                workflow_jobs.c.artifact_id == artifact_id, workflow_jobs.c.job_id == job_id)).mappings().first()
            if row is None:
                return None
            _payload(row)
            return _record(row)

    def cancel(self, artifact_id: str, job_id: str) -> JobRecord | None:
        with self._transaction() as connection:
            row = connection.execute(select(workflow_jobs).where(
                workflow_jobs.c.artifact_id == artifact_id, workflow_jobs.c.job_id == job_id,
            ).with_for_update()).mappings().first()
            if row is None:
                return None
            if row["state"] not in {"queued", "running"}:
                return _record(row)
            values: dict[str, Any] = {"cancel_requested": True, "updated_at": func.clock_timestamp()}
            if row["state"] == "queued":
                values.update(state="cancelled", error_code="cancelled")
            updated = connection.execute(update(workflow_jobs).where(
                workflow_jobs.c.job_id == job_id).values(**values).returning(workflow_jobs)).mappings().one()
            return _record(updated)

    def claim(self, *, lease_seconds: int = 30) -> JobClaim | None:
        if not 1 <= lease_seconds <= 60:
            raise ValueError("Lease duration must be between 1 and 60 seconds")
        with self._transaction() as connection:
            now = connection.scalar(select(func.clock_timestamp()))
            expired = connection.execute(select(workflow_jobs).where(
                workflow_jobs.c.state.in_(["queued", "running"]),
                (workflow_jobs.c.deadline_at <= now)
                | ((workflow_jobs.c.state == "running") & (workflow_jobs.c.lease_expires_at <= now)),
            ).order_by(workflow_jobs.c.created_at).limit(100).with_for_update(skip_locked=True)).mappings().all()
            for row in expired:
                _payload(row)  # Recovery only permits the explicitly safe native execution contract.
                if row["cancel_requested"]:
                    state, error = "cancelled", "cancelled"
                elif row["deadline_at"] <= now:
                    state, error = "failed", "deadline_exceeded"
                elif row["attempt_count"] >= row["max_attempts"]:
                    state, error = "failed", "attempts_exhausted"
                else:
                    state, error = "queued", None
                if row["state"] == "running":
                    connection.execute(update(job_attempts).where(
                        job_attempts.c.job_id == row["job_id"],
                        job_attempts.c.attempt_number == row["attempt_count"],
                    ).values(state="abandoned", error_code=error, finished_at=now))
                connection.execute(update(workflow_jobs).where(workflow_jobs.c.job_id == row["job_id"]).values(
                    state=state, error_code=error, lease_token=None, lease_expires_at=None,
                    progress=0 if state == "queued" else row["progress"], updated_at=now))
            row = connection.execute(select(workflow_jobs).where(
                workflow_jobs.c.state == "queued", workflow_jobs.c.deadline_at > now,
            ).order_by(workflow_jobs.c.created_at, workflow_jobs.c.job_id).limit(1)
                .with_for_update(skip_locked=True)).mappings().first()
            if row is None:
                return None
            payload = _payload(row)
            attempt = row["attempt_count"] + 1
            token = uuid.uuid4().hex
            expires = min(now + timedelta(seconds=lease_seconds), row["deadline_at"])
            bundle = f"jobs/{row['job_id']}/attempt_{attempt:04d}/{row['artifact_id']}"
            connection.execute(insert(job_attempts).values(job_id=row["job_id"], attempt_number=attempt,
                lease_token=token, bundle_path=bundle, state="running"))
            updated = connection.execute(update(workflow_jobs).where(workflow_jobs.c.job_id == row["job_id"]).values(
                state="running", attempt_count=attempt, lease_token=token, lease_expires_at=expires,
                updated_at=now, progress=0,
            ).returning(workflow_jobs)).mappings().one()
            return JobClaim(job=_record(updated), payload=payload, lease_token=token,
                            lease_expires_at=expires, bundle_path=bundle)

    def _owned(self, connection: Connection, claim: JobClaim) -> Mapping[str, Any]:
        row = connection.execute(select(workflow_jobs).where(
            workflow_jobs.c.job_id == claim.job.job_id).with_for_update()).mappings().first()
        now = connection.scalar(select(func.clock_timestamp()))
        if (row is None or row["state"] != "running" or row["lease_token"] != claim.lease_token
                or row["attempt_count"] != claim.job.attempt_count or row["lease_expires_at"] <= now
                or row["deadline_at"] <= now):
            raise JobLeaseLost("The job lease or execution deadline is no longer current.")
        _payload(row)
        return row

    def heartbeat(self, claim: JobClaim, *, progress: int, lease_seconds: int = 30) -> bool:
        if not 0 <= progress <= 99 or not 1 <= lease_seconds <= 60:
            raise ValueError("Invalid running progress or lease duration")
        with self._transaction() as connection:
            row = self._owned(connection, claim)
            if row["cancel_requested"]:
                self._finish(connection, row, state="cancelled", error="cancelled")
                return False
            now = connection.scalar(select(func.clock_timestamp()))
            connection.execute(update(workflow_jobs).where(workflow_jobs.c.job_id == row["job_id"]).values(
                progress=max(progress, row["progress"]), updated_at=now,
                lease_expires_at=min(now + timedelta(seconds=lease_seconds), row["deadline_at"])))
            return True

    def _finish(self, connection: Connection, row: Mapping[str, Any], *, state: str,
                error: JobError | None, generation_id: str | None = None) -> JobRecord:
        now = connection.scalar(select(func.clock_timestamp()))
        connection.execute(update(job_attempts).where(
            job_attempts.c.job_id == row["job_id"], job_attempts.c.attempt_number == row["attempt_count"],
        ).values(state=state, error_code=error, finished_at=now))
        updated = connection.execute(update(workflow_jobs).where(workflow_jobs.c.job_id == row["job_id"]).values(
            state=state, error_code=error, result_generation_id=generation_id, lease_token=None,
            lease_expires_at=None, updated_at=now, progress=100 if state == "succeeded" else row["progress"],
        ).returning(workflow_jobs)).mappings().one()
        return _record(updated)

    def fail(self, claim: JobClaim, *, error: JobError,
             state: Literal["failed", "needs_attention"] = "failed") -> JobRecord:
        if error not in JobError.__args__ or state not in {"failed", "needs_attention"}:
            raise ValueError("Job failure requires a safe error code and terminal state")
        with self._transaction() as connection:
            row = self._owned(connection, claim)
            return self._finish(connection, row, state="cancelled" if row["cancel_requested"] else state,
                                error="cancelled" if row["cancel_requested"] else error)

    def complete(self, claim: JobClaim, publish: Callable[[Connection], str]) -> JobRecord:
        """Publication and successful completion share the same fenced transaction."""
        with self._transaction() as connection:
            row = self._owned(connection, claim)
            if row["cancel_requested"]:
                return self._finish(connection, row, state="cancelled", error="cancelled")
            generation_id = publish(connection)
            self._owned(connection, claim)  # A slow publication must not outlive its lease.
            return self._finish(connection, row, state="succeeded", error=None, generation_id=generation_id)
