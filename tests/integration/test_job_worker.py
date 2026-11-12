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
    # The arrangement report is published inside the same bundle and explains
    # that exact output; a stale loose file never answers for it.
    published_report = (selected / "composition_report.json").read_bytes()
    stored_report = json.loads(published_report)
    assert stored_report["output_identity"] == current.output_identity.model_dump(mode="json")
    assert stored_report["schema_version"] == "composition-report/1.0"
    assert stored_report["decisions"]
    (directory / "composition_report.json").write_text(
        json.dumps({**stored_report, "artifact_id": "artifact_stale_loose_report"})
    )
    report_download = client.get(endpoint + "/composition_report.json")
    assert report_download.status_code == 200, report_download.text
    assert report_download.json() == stored_report
    assert "Synthetic Worker Candidate" not in json.dumps(stored_report)
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


@pytest.mark.parametrize("failure", ["signal_exit", "source_drift", "retired_configuration", "corrupt_output", "cancel_at_publish", "profile_at_publish", "publication_failure"])
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
    elif failure == "retired_configuration":
        monkeypatch.setenv("GENERATION_STRATEGY", "standard")
        rejected = client.post(endpoint + "/generation-jobs", json={**ctx["body"], "idempotency_key": "retired"})
        assert rejected.status_code == 500 and "generation strateg" in rejected.text.lower(), rejected.text
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


# -- uploaded-target jobs: proof creation and target generation ----------------

from app.generation import service as generation_service  # noqa: E402
from tests.helpers.layout_proof import create_and_approve_proofs, make_proof_pdf_exporter  # noqa: E402
from tests.helpers.synthetic_pdf import build_styled_target_pdf  # noqa: E402


class _InlineChild:
    """Run a job child entry in-process so the pytest renderer seams apply.

    The worker's real contract is a child process; this keeps the same entry
    points, bundle files, exit code and failure record without paying Chrome
    subprocess flakiness per case.
    """

    def __init__(self, bundle: Path, entry: str) -> None:
        from app.job_worker import render_proof_snapshot, render_target_snapshot

        self.returncode = 0
        try:
            {"--render-proof": render_proof_snapshot,
             "--render-target": render_target_snapshot}[entry](bundle)
        except BaseException:  # the entry already recorded its failure code
            self.returncode = 2

    def poll(self) -> int | None:
        return self.returncode


@pytest.fixture
def target_jobs(postgres_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                a_generation_without_model_loop):
    url, engine = postgres_database
    migrate_database(engine)
    monkeypatch.setattr(main, "DATABASE_URL", url)
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "local.sqlite3")
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setenv("LOCAL_JOBS_ENABLED", "1")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    exporter = make_proof_pdf_exporter()
    monkeypatch.setattr(generation_service, "export_html_to_pdf", exporter)
    monkeypatch.setattr("app.generation.chrome_html_to_pdf.export_html_to_pdf", exporter)
    client = TestClient(main.app)
    artifact_id = "artifact_target_jobs"
    directory = tmp_path / artifact_id
    directory.mkdir()
    target = build_styled_target_pdf(tmp_path / "client-format.pdf")
    uploaded = client.post("/api/target-format", data={"artifact_id": artifact_id},
        files={"file": ("client-format.pdf", target.read_bytes(), "application/pdf")})
    assert uploaded.status_code == 200, uploaded.text
    profile = CandidateProfile(full_name="Synthetic Target Candidate", email="target@example.invalid",
                               skills=["Python"], professional_summary="Synthetic summary.")
    (directory / "candidate_profile.json").write_text(profile.model_dump_json())
    saved = client.post(f"/api/artifacts/{artifact_id}/profiles/revisions",
                        json={"profile": profile.model_dump(mode="json")})
    assert saved.status_code == 200, saved.text
    queue = PostgresJobQueue(engine)
    worker = NativeJobWorker(queue, tmp_path)
    monkeypatch.setattr(worker, "_start", lambda bundle, log, entry="--render": _InlineChild(bundle, entry))
    return {"url": url, "engine": engine, "artifact_id": artifact_id, "directory": directory,
            "client": client, "queue": queue, "worker": worker,
            "revision": saved.json()["profile_revision_id"], "profile": profile}


