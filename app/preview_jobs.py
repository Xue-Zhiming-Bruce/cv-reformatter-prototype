"""Inspectable local preview jobs; restart never resumes a provider call silently."""
from __future__ import annotations

import json
import subprocess
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class PreviewJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    artifact_id: str
    profile_revision_id: str
    state: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    stage: str
    created_at: str
    updated_at: str
    result: dict[str, object] | None = None
    error: dict[str, object] | None = None


# shortcut: one local worker, explicit restart failure; durable queues belong to P2.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cv-preview")
_active: set[str] = set()
_guard = threading.RLock()
_locks: dict[str, threading.RLock] = {}


def artifact_lock(artifact_id: str) -> threading.RLock:
    with _guard:
        return _locks.setdefault(artifact_id, threading.RLock())


def _save(path: Path, job: PreviewJob) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(job.model_dump_json(indent=2))
    temporary.replace(path)


def read_job(directory: Path, job_id: str) -> PreviewJob:
    if not re_valid_job_id(job_id):
        raise HTTPException(400, "Invalid preview job id")
    path = directory / "preview_jobs" / job_id / "status.json"
    with _guard:
        if not path.exists():
            raise HTTPException(404, "Preview job not found")
        job = PreviewJob.model_validate_json(path.read_text())
        if job.state in {"queued", "running"} and job.job_id not in _active:
            job.state = "interrupted"; job.stage = "interrupted"
            job.error = {"error_code": "preview_interrupted", "message": "The backend restarted during this preview. Start a new preview job."}
            job.updated_at = datetime.now(UTC).isoformat(); _save(path, job)
        return job


def re_valid_job_id(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def assert_idle(directory: Path) -> None:
    with _guard:
        if any(path.parent.name in _active for path in (directory / "preview_jobs").glob("*/status.json")):
            raise HTTPException(409, {"error_code": "preview_running", "message": "Wait for the current preview before editing or accepting it."})


def _failure_evidence(error: BaseException | None) -> list[dict[str, object]]:
    # Exception text, source lines and locals can contain candidate data or credentials.
    chain: list[dict[str, object]] = []
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        item: dict[str, object] = {
            "exception_type": type(error).__name__,
            "frames": [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name} for frame in traceback.extract_tb(error.__traceback__)],
        }
        if isinstance(error, subprocess.TimeoutExpired):
            item["timeout_seconds"] = error.timeout
        if isinstance(error, subprocess.CalledProcessError):
            item["returncode"] = error.returncode
        chain.append(item)
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return chain


def start_job(directory: Path, artifact_id: str, revision_id: str, request: dict[str, object], generate: Callable[[], dict[str, object]]) -> PreviewJob:
    with _guard:
        for path in (directory / "preview_jobs").glob("*/status.json"):
            old = read_job(directory, path.parent.name)
            if old.state in {"queued", "running"}:
                if json.loads((path.parent / "request.json").read_text()) == request:
                    return old
                raise HTTPException(409, {"error_code": "preview_already_running", "message": "Wait for the current preview to finish.", "job_id": old.job_id})
        job_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        job = PreviewJob(job_id=job_id, artifact_id=artifact_id, profile_revision_id=revision_id, state="queued", stage="queued", created_at=now, updated_at=now)
        folder = directory / "preview_jobs" / job_id; folder.mkdir(parents=True)
        path = folder / "status.json"; _save(path, job)
        (folder / "request.json").write_text(json.dumps(request, indent=2))
        _active.add(job_id)
        queued = job.model_copy(deep=True)

    def run() -> None:
        try:
            with artifact_lock(artifact_id):
                with _guard:
                    job.state = "running"; job.stage = "building_and_checking_preview"
                    job.updated_at = datetime.now(UTC).isoformat(); _save(path, job)
                result = generate()
                with _guard:
                    job.result = result; job.state = "succeeded"; job.stage = "ready_for_review"
        except Exception as error:
            failed_stage = job.stage
            with _guard:
                job.state = "failed"; job.stage = "failed"
                if isinstance(error, HTTPException):
                    job.error = error.detail if isinstance(error.detail, dict) else {"error_code": "preview_failed", "message": str(error.detail)}
                else:
                    job.error = {"error_code": "preview_failed", "message": "Preview failed. Inspect the local job evidence and retry."}
                try:
                    (folder / "failure.json").write_text(json.dumps({"job_id": job_id, "stage": failed_stage, "exception_chain": _failure_evidence(error)}, indent=2))
                except OSError:
                    job.error["diagnostic_write_failed"] = True
        finally:
            with _guard:
                job.updated_at = datetime.now(UTC).isoformat(); _save(path, job); _active.discard(job_id)
    _executor.submit(run)
    return queued
