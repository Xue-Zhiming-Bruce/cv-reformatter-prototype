"""Actual native child execution, immutable publication and recovery boundaries."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text, update

from app import main
from app.extraction.candidate_schema import CandidateProfile
from app.job_worker import NativeJobWorker, ROOT
from app.storage.artifact_store import ArtifactStoreConflict
from app.storage.job_queue import PostgresJobQueue
from app.storage.postgres import PostgresGenerationRepository, artifact_sessions, generation_versions, job_attempts, workflow_jobs
from tests.helpers.postgres import migrate_database


pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.name != "posix", reason="Native worker process groups require POSIX")]
IDENTITY_FIELDS = ("profile_revision_id", "profile_sha256", "layout_template_version", "layout_spec_sha256", "html_sha256", "pdf_sha256")


@pytest.fixture
def native_jobs(postgres_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    url, engine = postgres_database
    migrate_database(engine)
    monkeypatch.setattr(main, "DATABASE_URL", url)
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setenv("LOCAL_JOBS_ENABLED", "1")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    client = TestClient(main.app)
    directory = tmp_path / "artifact_native_jobs"
    directory.mkdir()
    profile = CandidateProfile(full_name="Synthetic Worker Candidate", email="worker@example.invalid", skills=["Python"])
    (directory / "candidate_profile.json").write_text(profile.model_dump_json())
    saved = client.post(f"/api/artifacts/{directory.name}/profiles/revisions", json={"profile": profile.model_dump(mode="json")})
    assert saved.status_code == 200, saved.text
    revision_id = saved.json()["profile_revision_id"]
    generated = client.post("/api/generate", json={"artifact_id": directory.name, "profile_revision_id": revision_id})
    assert generated.status_code == 200, generated.text
    accepted = client.post(f"/api/artifacts/{directory.name}/final-acceptance",
        json={key: generated.json()[key] for key in IDENTITY_FIELDS})
    assert accepted.status_code == 200, accepted.text
    queue = PostgresJobQueue(engine)
    return {"url": url, "engine": engine, "directory": directory, "client": client,
            "queue": queue, "worker": NativeJobWorker(queue, tmp_path), "revision": revision_id,
            "body": {"local_synthetic": True, "profile_revision_id": revision_id, "idempotency_key": "native-1", "blind_profile": True},
            "pdf": (directory / "candidate_profile.pdf").read_bytes()}


def test_job_api_requires_explicit_local_postgres_enablement(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LOCAL_JOBS_ENABLED", raising=False)
    client = TestClient(main.app)
    response = client.post("/api/artifacts/unknown/generation-jobs", json={"local_synthetic": True,
        "profile_revision_id": "revision", "idempotency_key": "key"})
    assert response.status_code == 503 and response.json()["detail"]["error_code"] == "jobs_unavailable"
    monkeypatch.setenv("LOCAL_JOBS_ENABLED", "1")
    assert client.get("/api/artifacts/unknown/jobs/job_unknown").status_code == 503


@pytest.mark.parametrize("recovery", [False, True], ids=["normal", "restore"])
def test_native_child_publishes_bundle_and_reads_ignore_loose_files(native_jobs, request: pytest.FixtureRequest, recovery: bool):
    ctx = native_jobs
    if recovery:
        restore_commands = request.getfixturevalue("postgres_restore_commands")
    directory, client, queue = ctx["directory"], ctx["client"], ctx["queue"]
    endpoint = f"/api/artifacts/{directory.name}"
    submitted = client.post(endpoint + "/generation-jobs", json=ctx["body"])
    assert submitted.status_code == 202, submitted.text
    assert client.post(endpoint + "/generation-jobs", json=ctx["body"]).json() == submitted.json()
    completed = ctx["worker"].run_once()
    assert completed.state == "succeeded", completed
    selected = PostgresGenerationRepository(ctx["engine"]).output_directory(directory)
    assert selected != directory and selected.is_dir()
    assert json.loads((selected / "execution_result.json").read_text()) == {"state": "rendered"}
    pdf = (selected / "candidate_profile.pdf").read_bytes()
    (directory / "candidate_profile.pdf").write_bytes(b"changed loose PDF")
    (directory / "candidate_profile.html").write_text("changed loose HTML")
    assert client.get(endpoint + "/candidate_profile.pdf").content == pdf
    metadata = client.get(endpoint + "/metadata")
    assert metadata.status_code == 200 and metadata.json()["artifact_manifest_current"], metadata.text
    assert metadata.json()["debug_artifacts"]["generated_pdf"] == str(selected / "candidate_profile.pdf")
    assert metadata.json()["debug_artifacts"]["artifact_manifest"] == str(selected / "artifact_manifest.json")
    assert metadata.json()["client_ready"] is False
    assert "Synthetic Worker Candidate" not in (selected / "candidate_profile.html").read_text()
    current = PostgresGenerationRepository(ctx["engine"]).current(directory.name)
    assert "job_request" in current.inputs and "native_application_and_runtime" in current.component_source_sha256
    accepted = client.post(endpoint + "/final-acceptance", json=current.output_identity.model_dump(mode="json"))
    assert accepted.status_code == 200 and accepted.json()["client_ready"], accepted.text
    assert client.get(endpoint + "/metadata").json()["client_ready"]
    with pytest.raises(RuntimeError, match="bundle references cannot be dropped"):
        migrate_database(ctx["engine"], "0006_workflow_jobs", downgrade=True)
    assert PostgresGenerationRepository(ctx["engine"]).output_directory(directory) == selected
    if recovery:
        from tests.helpers.postgres import assert_local_backup_restore
        before_metadata = client.get(endpoint + "/metadata").json()
        assert_local_backup_restore(ctx["engine"], directory, directory.parent / "native_restore_backup", restore_commands)
        assert client.get(endpoint + "/metadata").json() == before_metadata
        assert client.get(endpoint + "/candidate_profile.pdf").content == pdf
        assert ctx["queue"].get(directory.name, completed.job_id) == completed
    repeated = client.post(endpoint + "/generation-jobs", json=ctx["body"])
    assert repeated.status_code == 202 and repeated.json()["job_id"] == completed.job_id
    assert repeated.json()["state"] == "succeeded"
    restarted = subprocess.run([sys.executable, "-B", "-m", "app.job_worker", "--once"], cwd=ROOT,
        env={**os.environ, "DATABASE_URL": ctx["url"], "GENERATED_OUTPUTS_DIR": str(directory.parent)},
        capture_output=True, text=True, timeout=30)
    assert restarted.returncode == 0 and not restarted.stdout.strip(), restarted.stderr
    with ctx["engine"].connect() as connection:
        assert len(connection.execute(select(generation_versions)).all()) == 2
        payload = connection.scalar(select(workflow_jobs.c.payload))
    assert "Synthetic Worker Candidate" not in json.dumps(payload) and "worker@example.invalid" not in json.dumps(payload)
    # A published file changing is an explicit failure, never a root-file fallback.
    (selected / "candidate_profile.pdf").write_bytes(b"tampered bundle")
    assert client.get(endpoint + "/candidate_profile.pdf").status_code == 500
    assert not client.get(endpoint + "/metadata").json()["client_ready"]


@pytest.mark.parametrize("failure", ["signal_exit", "source_drift", "corrupt_output", "cancel_at_publish", "profile_at_publish", "publication_failure"])
def test_native_worker_failure_preserves_previous_preview(native_jobs, monkeypatch: pytest.MonkeyPatch, failure: str):
    ctx = native_jobs
    directory, client, worker = ctx["directory"], ctx["client"], ctx["worker"]
    endpoint = f"/api/artifacts/{directory.name}"
    submitted = client.post(endpoint + "/generation-jobs", json=ctx["body"])
    assert submitted.status_code == 202, submitted.text
    if failure == "signal_exit":
        monkeypatch.setattr(worker, "_start", lambda bundle, log: subprocess.Popen(
            [sys.executable, "-c", "import os,signal,resource; resource.setrlimit(resource.RLIMIT_CORE,(0,0)); os.kill(os.getpid(), signal.SIGBUS)"],
            cwd=bundle, stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
    elif failure == "source_drift":
        import app.job_worker as module
        monkeypatch.setattr(module, "execution_fingerprint", lambda: {"native_application_and_runtime": "0" * 64})
    elif failure in {"corrupt_output", "cancel_at_publish", "profile_at_publish"}:
        original = worker._manifest

        def interrupt(claim, bundle):
            if failure == "corrupt_output":
                (bundle / "candidate_profile.pdf").write_bytes(b"corrupt child output")
            elif failure == "cancel_at_publish":
                ctx["queue"].cancel(directory.name, claim.job.job_id)
            else:
                profile = CandidateProfile.model_validate_json((directory / "candidate_profile.json").read_text())
                saved = client.post(endpoint + "/profiles/revisions", json={"profile": {
                    **profile.model_dump(mode="json"), "full_name": "Synthetic Revised Candidate"}})
                assert saved.status_code == 200, saved.text
            return original(claim, bundle)

        monkeypatch.setattr(worker, "_manifest", interrupt)
    else:
        publish = PostgresGenerationRepository.publish

        def fail_after_publication(self, connection, manifest, layout_spec, **kwargs):
            publish(self, connection, manifest, layout_spec, **kwargs)
            raise ArtifactStoreConflict("Synthetic conflict after generation insert")

        monkeypatch.setattr(PostgresGenerationRepository, "publish", fail_after_publication)
    result = worker.run_once()
    assert result.state == ("needs_attention" if failure == "signal_exit" else "cancelled" if failure == "cancel_at_publish" else "failed")
    assert client.get(endpoint + "/candidate_profile.pdf").content == ctx["pdf"]
    assert client.get(endpoint + "/metadata").json()["client_ready"] is (failure != "profile_at_publish")
    if failure == "signal_exit":
        exit_path = next((directory / "jobs").glob("*/attempt_*/artifact_*/child_exit.json"))
        assert json.loads(exit_path.read_text())["returncode"] == -signal.SIGBUS
    with ctx["engine"].connect() as connection:
        assert len(connection.execute(select(generation_versions)).all()) == 1
    assert ctx["queue"].claim() is None


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_native_worker_stops_child_before_cancelled_or_expired_work_can_publish(native_jobs, monkeypatch: pytest.MonkeyPatch, stop: str):
    from app.jobs import JobSubmission, NativeJobCreate
    from app.job_worker import native_submission
    from app.storage.postgres import PostgresProfileRevisionRepository
    ctx = native_jobs
    directory, worker, queue = ctx["directory"], ctx["worker"], ctx["queue"]
    revision = PostgresProfileRevisionRepository(ctx["engine"]).current(directory.name)
    submission = native_submission(directory, revision, NativeJobCreate.model_validate(ctx["body"]))
    if stop == "deadline":
        submission = JobSubmission(idempotency_key="deadline", payload=submission.payload.model_copy(update={"timeout_seconds": 1}))
    job = queue.submit(submission)
    processes = []

    def start(bundle, log):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(process)
        if stop == "cancel":
            queue.cancel(directory.name, job.job_id)
        return process

    monkeypatch.setattr(worker, "_start", start)
    started = time.monotonic()
    stopped = worker.run_once()
    assert time.monotonic() - started < 6
    assert processes and processes[0].poll() is not None
    assert stopped.state == ("cancelled" if stop == "cancel" else "failed")
    assert queue.claim() is None  # Reap an execution whose deadline expired.
    terminal = queue.get(directory.name, job.job_id)
    assert terminal.state == ("cancelled" if stop == "cancel" else "failed")
    assert terminal.error_code == ("cancelled" if stop == "cancel" else "deadline_exceeded")
    with ctx["engine"].connect() as connection:
        assert len(connection.execute(select(generation_versions)).all()) == 1


def test_job_api_cancellation_and_uploaded_target_rejection(native_jobs):
    ctx = native_jobs
    directory, client = ctx["directory"], ctx["client"]
    endpoint = f"/api/artifacts/{directory.name}"
    for confirmation in (False, 1, "true"):
        invalid = client.post(endpoint + "/generation-jobs", json={**ctx["body"], "local_synthetic": confirmation})
        assert invalid.status_code == 422
    job = client.post(endpoint + "/generation-jobs", json=ctx["body"]).json()
    cancelled = client.post(endpoint + f"/jobs/{job['job_id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    assert ctx["worker"].run_once() is None
    (directory / "target_format.json").write_text("{}")
    rejected = client.post(endpoint + "/generation-jobs", json={**ctx["body"], "idempotency_key": "uploaded"})
    assert rejected.status_code == 409 and "uploaded" in rejected.text.lower()


def test_killed_native_worker_recovers_in_new_attempt_without_publishing_old_bundle(native_jobs):
    ctx = native_jobs
    directory, client = ctx["directory"], ctx["client"]
    endpoint = f"/api/artifacts/{directory.name}"
    job = client.post(endpoint + "/generation-jobs", json=ctx["body"]).json()
    process = subprocess.Popen([sys.executable, "-B", "-m", "app.job_worker", "--once"], cwd=ROOT,
        env={**os.environ, "DATABASE_URL": ctx["url"], "GENERATED_OUTPUTS_DIR": str(directory.parent)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            children = subprocess.run(["pgrep", "-P", str(process.pid)], capture_output=True, text=True)
            if children.returncode == 0 and children.stdout.strip():
                os.kill(process.pid, signal.SIGSTOP)
                break
            assert process.poll() is None, process.communicate(timeout=1)
            time.sleep(0.01)
        else:
            pytest.fail("The native worker did not start a render child")
        old_bundle = next((directory / "jobs" / job["job_id"]).glob("attempt_*/artifact_*"))
        while time.monotonic() < deadline and not (old_bundle / "execution_result.json").is_file():
            time.sleep(0.05)
        assert (old_bundle / "execution_result.json").is_file()
        os.kill(process.pid, signal.SIGKILL)
        assert process.wait(timeout=5) == -signal.SIGKILL
    finally:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    assert client.get(endpoint + "/candidate_profile.pdf").content == ctx["pdf"]
    with ctx["engine"].begin() as connection:
        connection.execute(update(workflow_jobs).where(workflow_jobs.c.job_id == job["job_id"])
            .values(lease_expires_at=func.clock_timestamp() - text("interval '1 second'")))
    recovered = ctx["worker"].run_once()
    assert recovered.state == "succeeded" and recovered.attempt_count == 2, recovered
    with ctx["engine"].connect() as connection:
        attempts = connection.execute(select(job_attempts).order_by(job_attempts.c.attempt_number)).mappings().all()
        assert len(connection.execute(select(generation_versions)).all()) == 2
    assert [attempt["state"] for attempt in attempts] == ["abandoned", "succeeded"]
    assert attempts[0]["bundle_path"] != attempts[1]["bundle_path"]
    assert old_bundle.is_dir() and (old_bundle / "execution_result.json").is_file()