def _current_generation(ctx) -> str | None:
    with ctx["engine"].connect() as connection:
        return connection.scalar(select(artifact_sessions.c.current_generation_id)
                                 .where(artifact_sessions.c.artifact_id == ctx["artifact_id"]))


def _target_body(ctx, key: str) -> dict:
    return {"local_synthetic": True, "profile_revision_id": ctx["revision"], "idempotency_key": key}


def test_target_proof_job_reports_progress_and_survives_reconnect(target_jobs):
    ctx = target_jobs
    endpoint = f"/api/artifacts/{ctx['artifact_id']}"
    submitted = ctx["client"].post(endpoint + "/layout-proof-jobs", json=_target_body(ctx, "proof-1"))
    assert submitted.status_code == 202, submitted.text
    job = submitted.json()
    assert job["operation"] == "create_proofs" and job["state"] == "queued" and job["progress"] == 0
    assert TestClient(main.app).get(endpoint + f"/jobs/{job['job_id']}").json() == job
    assert ctx["client"].post(endpoint + "/layout-proof-jobs", json=_target_body(ctx, "proof-1")).json() == job
    completed = ctx["worker"].run_once()
    assert completed.state == "succeeded", completed
    assert completed.stage == "review_ready" and completed.progress == 100
    assert completed.variants == {"short": "validated", "medium": "validated", "long": "validated"}
    assert completed.result_proof_approval_id and completed.result_generation_id is None
    after = ctx["client"].get(endpoint + f"/jobs/{job['job_id']}").json()
    assert after == json.loads(completed.model_dump_json()) and after["attempt_count"] == 1
    bundle = next((ctx["directory"] / "jobs" / job["job_id"]).glob("attempt_*/*"))
    assert json.loads((bundle / "progress.json").read_text())["stage"] == "review_ready"
    proofs = ctx["client"].get(endpoint + "/layout-proofs").json()
    assert proofs["state"] == "pending" and len(proofs["variants"]) == 3
    # One changed frozen identity under the same key is an explicit conflict.
    edited = ctx["client"].post(f"/api/artifacts/{ctx['artifact_id']}/profiles/revisions",
        json={"profile": {**ctx["profile"].model_dump(mode="json"), "full_name": "Synthetic Revised"}})
    assert edited.status_code == 200, edited.text
    conflict = ctx["client"].post(endpoint + "/layout-proof-jobs",
        json={**_target_body(ctx, "proof-1"), "profile_revision_id": edited.json()["profile_revision_id"]})
    assert conflict.status_code == 409 and "create_proofs" in conflict.text, conflict.text


def test_proof_child_reuses_matched_evidence_and_rerenders_drift(target_jobs, monkeypatch):
    """AC5: a resumed stage re-renders only variants whose identities drifted."""
    import shutil

    from app.jobs import TargetProofJobCreate
    from app.job_worker import render_proof_snapshot, target_proof_submission
    from app.template_analysis.layout_proof_schemas import LengthVariant
    from app.main import _layout_proof_context
    from app.storage.postgres import PostgresProfileRevisionRepository
    from app.template_analysis import layout_proof_service
    from app.template_analysis.layout_proof_validation import reusable_variant_evidence

    ctx = target_jobs
    directory = ctx["directory"]
    create_and_approve_proofs(ctx["client"], ctx["artifact_id"], directory)
    _, layout_spec, target_pdf, target_checksum = _layout_proof_context(directory, ctx["artifact_id"], None)
    revision = PostgresProfileRevisionRepository(ctx["engine"]).current(ctx["artifact_id"])
    submission = target_proof_submission(directory, revision,
        TargetProofJobCreate(local_synthetic=True, profile_revision_id=revision.profile_revision_id,
                             idempotency_key="proof-reuse"),
        layout_spec=layout_spec, target_pdf=target_pdf, target_checksum=target_checksum)
    payload = submission.payload
    bundle = ctx["directory"].parent / "bundle"
    shutil.copytree(directory, bundle)
    (bundle / "job_request.json").write_text(payload.model_dump_json())
    (bundle / "layout_proofs" / "approval.json").unlink()
    checksum, layout, renderer = payload.target_sha256, payload.layout_sha256, payload.renderer_version
    for variant in ("short", "medium", "long"):
        assert reusable_variant_evidence(bundle, LengthVariant(variant), target_checksum=checksum,
                                         layout_sha256=layout, renderer_version=renderer) is not None
    assert reusable_variant_evidence(bundle, LengthVariant.SHORT, target_checksum="0" * 64,
                                     layout_sha256=layout, renderer_version=renderer) is None
    assert reusable_variant_evidence(bundle, LengthVariant.SHORT, target_checksum=checksum,
                                     layout_sha256="0" * 64, renderer_version=renderer) is None

    real = layout_proof_service.create_proof_variant

    def must_not_render(**_kwargs):
        raise AssertionError("A matched variant was re-rendered")

    monkeypatch.setattr(layout_proof_service, "create_proof_variant", must_not_render)
    render_proof_snapshot(bundle)
    assert json.loads((bundle / "progress.json").read_text())["variants"] == {
        "short": "reused", "medium": "reused", "long": "reused"}
    proof_pdf = next((bundle / "layout_proofs").glob("*/proof-medium.pdf"))
    proof_pdf.write_bytes(b"tampered proof")
    assert reusable_variant_evidence(bundle, LengthVariant.MEDIUM, target_checksum=checksum,
                                     layout_sha256=layout, renderer_version=renderer) is None
    (bundle / "layout_proofs" / "approval.json").unlink(missing_ok=True)
    rendered: list[str] = []
    monkeypatch.setattr(layout_proof_service, "create_proof_variant",
                        lambda **kwargs: rendered.append(kwargs["variant"].value) or real(**kwargs))
    render_proof_snapshot(bundle)
    assert rendered == ["medium"]


