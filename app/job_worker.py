"""Local deterministic job execution and clean-exit, fenced bundle publication."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
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
from app.jobs import (JobClaim, JobError, JobProgress, JobRecord, JobSubmission, NativeChildExit,
                      NativeGenerationPayload, NativeJobCreate, TargetGenerationPayload,
                      TargetProofPayload, refuse_live_execution)
from app.profile_approval import ApprovedProfileVersion, load_profile_revision
from app.storage.artifact_store import ArtifactStoreConflict, ArtifactStoreError
from app.storage.job_queue import JobLeaseLost, PostgresJobQueue
from app.storage.postgres import PostgresGenerationRepository, PostgresProfileRevisionRepository, artifact_sessions, postgres_engine
from app.template_analysis.artifacts import (ADOBE_RAW_FILENAME, LAYOUT_SPEC_FILENAME,
                                            NORMALIZED_LAYOUT_FILENAME, load_layout_template_spec)
from app.template_analysis.schemas import LayoutTemplateSpec, built_in_layout_template_spec


ROOT = Path(__file__).resolve().parents[1]
#: Non-secret execution facts the child must see so its renderer identity and
#: model settings match the submitting process; no key or credential is passed.
CHILD_ENV = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "CHROME_BIN", "CHROME_EXECUTABLE",
             "CHROME_EXPORT_TIMEOUT_SECONDS", "DEEPSEEK_BASE_URL", "VISUAL_API_BASE",
             "AGENTIC_MAX_MODEL_REQUESTS", "AGENTIC_MAX_TOOL_CALLS", "AGENTIC_MAX_REPAIR_ROUNDS")
#: Bounded stage weights; variants own the middle of the recorded progress.
STAGE_PROGRESS = {"snapshot": 5, "render": 20, "validate": 70, "publish": 90, "review_ready": 95}


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


def analysis_checksum(directory: Path) -> str:
    """The frozen target-analysis identity: Adobe raw plus normalized evidence."""
    adobe = (directory / ADOBE_RAW_FILENAME).read_bytes()
    return hashlib.sha256(adobe + b"\0" + (directory / NORMALIZED_LAYOUT_FILENAME).read_bytes()).hexdigest()


def target_layout_path(directory: Path, design_id: str | None) -> Path:
    if design_id is None:
        return directory / LAYOUT_SPEC_FILENAME
    return directory / "designs" / design_id / "layout_template_spec.json"


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


def target_proof_submission(directory: Path, revision: ApprovedProfileVersion, request,
                            *, layout_spec: LayoutTemplateSpec, target_pdf: Path,
                            target_checksum: str) -> JobSubmission:
    """Freeze every identity an uploaded-target proof job depends on."""
    from app.template_analysis.layout_proof_rendering import active_renderer_version

    if request.execution == "live":
        refuse_live_execution()
    approval = request.presentation_label_approval
    return JobSubmission(idempotency_key=request.idempotency_key, payload=TargetProofPayload(
        artifact_id=directory.name, profile_revision_id=revision.profile_revision_id,
        profile_sha256=revision.profile_sha256, source_draft_sha256=revision.source_draft_sha256,
        target_sha256=target_checksum, target_stored_filename=target_pdf.name,
        analysis_sha256=analysis_checksum(directory), layout_sha256=layout_spec_sha256(layout_spec),
        design_id=request.design_id,
        presentation_label_catalog_sha256=approval.catalog_sha256 if approval is not None else None,
        presentation_label_approval=approval, renderer_version=active_renderer_version(),
        component_source_sha256=execution_fingerprint(),
    ))


def target_generation_submission(directory: Path, revision: ApprovedProfileVersion, request,
                                 *, layout_spec: LayoutTemplateSpec, target_pdf: Path,
                                 target_checksum: str, approval) -> JobSubmission:
    """Freeze the target generation plus the approved proof set it renders from."""
    from app.template_analysis.layout_proof_rendering import active_renderer_version
    from app.template_analysis.layout_proof_service import require_review_evidence

    if request.execution == "live":
        refuse_live_execution()
    if approval is None or approval.approval_id != request.proof_approval_id or not approval.is_approved:
        raise ValueError("Target generation requires the approved proof set named by this request")
    require_review_evidence(approval, required_execution="offline-renderer/1")
    inputs = capture_generation_inputs(directory, revision_id=revision.profile_revision_id,
                                       layout_path=target_layout_path(directory, request.design_id),
                                       target_filename=target_pdf.name)
    return JobSubmission(idempotency_key=request.idempotency_key, payload=TargetGenerationPayload(
        artifact_id=directory.name, profile_revision_id=revision.profile_revision_id,
        profile_sha256=revision.profile_sha256, source_draft_sha256=revision.source_draft_sha256,
        target_sha256=target_checksum, target_stored_filename=target_pdf.name,
        analysis_sha256=analysis_checksum(directory), layout_sha256=layout_spec_sha256(layout_spec),
        design_id=request.design_id,
        presentation_label_catalog_sha256=approval.presentation_label_catalog_sha256,
        presentation_label_approval=(approval.presentation_labels[0]
                                     if len(approval.presentation_labels) == 1 else None),
        renderer_version=active_renderer_version(),
        component_source_sha256=execution_fingerprint(),
        proof_approval_id=approval.approval_id, proof_set_sha256=inputs["layout_proof_approval"].sha256,
        inputs=inputs, blind_profile=request.blind_profile,
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

    def _verify_target_inputs(self, claim: JobClaim) -> Path:
        """Every frozen uploaded-target identity must still resolve unchanged."""
        from app.template_analysis.layout_proof_rendering import active_renderer_version

        payload = claim.payload
        directory = self.outputs / payload.artifact_id
        if (execution_fingerprint() != payload.component_source_sha256
                or active_renderer_version() != payload.renderer_version):
            raise ValueError("The frozen target execution inputs changed")
        if (layout_spec_sha256(load_layout_template_spec(target_layout_path(directory, payload.design_id)))
                != payload.layout_sha256):
            raise ValueError("The approved layout spec changed")
        if (analysis_checksum(directory) != payload.analysis_sha256
                or file_sha256(directory / payload.target_stored_filename) != payload.target_sha256):
            raise ValueError("The target document or its analysis changed")
        revision = PostgresProfileRevisionRepository(self.queue.engine).current(payload.artifact_id)
        if (revision is None or revision.profile_revision_id != payload.profile_revision_id
                or revision.profile_sha256 != payload.profile_sha256
                or revision.source_draft_sha256 != payload.source_draft_sha256):
            raise ValueError("The current saved profile changed")
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
        self._write_snapshot_index(claim, bundle)
        return bundle

    def _snapshot_target(self, claim: JobClaim, directory: Path) -> Path:
        """Freeze the artifact tree for one attempt; prior attempt bundles stay out."""
        bundle = (directory / claim.bundle_path).resolve()
        bundle.relative_to(directory.resolve())
        shutil.copytree(directory, bundle, symlinks=False,
                        ignore=shutil.ignore_patterns("jobs", "*.sqlite3"))
        self._write_snapshot_index(claim, bundle)
        return bundle

    @staticmethod
    def _write_snapshot_index(claim: JobClaim, bundle: Path) -> None:
        # This index is a derived execution snapshot of the database-selected revision.
        revision_id = claim.payload.profile_revision_id
        (bundle / "profiles").mkdir(parents=True, exist_ok=True)
        (bundle / "profiles" / "index.json").write_text(json.dumps({
            "current": revision_id, "versions": {revision_id: {"superseded": False, "superseded_at": None}},
        }))
        (bundle / "job_request.json").write_text(claim.payload.model_dump_json(indent=2))

    @staticmethod
    def _seed_reused_variants(claim: JobClaim, bundle: Path) -> None:
        """Copy a matching variant checkpoint from an earlier attempt of this job.

        The child still re-checks every frozen identity before it reports the
        variant as ``reused``; a mismatch is simply rendered again.
        """
        from app.template_analysis.layout_proof_validation import reusable_variant_evidence

        payload = claim.payload
        previous = sorted((bundle.parents[2] / claim.job.job_id).glob(f"attempt_*/{payload.artifact_id}"))
        for attempt in reversed([path for path in previous if path != bundle]):
            for variant in ("short", "medium", "long"):
                evidence = reusable_variant_evidence(attempt, _variant(variant),
                                                     target_checksum=payload.target_sha256,
                                                     layout_sha256=payload.layout_sha256,
                                                     renderer_version=payload.renderer_version,
                                                     execution_mode="offline-renderer/1")
                if evidence is None:
                    continue
                proof_id = evidence.render_result.proof_id
                for relative in (Path("layout_proofs") / "variants" / variant,
                                 Path("layout_proofs") / proof_id):
                    source = attempt / relative
                    if source.is_dir():
                        shutil.copytree(source, bundle / relative, dirs_exist_ok=True)
            return

    def _start(self, bundle: Path, log, entry: str = "--render") -> subprocess.Popen:
        environment = {name: value for name, value in os.environ.items() if name in CHILD_ENV}
        environment.update(LLM_PROVIDER="mock", API_LLM_PROVIDER="mock", GENERATION_STRATEGY="agentic",
                           TEMPLATE_DESIGN_STRATEGY="measured_template", PYDANTIC_AI_NO_BANNER="1")
        return subprocess.Popen([sys.executable, "-B", "-m", "app.job_worker", entry, str(bundle)],
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

    def _target_manifest(self, claim: JobClaim, bundle: Path) -> tuple[ArtifactManifest, LayoutTemplateSpec]:
        """Validate a published target-based output against the frozen job request."""
        payload = claim.payload
        child_exit = NativeChildExit.model_validate_json((bundle / "child_exit.json").read_text())
        if (child_exit.job_id != claim.job.job_id or child_exit.attempt_number != claim.job.attempt_count
                or child_exit.returncode != 0
                or json.loads((bundle / "execution_result.json").read_text()) != {"state": "rendered"}
                or TargetGenerationPayload.model_validate_json((bundle / "job_request.json").read_text()) != payload):
            raise ValueError("The child did not complete the frozen request cleanly")
        manifest = ArtifactManifest.model_validate_json((bundle / "artifact_manifest.json").read_text())
        layout = load_layout_template_spec(target_layout_path(bundle, payload.design_id))
        identity = manifest.output_identity
        if (manifest.artifact_id != payload.artifact_id or manifest.execution.mode != "deterministic"
                or identity.profile_revision_id != payload.profile_revision_id
                or identity.profile_sha256 != payload.profile_sha256
                or identity.layout_spec_sha256 != payload.layout_sha256
                or layout_spec_sha256(layout) != payload.layout_sha256
                or manifest.blind_profile != payload.blind_profile
                or current_output_identity(bundle) != identity):
            raise ValueError("The rendered bundle disagrees with its job")
        for name, reference in payload.inputs.items():
            if manifest.inputs.get(name) != reference:
                raise ValueError("The rendered bundle used different inputs")
        references = [*manifest.inputs.values(), *manifest.outputs.values(),
                      *(check.report for check in manifest.checks.values() if check.report is not None)]
        if any(identify_file(bundle, bundle / reference.path) != reference for reference in references):
            raise ValueError("A rendered bundle file changed")
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
        return {"generate": self._run_native,
                "create_proofs": self._run_target_proof,
                "generate_target": self._run_target_generation}[claim.job.operation](claim)

    def _run_native(self, claim: JobClaim) -> JobRecord:
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
            return self.queue.complete(claim, self._publisher(claim, manifest, layout, reviewed, debug))
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

    def _publisher(self, claim: JobClaim, manifest: ArtifactManifest, layout: LayoutTemplateSpec,
                   reviewed: CandidateProfile, debug: dict[str, str]):
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

        return publish

    def _run_target_proof(self, claim: JobClaim) -> JobRecord:
        return self._run_target(claim, entry="--render-proof", publish=self._publish_proof)

    def _run_target_generation(self, claim: JobClaim) -> JobRecord:
        return self._run_target(claim, entry="--render-target",
                                publish=self._publish_target_generation)

    def _run_target(self, claim: JobClaim, *, entry: str, publish) -> JobRecord:
        process = None
        exit_recorded = False
        bundle = None
        try:
            directory = self._verify_target_inputs(claim)
            bundle = self._snapshot_target(claim, directory)
            if claim.job.operation == "create_proofs":
                self._seed_reused_variants(claim, bundle)
            if not self.queue.heartbeat(claim, progress=5, stage="snapshot", variants=_variant_states(claim)):
                return self.queue.get(claim.job.artifact_id, claim.job.job_id)
            with (bundle / "worker.log").open("wb") as log:
                process = self._start(bundle, log, entry)
                stopped = self._supervise(claim, bundle, process)
                if stopped is not None:
                    return stopped
                self._record_exit(claim, bundle, process)
                exit_recorded = True
                if process.returncode != 0:
                    return self._child_failure(claim, bundle)
            final = _read_progress(bundle)
            if not self.queue.heartbeat(claim, progress=90, stage=final.stage,
                    variants={name.value: state for name, state in final.variants.items()}):
                return self.queue.get(claim.job.artifact_id, claim.job.job_id)
            return self.queue.complete(claim, publish(claim, bundle))
        except JobLeaseLost:
            self.queue.recover_expired()
            return self.queue.get(claim.job.artifact_id, claim.job.job_id)
        except (ArtifactStoreConflict, ValueError, OSError, KeyError, RuntimeError) as exc:
            try:
                return self.queue.fail(claim, error="publication_conflict" if isinstance(exc, ArtifactStoreConflict) else "inputs_changed")
            except JobLeaseLost:
                self.queue.recover_expired()
                return self.queue.get(claim.job.artifact_id, claim.job.job_id)
        finally:
            if process is not None:
                self._stop(process)
                if bundle is not None and not exit_recorded:
                    self._record_exit(claim, bundle, process)

    def _supervise(self, claim: JobClaim, bundle: Path, process: subprocess.Popen) -> JobRecord | None:
        """Lease, progress and per-stage deadline checks while the child runs."""
        stage_deadline = time.monotonic() + claim.payload.variant_timeout_seconds
        last_key = None
        next_heartbeat = 0.0
        while process.poll() is None:
            if datetime.now(UTC) >= claim.job.deadline_at:
                raise JobLeaseLost("The target execution deadline expired")
            progress = _read_progress(bundle)
            key = (progress.stage, tuple(sorted((name.value, state) for name, state in progress.variants.items())))
            if key != last_key:
                last_key, stage_deadline = key, time.monotonic() + claim.payload.variant_timeout_seconds
            if time.monotonic() >= stage_deadline:
                self._stop(process)
                return self.queue.fail(claim, error="deadline_exceeded")
            if time.monotonic() >= next_heartbeat:
                if not self.queue.heartbeat(claim, progress=_progress_percent(claim, progress),
                        stage=progress.stage,
                        variants={name.value: state for name, state in progress.variants.items()}):
                    return self.queue.get(claim.job.artifact_id, claim.job.job_id)
                next_heartbeat = time.monotonic() + 1
            time.sleep(0.1)
        return None

    def _child_failure(self, claim: JobClaim, bundle: Path) -> JobRecord:
        failure = json.loads((bundle / "failure.json").read_text()) if (bundle / "failure.json").exists() else {}
        code = failure.get("error_code", "execution_failed")
        error: JobError = code if code in {"budget_exceeded", "inputs_changed", "execution_failed",
                                           "provider_outcome_unknown"} else "execution_failed"
        return self.queue.fail(claim, error=error,
                               state="needs_attention" if error == "provider_outcome_unknown" else "failed")

    def _publish_proof(self, claim: JobClaim, bundle: Path):
        """Copy the immutable proof set, then record the pending aggregate fenced."""
        from app.template_analysis.layout_proof_service import (load_layout_proof_approval,
                                                                verify_approval_files)

        approval = load_layout_proof_approval(bundle)
        if approval is None or approval.state != "pending":
            raise ValueError("The attempt has no complete pending proof set")
        if {evidence.render_result.execution_mode for evidence in approval.variants.values()} \
                != {"offline-renderer/1"}:
            raise ValueError("An offline proof job can only publish offline-rendered evidence")
        if (approval.artifact_id != claim.payload.artifact_id
                or approval.target_checksum != claim.payload.target_sha256
                or approval.layout_spec_checksum != claim.payload.layout_sha256
                or approval.renderer_version != claim.payload.renderer_version
                or sorted(evidence.variant.value for evidence in approval.variants.values())
                != ["long", "medium", "short"]):
            raise ValueError("The proof set disagrees with its job")
        verify_approval_files(bundle, approval)
        artifact_dir = self.outputs / claim.payload.artifact_id
        shutil.copytree(bundle / "layout_proofs", artifact_dir / "layout_proofs", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("approval.json"))

        def publish(connection):
            from app.storage.postgres import PostgresLayoutProofRepository

            PostgresLayoutProofRepository(self.queue.engine).save(approval, connection=connection)
            return approval.approval_id

        return publish

    def _publish_target_generation(self, claim: JobClaim, bundle: Path):
        manifest, layout = self._target_manifest(claim, bundle)
        reviewed = CandidateProfile.model_validate_json((bundle / "generated_profile.json").read_text())
        debug = {name: str(bundle / filename) for name, filename in {
            "generated_html": "candidate_profile.html", "generated_pdf": "candidate_profile.pdf",
            "artifact_manifest": "artifact_manifest.json", "generation_metadata": "generation_metadata.json",
            "production_render_plan": "production_render_plan.json", "generated_profile": "generated_profile.json",
            "client_render_context": "client_render_context.json", "missing_fields": "missing_fields.json",
            "content_validation": "content_validation.json", "structure_validation": "structure_validation.json",
            "layout_proof_approval": "layout_proofs/approval.json",
        }.items()}
        debug["profile_revision_id"] = claim.payload.profile_revision_id
        return self._publisher(claim, manifest, layout, reviewed, debug)


def _variant(name: str):
    from app.template_analysis.layout_proof_schemas import LengthVariant

    return LengthVariant(name)


def _variant_states(claim: JobClaim) -> dict[str, str]:
    if claim.job.operation == "create_proofs":
        return {name: "pending" for name in ("short", "medium", "long")}
    return {}


def _read_progress(bundle: Path) -> JobProgress:
    path = bundle / "progress.json"
    if not path.exists():
        return JobProgress(stage="snapshot")
    try:
        return JobProgress.model_validate_json(path.read_text())
    except ValueError:
        return JobProgress(stage="snapshot")


def _progress_percent(claim: JobClaim, progress: JobProgress) -> int:
    base = STAGE_PROGRESS.get(progress.stage, 5)
    if claim.job.operation != "create_proofs":
        return max(base, 20)
    done = sum(1 for state in progress.variants.values() if state in {"validated", "reused"})
    return max(base, min(20 + 60 * done // 3, 89))


def install_offline_renderer() -> None:
    """Install the child's mock/offline lane: A HTML compilation plus Chrome export.

    Job children never reach a paid provider: live target-provider execution
    needs the B1A ProviderPolicy/authorization gates, so the local lane renders
    the reviewed context deterministically and records deterministic provenance.
    """
    from app.generation import agentic, chrome_html_to_pdf, service as generation_service
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.html_renderer import render_layout_template
    from pypdf import PdfReader

    original_writer = generation_service.write_artifact_manifest

    def render(*, context, layout_spec, target_pdf, evidence_dir, output_dir,
               presentation_label_approval=None, layout_proof_approval=None,
               max_model_requests=None, max_tool_calls=None):
        _, _, html = render_layout_template(layout_spec,
            candidate_document_from_context(context, candidate_id="local-job-candidate",
                include_subheading=layout_spec.header.show_candidate_subheading),
            target_sha256=hashlib.sha256(Path(target_pdf).read_bytes()).hexdigest(),
            page_count=len(PdfReader(target_pdf).pages))
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=False)
        source = directory / "render.html"
        source.write_text(html, encoding="utf-8")
        return source, chrome_html_to_pdf.export_html_to_pdf(source, directory / "render.pdf"), {
            "execution": "local-job/offline", "model_requests": 0, "tool_calls": 0}

    # The proof/render evidence records the renderer that actually ran; the
    # offline substitution is never mistaken for the agentic provider owner.
    render.execution_mode = "offline-renderer/1"
    agentic.render_agentic = render
    # The offline lane runs no model loop, so its manifest records the real
    # deterministic execution instead of agentic provenance it cannot have.
    generation_service.write_artifact_manifest = lambda *args, **kwargs: original_writer(
        *args, **{**kwargs, "uses_agentic": False})


class _ProgressWriter:
    def __init__(self, bundle: Path) -> None:
        self.path = bundle / "progress.json"
        self.state = JobProgress(stage="snapshot")

    def write(self, stage: str, variants: dict[str, str] | None = None, *,
              provider_call_in_flight: bool = False) -> None:
        from app.template_analysis.layout_proof_schemas import LengthVariant

        self.state = JobProgress(stage=stage,
            variants={LengthVariant(name): state for name, state in (variants or {}).items()},
            provider_call_in_flight=provider_call_in_flight)
        self.path.write_text(self.state.model_dump_json())


def record_failure(bundle: Path, error_code: str, exc: BaseException) -> None:
    (bundle / "failure.json").write_text(json.dumps(
        {"error_code": error_code, "message": str(exc)[:2000]}, indent=2))


def provider_outcome_unknown(exc: BaseException) -> bool:
    """A sent provider request whose charge/result status was never confirmed."""
    seen = 0
    while exc is not None and seen < 10:
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return True
        exc, seen = exc.__cause__, seen + 1
    return False


def budget_exhausted(exc: BaseException) -> bool:
    seen = 0
    while exc is not None and seen < 10:
        if getattr(exc, "terminal", None) == "budget_exhausted":
            return True
        exc, seen = exc.__cause__, seen + 1
    return False


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


def render_target_snapshot(bundle: Path) -> None:
    """Render an approved, identity-matched target-based generation offline."""
    os.environ["LLM_PROVIDER"] = "mock"
    from app.generation.contracts import GenerateRequest
    from app.generation.service import GenerationSettings, generate_outputs

    payload = TargetGenerationPayload.model_validate_json((bundle / "job_request.json").read_text())
    install_offline_renderer()
    progress = _ProgressWriter(bundle)
    progress.write("render")
    _require_frozen_target(bundle, payload)
    try:
        generate_outputs(
            GenerateRequest(artifact_id=payload.artifact_id,
                            profile_revision_id=payload.profile_revision_id,
                            blind_profile=payload.blind_profile),
            settings=GenerationSettings(outputs_dir=bundle.parent, database_url=None,
                                        local_database_path=bundle.parent / "execution.sqlite3"),
            max_model_requests=payload.max_model_requests, max_tool_calls=payload.max_tool_calls,
            required_execution="offline-renderer/1",
        )
    except BaseException as exc:
        record_failure(bundle, "budget_exceeded" if budget_exhausted(exc) else
                       "provider_outcome_unknown" if provider_outcome_unknown(exc) else "execution_failed", exc)
        raise
    progress.write("review_ready")
    (bundle / "execution_result.json").write_text(json.dumps({"state": "rendered"}))


def render_proof_snapshot(bundle: Path) -> None:
    """Render short/medium/long proofs, reusing only identity-matched checkpoints."""
    os.environ["LLM_PROVIDER"] = "mock"
    from app.template_analysis.layout_proof_rendering import LayoutProofRenderError
    from app.template_analysis.layout_proof_schemas import LengthVariant
    from app.template_analysis.layout_proof_service import (create_proof_variant,
                                                            pending_layout_proof_approval,
                                                            resolve_layout_proof_context,
                                                            save_layout_proof_approval,
                                                            verify_approval_files)
    from app.template_analysis.layout_proof_validation import reusable_variant_evidence

    payload = TargetProofPayload.model_validate_json((bundle / "job_request.json").read_text())
    install_offline_renderer()
    progress = _ProgressWriter(bundle)
    variants = {variant.value: "pending" for variant in LengthVariant}
    try:
        _require_frozen_target(bundle, payload)
        _, layout_spec, target_pdf, target_checksum = resolve_layout_proof_context(
            bundle, payload.artifact_id, payload.design_id)
        if target_checksum != payload.target_sha256:
            raise ValueError("The bundled target document changed")
        progress.write("render", variants)
        outcomes = {}
        for variant in LengthVariant:
            reused = reusable_variant_evidence(bundle, variant, target_checksum=payload.target_sha256,
                layout_sha256=payload.layout_sha256, renderer_version=payload.renderer_version,
                execution_mode="offline-renderer/1")
            if reused is not None:
                outcomes[variant], variants[variant.value] = reused, "reused"
            else:
                variants[variant.value] = "rendering"
                progress.write("render", variants, provider_call_in_flight=True)
                reused = create_proof_variant(artifact_dir=bundle, artifact_id=payload.artifact_id,
                    layout_spec=layout_spec, target_pdf=target_pdf, target_checksum=target_checksum,
                    variant=variant, presentation_label_approval=payload.presentation_label_approval,
                    max_model_requests=payload.max_model_requests, max_tool_calls=payload.max_tool_calls)
                variants[variant.value] = "rendered" if reused.structural.passed else "failed"
                outcomes[variant] = reused
                if not reused.structural.passed:
                    progress.write("validate", variants)
                    raise LayoutProofRenderError(f"variant '{variant.value}' failed structural validation")
            progress.write("validate", variants)
            variants[variant.value] = "validated" if variants[variant.value] != "reused" else "reused"
        approval = pending_layout_proof_approval(artifact_id=payload.artifact_id,
            target_checksum=payload.target_sha256, layout_spec=layout_spec, outcomes=outcomes)
        verify_approval_files(bundle, approval)
        save_layout_proof_approval(bundle, approval, repository=None)
        progress.write("review_ready", variants)
    except BaseException as exc:
        record_failure(bundle, "budget_exceeded" if budget_exhausted(exc) else
                       "provider_outcome_unknown" if provider_outcome_unknown(exc) else "execution_failed", exc)
        raise
    (bundle / "execution_result.json").write_text(json.dumps({"state": "proofs_rendered"}))


def _require_frozen_target(bundle: Path, payload) -> None:
    """The child re-checks the frozen identities before it renders anything."""
    if (analysis_checksum(bundle) != payload.analysis_sha256
            or file_sha256(bundle / payload.target_stored_filename) != payload.target_sha256
            or layout_spec_sha256(load_layout_template_spec(target_layout_path(bundle, payload.design_id)))
            != payload.layout_sha256):
        raise ValueError("The snapshot disagrees with its frozen target identities")
    from app.template_analysis.layout_proof_rendering import active_renderer_version

    if active_renderer_version() != payload.renderer_version:
        raise ValueError("The renderer version changed after submission")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--render", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--render-proof", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--render-target", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    for entry, name in ((args.render, "render"), (args.render_proof, "proof"),
                        (args.render_target, "target")):
        if entry is not None:
            {"render": render_snapshot, "proof": render_proof_snapshot,
             "target": render_target_snapshot}[name](entry.resolve())
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
