"""Local deterministic job execution and clean-exit, fenced bundle publication."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from sqlalchemy import update, func, literal

from app.extraction.candidate_schema import CandidateProfile
from app.final_acceptance import current_output_identity, file_sha256
from app.generation.artifact_manifest import ArtifactManifest, capture_generation_inputs, identify_file
from app.generation.render_plan import layout_spec_sha256
from app.generation.template_mapper import DEFAULT_TEMPLATE_NAME
from app.jobs import JobClaim, JobError, JobRecord, JobSubmission, NativeChildExit, NativeGenerationPayload, NativeJobCreate
from app.profile_approval import ApprovedProfileVersion, load_profile_revision
from app.storage.artifact_store import ArtifactStoreConflict, ArtifactStoreError
from app.storage.job_queue import JobLeaseLost, PostgresJobQueue
from app.storage.postgres import PostgresGenerationRepository, PostgresProfileRevisionRepository, artifact_sessions, postgres_engine
from app.template_analysis.schemas import LayoutTemplateSpec, built_in_layout_template_spec


ROOT = Path(__file__).resolve().parents[1]


def execution_fingerprint() -> dict[str, str]:
    from app.generation.agentic import AgenticGenerationError, generation_strategy
    try:
        strategy = generation_strategy()
    except AgenticGenerationError as exc:
        raise ValueError("Native jobs reject retired or unknown generation strategies") from exc
    digest = hashlib.sha256()
    digest.update(strategy.encode() + b"\0")
    for path in sorted((ROOT / "app").rglob("*")):
        if path.is_file() and (path.suffix == ".py" or "assets/fonts" in path.as_posix()):
            digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes())
    digest.update(sys.version.encode())
    digest.update(json.dumps({name: os.getenv(name) for name in (
        "CHROME_BIN", "CHROME_EXECUTABLE", "CHROME_EXPORT_TIMEOUT_SECONDS",
    )}, sort_keys=True).encode())
    return {"native_application_and_runtime": digest.hexdigest()}


def native_submission(directory: Path, revision: ApprovedProfileVersion, request: NativeJobCreate) -> JobSubmission:
    if (directory / "target_format.json").exists():
        raise ArtifactStoreConflict("Local jobs currently require a native no-target artifact; uploaded targets need gated provider execution.")
    try:
        fingerprint = execution_fingerprint()
    except ValueError as exc:
        raise ArtifactStoreError("Native jobs reject retired or unknown generation strategies; configure GENERATION_STRATEGY=agentic.") from exc
    inputs = capture_generation_inputs(directory, revision_id=revision.profile_revision_id,
                                       layout_path=None, target_filename=None)
    return JobSubmission(idempotency_key=request.idempotency_key, payload=NativeGenerationPayload(
        artifact_id=directory.name, profile_revision_id=revision.profile_revision_id,
        profile_sha256=revision.profile_sha256, source_draft_sha256=revision.source_draft_sha256,
        inputs=inputs, layout_sha256=layout_spec_sha256(built_in_layout_template_spec()),
        component_source_sha256=fingerprint, blind_profile=request.blind_profile,
    ))


class NativeJobWorker:
    def __init__(self, queue: PostgresJobQueue, outputs: Path) -> None:
        self.queue, self.outputs = queue, outputs.resolve()

    def _verify_inputs(self, claim: JobClaim) -> Path:
        payload = claim.payload
        directory = self.outputs / payload.artifact_id
        if ((directory / "target_format.json").exists()
                or execution_fingerprint() != payload.component_source_sha256
                or layout_spec_sha256(built_in_layout_template_spec()) != payload.layout_sha256):
            raise ValueError("The frozen native execution inputs changed")
        revision = PostgresProfileRevisionRepository(self.queue.engine).current(payload.artifact_id)
        if (revision is None or revision.profile_revision_id != payload.profile_revision_id
                or revision.profile_sha256 != payload.profile_sha256
                or revision.source_draft_sha256 != payload.source_draft_sha256):
            raise ValueError("The current saved profile changed")
        load_profile_revision(directory, payload.profile_revision_id,
                              repository=PostgresProfileRevisionRepository(self.queue.engine))
        current_inputs = capture_generation_inputs(directory, revision_id=payload.profile_revision_id,
                                                   layout_path=None, target_filename=None)
        if current_inputs != payload.inputs:
            raise ValueError("Saved source files changed")
        return directory

    def _snapshot(self, claim: JobClaim, directory: Path) -> Path:
        bundle = (directory / claim.bundle_path).resolve()
        bundle.relative_to(directory.resolve())
        bundle.mkdir(parents=True, exist_ok=False)
        for reference in claim.payload.inputs.values():
            source = (directory / reference.path).resolve()
            source.relative_to(directory.resolve())
            contents = source.read_bytes()
            if len(contents) != reference.size_bytes or hashlib.sha256(contents).hexdigest() != reference.sha256:
                raise ValueError("An input changed during snapshot")
            destination = bundle / reference.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(contents)
        # This index is a derived execution snapshot of the database-selected revision.
        revision_id = claim.payload.profile_revision_id
        (bundle / "profiles" / "index.json").write_text(json.dumps({
            "current": revision_id, "versions": {revision_id: {"superseded": False, "superseded_at": None}},
        }))
        (bundle / "job_request.json").write_text(claim.payload.model_dump_json(indent=2))
        return bundle

    def _start(self, bundle: Path, log) -> subprocess.Popen:
        environment = {name: value for name, value in os.environ.items()
            if name in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "CHROME_BIN", "CHROME_EXECUTABLE", "CHROME_EXPORT_TIMEOUT_SECONDS"}}
        environment.update(LLM_PROVIDER="mock", API_LLM_PROVIDER="mock", GENERATION_STRATEGY="agentic",
                           TEMPLATE_DESIGN_STRATEGY="measured_template", PYDANTIC_AI_NO_BANNER="1")
        return subprocess.Popen([sys.executable, "-B", "-m", "app.job_worker", "--render", str(bundle)],
            cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)

    def _manifest(self, claim: JobClaim, bundle: Path) -> tuple[ArtifactManifest, LayoutTemplateSpec]:
        child_exit = NativeChildExit.model_validate_json((bundle / "child_exit.json").read_text())
        if (child_exit.job_id != claim.job.job_id or child_exit.attempt_number != claim.job.attempt_count
                or child_exit.returncode != 0
                or json.loads((bundle / "execution_result.json").read_text()) != {"state": "rendered"}
                or NativeGenerationPayload.model_validate_json((bundle / "job_request.json").read_text()) != claim.payload):
            raise ValueError("The child did not complete the frozen request cleanly")
        manifest = ArtifactManifest.model_validate_json((bundle / "artifact_manifest.json").read_text())
        layout_source = (bundle / "generation_layout_spec.json").read_text()
        LayoutTemplateSpec.model_validate_json(layout_source)
        # Revalidation normalizes legacy separator defaults; retain the frozen
        # native factory's exact values and verify the original JSON identity.
        layout = built_in_layout_template_spec()
        layout_digest = hashlib.sha256(json.dumps(json.loads(layout_source), sort_keys=True,
            ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        identity = manifest.output_identity
        if (manifest.artifact_id != claim.payload.artifact_id or manifest.execution.mode != "deterministic"
                or identity.profile_revision_id != claim.payload.profile_revision_id
                or identity.profile_sha256 != claim.payload.profile_sha256
                or identity.layout_spec_sha256 != claim.payload.layout_sha256
                or layout_digest != claim.payload.layout_sha256
                or manifest.blind_profile != claim.payload.blind_profile
                or current_output_identity(bundle) != identity):
            raise ValueError("The rendered bundle disagrees with its job")
        for name, reference in claim.payload.inputs.items():
            if manifest.inputs.get(name) != reference:
                raise ValueError("The rendered bundle used different inputs")
        references = [*manifest.inputs.values(), *manifest.outputs.values(),
                      *(check.report for check in manifest.checks.values() if check.report is not None)]
        if any(identify_file(bundle, bundle / reference.path) != reference for reference in references):
            raise ValueError("A rendered bundle file changed")
        manifest = ArtifactManifest.model_validate_json(manifest.model_copy(update={
            "inputs": {**manifest.inputs, "job_request": identify_file(bundle, bundle / "job_request.json")},
            "outputs": {**manifest.outputs, "child_exit": identify_file(bundle, bundle / "child_exit.json"),
                        "missing_fields": identify_file(bundle, bundle / "missing_fields.json")},
            "component_source_sha256": {**manifest.component_source_sha256, **claim.payload.component_source_sha256},
        }).model_dump_json())
        (bundle / "artifact_manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n")
        return manifest, layout

    @staticmethod
    def _record_exit(claim: JobClaim, bundle: Path, process: subprocess.Popen) -> None:
        path = bundle / "child_exit.json"
        if not path.exists() and process.returncode is not None:
            path.write_text(NativeChildExit(job_id=claim.job.job_id,
                attempt_number=claim.job.attempt_count, returncode=process.returncode,
                finished_at=datetime.now(UTC)).model_dump_json(indent=2))

    def run_once(self) -> JobRecord | None:
        claim = self.queue.claim()
        if claim is None:
            return None
        process = None
        exit_recorded = False
        error_code: JobError = "inputs_changed"
        try:
            directory = self._verify_inputs(claim)
            bundle = self._snapshot(claim, directory)
            if not self.queue.heartbeat(claim, progress=10):
                return self.queue.get(claim.job.artifact_id, claim.job.job_id)
            with (bundle / "worker.log").open("wb") as log:
                error_code = "execution_failed"
                process = self._start(bundle, log)
                next_heartbeat = 0.0
                while process.poll() is None:
                    if datetime.now(UTC) >= claim.job.deadline_at:
                        raise JobLeaseLost("The native execution deadline expired")
                    if time.monotonic() >= next_heartbeat:
                        if not self.queue.heartbeat(claim, progress=50):
                            return self.queue.get(claim.job.artifact_id, claim.job.job_id)
                        next_heartbeat = time.monotonic() + 1
                    time.sleep(0.1)
                self._record_exit(claim, bundle, process)
                exit_recorded = True
                if process.returncode != 0:
                    return self.queue.fail(claim, error="worker_exit", state="needs_attention")
            if not self.queue.heartbeat(claim, progress=90):
                return self.queue.get(claim.job.artifact_id, claim.job.job_id)
            error_code = "inputs_changed"
            self._verify_inputs(claim)
            error_code = "execution_failed"
            manifest, layout = self._manifest(claim, bundle)
            reviewed = CandidateProfile.model_validate_json((bundle / "generated_profile.json").read_text())
            debug = {name: str(bundle / filename) for name, filename in {
                "generated_html": "candidate_profile.html", "generated_pdf": "candidate_profile.pdf",
                "artifact_manifest": "artifact_manifest.json", "generation_metadata": "generation_metadata.json",
                "production_render_plan": "production_render_plan.json", "generated_profile": "generated_profile.json",
                "client_render_context": "client_render_context.json", "missing_fields": "missing_fields.json",
                "composition_report": "composition_report.json",
                "content_validation": "content_validation.json", "structure_validation": "structure_validation.json",
            }.items()}
            debug["profile_revision_id"] = claim.payload.profile_revision_id

            def publish(connection):
                generation_id = PostgresGenerationRepository(self.queue.engine).publish(
                    connection, manifest, layout, bundle_path=claim.bundle_path)
                connection.execute(update(artifact_sessions).where(
                    artifact_sessions.c.artifact_id == claim.payload.artifact_id).values(
                        status="generated", updated_at=func.clock_timestamp(), blind_profile=claim.payload.blind_profile,
                        template_name=DEFAULT_TEMPLATE_NAME, needs_review_count=len(reviewed.missing_fields),
                        missing_field_labels=[field.label for field in reviewed.missing_fields],
                        debug_artifacts=artifact_sessions.c.debug_artifacts.op("||")(
                            literal(debug, type_=artifact_sessions.c.debug_artifacts.type)),
                        html_surface_url=f"/api/artifacts/{claim.payload.artifact_id}/candidate_profile.html",
                        pdf_download_url=f"/api/artifacts/{claim.payload.artifact_id}/candidate_profile.pdf",
                        pdf_preview_url=f"/api/artifacts/{claim.payload.artifact_id}/candidate_profile.pdf"))
                return generation_id

            return self.queue.complete(claim, publish)
        except JobLeaseLost:
            self.queue.recover_expired()
            return self.queue.get(claim.job.artifact_id, claim.job.job_id)
        except (ArtifactStoreConflict, ValueError, OSError, KeyError) as exc:
            try:
                return self.queue.fail(claim, error="publication_conflict" if isinstance(exc, ArtifactStoreConflict) else error_code)
            except JobLeaseLost:
                self.queue.recover_expired()
                return self.queue.get(claim.job.artifact_id, claim.job.job_id)
        finally:
            if process is not None:
                self._stop(process)
                if not exit_recorded:
                    self._record_exit(claim, bundle, process)


def render_snapshot(bundle: Path) -> None:
    """Run the shared generation workflow in an isolated, database-free child."""
    os.environ["LLM_PROVIDER"] = "mock"
    from app.generation.contracts import GenerateRequest
    from app.generation.service import GenerationError, GenerationSettings, generate_outputs

    payload = NativeGenerationPayload.model_validate_json(
        (bundle / "job_request.json").read_text()
    )
    try:
        generate_outputs(
            GenerateRequest(
                artifact_id=payload.artifact_id,
                profile_revision_id=payload.profile_revision_id,
                blind_profile=payload.blind_profile,
            ),
            settings=GenerationSettings(
                outputs_dir=bundle.parent,
                database_url=None,
                local_database_path=bundle.parent / "execution.sqlite3",
            ),
        )
    except GenerationError as exc:
        raise SystemExit(f"generation failed: {exc.status_code} {exc.detail}") from exc
    (bundle / "execution_result.json").write_text(json.dumps({"state": "rendered"}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--render", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.render is not None:
        render_snapshot(args.render.resolve())
        return
    if os.getenv("LOCAL_JOBS_ENABLED") != "1" or not os.getenv("DATABASE_URL"):
        parser.error("Local synthetic jobs require LOCAL_JOBS_ENABLED=1 and DATABASE_URL")
    queue = PostgresJobQueue(postgres_engine(os.environ["DATABASE_URL"]))
    worker = NativeJobWorker(queue, Path(os.getenv("GENERATED_OUTPUTS_DIR", "data/generated_outputs")))
    while True:
        result = worker.run_once()
        if args.once:
            if result is not None:
                print(result.model_dump_json())
            return
        if result is None:
            time.sleep(0.5)


if __name__ == "__main__":
    main()