def test_target_proof_job_cancel_before_publication_publishes_nothing(target_jobs, monkeypatch):
    from app.template_analysis import layout_proof_service

    ctx = target_jobs
    endpoint = f"/api/artifacts/{ctx['artifact_id']}"
    queued = ctx["client"].post(endpoint + "/layout-proof-jobs", json=_target_body(ctx, "proof-cancel")).json()
    assert ctx["client"].post(endpoint + f"/jobs/{queued['job_id']}/cancel").json()["state"] == "cancelled"
    assert ctx["worker"].run_once() is None
    job = ctx["client"].post(endpoint + "/layout-proof-jobs", json=_target_body(ctx, "proof-cancel-2")).json()
    real = layout_proof_service.create_proof_variant

    def cancel_then_render(**kwargs):
        ctx["queue"].cancel(ctx["artifact_id"], job["job_id"])
        return real(**kwargs)

    monkeypatch.setattr(layout_proof_service, "create_proof_variant", cancel_then_render)
    cancelled = ctx["worker"].run_once()
    assert cancelled.state == "cancelled" and cancelled.error_code == "cancelled"
    assert cancelled.result_proof_approval_id is None
    assert not (ctx["directory"] / "layout_proofs" / "approval.json").exists()
    assert ctx["client"].get(endpoint + "/layout-proofs").json()["state"] == "none"


@pytest.mark.parametrize("blocked", ["budget", "unknown_outcome"])
def test_target_proof_job_reports_budget_and_unknown_provider_outcome(target_jobs, monkeypatch, blocked: str):
    from app.generation.agentic import AgenticGenerationError
    from app.template_analysis import layout_proof_service

    ctx = target_jobs
    endpoint = f"/api/artifacts/{ctx['artifact_id']}"

    def refuse(**_kwargs):
        if blocked == "budget":
            error = AgenticGenerationError("synthetic budget stop")
            error.terminal = "budget_exhausted"
            raise error
        raise TimeoutError("synthetic provider timeout after the request was sent")

    monkeypatch.setattr(layout_proof_service, "create_proof_variant", refuse)
    ctx["client"].post(endpoint + "/layout-proof-jobs", json=_target_body(ctx, "proof-blocked"))
    stopped = ctx["worker"].run_once()
    assert stopped.error_code == ("budget_exceeded" if blocked == "budget" else "provider_outcome_unknown")
    assert stopped.state == ("failed" if blocked == "budget" else "needs_attention")
    assert not (ctx["directory"] / "layout_proofs" / "approval.json").exists()


def test_target_proof_job_stage_deadline_stops_the_child(target_jobs, monkeypatch):
    ctx = target_jobs
    import time as real_time

    from app.jobs import JobSubmission, TargetProofJobCreate
    from app.job_worker import target_proof_submission
    from app.main import _layout_proof_context
    from app.storage.postgres import PostgresProfileRevisionRepository

    directory = ctx["directory"]
    _, layout_spec, target_pdf, target_checksum = _layout_proof_context(
        directory, ctx["artifact_id"], None)
    revision = PostgresProfileRevisionRepository(ctx["engine"]).current(ctx["artifact_id"])
    submission = target_proof_submission(directory, revision,
        TargetProofJobCreate(local_synthetic=True, profile_revision_id=revision.profile_revision_id,
                             idempotency_key="proof-deadline"),
        layout_spec=layout_spec, target_pdf=target_pdf, target_checksum=target_checksum)
    ctx["queue"].submit(JobSubmission(idempotency_key="proof-deadline", payload=submission.payload))
    loops = {"count": 0}
    base = real_time.monotonic()

    class _Clock:
        def monotonic(_self):
            loops["count"] += 1
            return base + (0 if loops["count"] < 3 else 10_000)

        def sleep(_self, seconds):
            real_time.sleep(min(seconds, 0.05))

    monkeypatch.setattr("app.job_worker.time", _Clock())
    processes: list[subprocess.Popen] = []

    def start(bundle, log, entry="--render"):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(process)
        return process

    monkeypatch.setattr(ctx["worker"], "_start", start)
    stopped = ctx["worker"].run_once()
    assert stopped.state == "failed" and stopped.error_code == "deadline_exceeded"
    assert processes and processes[0].poll() is not None
    assert ctx["queue"].claim() is None


def test_stale_target_job_lease_cannot_publish(target_jobs):
    from app.storage.job_queue import JobLeaseLost

    ctx = target_jobs
    endpoint = f"/api/artifacts/{ctx['artifact_id']}"
    job = ctx["client"].post(endpoint + "/layout-proof-jobs", json=_target_body(ctx, "proof-lease")).json()
    claim = ctx["queue"].claim()
    assert claim is not None and claim.job.job_id == job["job_id"]
    with ctx["engine"].begin() as connection:
        connection.execute(update(workflow_jobs).where(workflow_jobs.c.job_id == job["job_id"])
            .values(lease_expires_at=func.clock_timestamp() - text("interval '1 second'")))
    with pytest.raises(JobLeaseLost):
        ctx["queue"].complete(claim, lambda connection: "approval_0000000000000000")
    assert not (ctx["directory"] / "layout_proofs" / "approval.json").exists()
    assert ctx["queue"].get(ctx["artifact_id"], job["job_id"]).state == "running"


def test_target_generation_job_publishes_and_keeps_prior_output_on_failure(target_jobs, monkeypatch):
    ctx = target_jobs
    endpoint = f"/api/artifacts/{ctx['artifact_id']}"
    create_and_approve_proofs(ctx["client"], ctx["artifact_id"], ctx["directory"])
    approval_id = ctx["client"].get(endpoint + "/layout-proofs").json()["approval_id"]
    body = {**_target_body(ctx, "generation-1"), "proof_approval_id": approval_id}
    submitted = ctx["client"].post(endpoint + "/target-generation-jobs", json=body)
    assert submitted.status_code == 202, submitted.text
    assert submitted.json()["operation"] == "generate_target"
    completed = ctx["worker"].run_once()
    assert completed.state == "succeeded", completed
    assert _current_generation(ctx) == completed.result_generation_id
    published = ctx["client"].get(endpoint + "/candidate_profile.pdf").content
    # A failing later job leaves that published output current and readable.
    from app.generation import service as generation_service_module

    def fail_render(**_kwargs):
        raise RuntimeError("synthetic renderer failure")

    monkeypatch.setattr(generation_service_module, "render_candidate_document", fail_render)
    ctx["client"].post(endpoint + "/target-generation-jobs",
                       json={**_target_body(ctx, "generation-2"), "proof_approval_id": approval_id})
    failed = ctx["worker"].run_once()
    assert failed.state == "failed"
    assert _current_generation(ctx) == completed.result_generation_id
    assert ctx["client"].get(endpoint + "/candidate_profile.pdf").content == published
