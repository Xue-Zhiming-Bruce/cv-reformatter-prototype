"""Real local PostgreSQL checks in the canonical integration lane."""

import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, update
from sqlalchemy.exc import IntegrityError

from app import main
from app.extraction.candidate_schema import CandidateProfile, MissingField
from app.final_acceptance import FinalOutputAcceptance
from app.generation.artifact_manifest import ArtifactManifest
from app.profile_approval import draft_sha256, save_approved_profile
from app.storage.artifact_store import ArtifactStoreError
from app.storage.postgres import PostgresArtifactStore, PostgresFinalAcceptanceRepository, PostgresGenerationRepository, PostgresProfileRevisionRepository, artifact_sessions, final_output_acceptances, generation_versions, metadata, profile_revisions
from tests.helpers.postgres import migrate_database as _migrate


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration


def test_postgres_migrations_are_explicit_reversible_and_match_repository(
    postgres_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, engine = postgres_database
    monkeypatch.setattr(main, "DATABASE_URL", url)
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    sqlite_path = tmp_path / "unused.sqlite3"
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", sqlite_path)
    client = TestClient(main.app)
    assert inspect(engine).get_table_names() == []
    unavailable = client.get("/api/artifacts")
    assert unavailable.status_code == 500
    assert "migrations" in unavailable.json()["detail"]
    assert not sqlite_path.exists()
    assert inspect(engine).get_table_names() == []  # Requests never create/repair schema.
    _migrate(engine, "0001_artifact_sessions")
    existing_directory = tmp_path / "synthetic_upgrade"
    existing_directory.mkdir()
    with engine.begin() as connection:
        connection.execute(artifact_sessions.insert().values(
            artifact_id="synthetic_upgrade", artifact_dir=str(existing_directory), status="processed",
            debug_artifacts={"original_text": "extracted_text.txt"},
        ))
    _migrate(engine, "0002_profile_revisions")
    profile = CandidateProfile(full_name="Synthetic Upgrade Candidate", skills=["Python"])
    (existing_directory / "candidate_profile.json").write_text(profile.model_dump_json())
    existing_revision = save_approved_profile(existing_directory, artifact_id="synthetic_upgrade", profile=profile,
        source_draft_sha256=draft_sha256(existing_directory), repository=PostgresProfileRevisionRepository(engine))
    _migrate(engine, "0003_generations_acceptance")
    generated = client.post("/api/generate", json={"artifact_id": "synthetic_upgrade",
        "profile_revision_id": existing_revision.profile_revision_id})
    assert generated.status_code == 200, generated.text
    identity_fields = ("profile_revision_id", "profile_sha256", "layout_template_version", "layout_spec_sha256", "html_sha256", "pdf_sha256")
    accepted = client.post("/api/artifacts/synthetic_upgrade/final-acceptance",
        json={key: generated.json()[key] for key in identity_fields})
    assert accepted.status_code == 200, accepted.text
    # Reproduce the earlier raw-JSONB layout representation without rewriting history.
    with engine.begin() as connection:
        legacy = dict(connection.execute(select(generation_versions)).mappings().one())
        legacy.update(generation_id="legacy_generation", layout_spec=json.loads((existing_directory / "generation_layout_spec.json").read_text()))
        connection.execute(generation_versions.insert().values(**legacy))
        connection.execute(update(artifact_sessions).values(current_generation_id="legacy_generation"))
    before_upgrade = PostgresArtifactStore(engine).get_artifact("synthetic_upgrade")
    before_generation = PostgresGenerationRepository(engine).current("synthetic_upgrade")
    before_acceptance = PostgresFinalAcceptanceRepository(engine).load("synthetic_upgrade")
    upgraded = subprocess.run([sys.executable, "-B", "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=tmp_path, env={**os.environ, "DATABASE_URL": url, "PYTHONPATH": ""},
        capture_output=True, text=True, timeout=30)
    assert upgraded.returncode == 0, upgraded.stderr
    preserved = PostgresArtifactStore(engine).get_artifact("synthetic_upgrade")
    assert preserved == before_upgrade
    assert preserved["status"] == "generated" and preserved["debug_artifacts"]["original_text"] == "extracted_text.txt"
    assert preserved["current_profile_revision_id"] == existing_revision.profile_revision_id
    assert PostgresProfileRevisionRepository(engine).current("synthetic_upgrade") == existing_revision
    assert PostgresGenerationRepository(engine).current("synthetic_upgrade") == before_generation
    assert PostgresFinalAcceptanceRepository(engine).load("synthetic_upgrade") == before_acceptance
    assert client.get("/api/artifacts/synthetic_upgrade/metadata").json()["client_ready"]
    cli = subprocess.run([sys.executable, "-B", "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "current"],
        cwd=tmp_path, env={**os.environ, "DATABASE_URL": url, "PYTHONPATH": ""},
        capture_output=True, text=True, timeout=30)
    assert cli.returncode == 0 and "0005_template_design_versions" in cli.stdout, cli.stderr
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []
    _migrate(engine, "base", downgrade=True)
    assert "artifact_sessions" not in inspect(engine).get_table_names()
    _migrate(engine)
    assert client.get("/api/artifacts").json() == {"artifacts": []}


def test_postgres_api_process_generate_and_fresh_process_preserve_artifact_identity(
    postgres_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, engine = postgres_database
    _migrate(engine)
    monkeypatch.setattr(main, "DATABASE_URL", url)
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", tmp_path / "unused.sqlite3")
    monkeypatch.setenv("API_LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    native_template = main.built_in_layout_template_spec

    def signed_zero_template():
        spec = native_template()
        spec.page.margin_right_pt = -0.0  # Valid geometry whose JSON spelling JSONB would change.
        return spec

    monkeypatch.setattr(main, "built_in_layout_template_spec", signed_zero_template)
    resume = Document()
    for line in ("Synthetic Candidate", "candidate@example.test", "Location: Singapore", "Skills: Python, SQL"):
        resume.add_paragraph(line)
    source = io.BytesIO()
    resume.save(source)
    client = TestClient(main.app)
    processed = client.post("/api/process", files={"file": (
        "synthetic.docx", source.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )})
    assert processed.status_code == 200, processed.text
    artifact = processed.json()
    artifact_id = artifact["artifact_id"]
    generated = client.post("/api/generate", json={"artifact_id": artifact_id,
        "profile_revision_id": artifact["profile_revision_id"]})
    assert generated.status_code == 200, generated.text
    output = generated.json()
    directory = tmp_path / artifact_id
    manifest = ArtifactManifest.model_validate_json((directory / "artifact_manifest.json").read_text())
    assert PostgresGenerationRepository(engine).current(artifact_id) == manifest
    identity_fields = ("profile_revision_id", "profile_sha256", "layout_template_version",
                       "layout_spec_sha256", "html_sha256", "pdf_sha256")
    accepted = client.post(f"/api/artifacts/{artifact_id}/final-acceptance",
                           json={key: output[key] for key in identity_fields})
    assert accepted.status_code == 200 and accepted.json()["client_ready"], accepted.text
    acceptance = PostgresFinalAcceptanceRepository(engine).load(artifact_id)
    assert acceptance == FinalOutputAcceptance.model_validate({key: value for key, value in accepted.json().items() if key != "client_ready"})
    (directory / "final_acceptance.json").unlink()  # Database acceptance survives loss of its local mirror.
    before = client.get(f"/api/artifacts/{artifact_id}/metadata").json()
    assert before["status"] == "generated" and before["source_filename"] == "synthetic.docx"
    assert before["client_ready"] and before["artifact_manifest_current"]
    assert "candidate_profile" in before["debug_artifacts"] and "artifact_manifest" in before["debug_artifacts"]
    assert not (tmp_path / "unused.sqlite3").exists()
    engine.dispose()
    code = """import json
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
response = client.get('/api/artifacts')
assert response.status_code == 200, response.text
print(json.dumps(response.json()))
"""
    restarted = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT,
        env={**os.environ, "DATABASE_URL": url, "GENERATED_OUTPUTS_DIR": str(tmp_path),
             "LOCAL_DATABASE_PATH": str(tmp_path / "unused.sqlite3"), "PYDANTIC_AI_NO_BANNER": "1"},
        capture_output=True, text=True, timeout=30)
    assert restarted.returncode == 0, restarted.stderr
    after = json.loads(restarted.stdout)["artifacts"]
    assert len(after) == 1 and after[0] == before
    # The database holds metadata references; document bytes remain in the artifact store.
    assert set(inspect(engine).get_table_names()) == {"artifact_sessions", "candidate_profile_revisions", "generation_versions", "final_output_acceptances", "layout_proof_approvals", "template_design_versions", "alembic_version"}
    assert (tmp_path / artifact_id / "source_resume.docx").read_bytes() == source.getvalue()
    directory = tmp_path / artifact_id
    original_outputs = {name: (directory / name).read_bytes() for name in (
        "generation_metadata.json", "candidate_profile.html", "candidate_profile.pdf",
    )}
    blind = client.post("/api/generate", json={"artifact_id": artifact_id,
        "profile_revision_id": artifact["profile_revision_id"], "blind_profile": True})
    assert blind.status_code == 200 and not blind.json()["client_ready"], blind.text
    blind_outputs = {name: (directory / name).read_bytes() for name in original_outputs}
    for name, content in original_outputs.items():
        (directory / name).write_bytes(content)
    # Reverting loose files cannot override the database's newer generation pointer.
    assert client.get(f"/api/artifacts/{artifact_id}/metadata").json()["client_ready"] is False
    for name, content in blind_outputs.items():
        (directory / name).write_bytes(content)
    repository = PostgresProfileRevisionRepository(engine)
    original_revision = repository.current(artifact_id)
    assert original_revision.profile_revision_id == artifact["profile_revision_id"]
    assert not (directory / "profiles" / "index.json").exists()
    original_path = directory / "profiles" / f"approved_profile_{original_revision.profile_revision_id}.json"
    original_bytes = original_path.read_bytes()
    draft_bytes = (directory / "candidate_profile.json").read_bytes()
    edited = client.post(f"/api/artifacts/{artifact_id}/profiles/revisions", json={"profile": {
        **artifact["profile"], "full_name": "Synthetic Revised Candidate",
        "client_display_rules": {"salary_expectation": "available_upon_request"},
    }})
    assert edited.status_code == 200, edited.text
    new_revision_id = edited.json()["profile_revision_id"]
    assert new_revision_id != original_revision.profile_revision_id
    # A stale local index cannot resurrect acceptance or override the database pointer.
    (directory / "profiles" / "index.json").write_text(json.dumps({"current": original_revision.profile_revision_id}))
    metadata_url = f"/api/artifacts/{artifact_id}/metadata"
    changed = client.get(metadata_url).json()
    assert changed["client_ready"] is False and changed["artifact_manifest_current"] is False
    stale = client.post("/api/generate", json={"artifact_id": artifact_id,
        "profile_revision_id": original_revision.profile_revision_id})
    assert stale.status_code == 409 and stale.json()["detail"]["error_code"] == "approved_profile_superseded"
    assert repository.load(artifact_id, original_revision.profile_revision_id) == original_revision
    assert original_path.read_bytes() == original_bytes
    new_path = directory / "profiles" / f"approved_profile_{new_revision_id}.json"
    new_bytes = new_path.read_bytes()
    new_path.unlink()
    assert client.get(metadata_url).json()["client_ready"] is False
    assert new_path.read_bytes() == new_bytes  # Rebuilt from the canonical database version.
    regenerated = client.post("/api/generate", json={"artifact_id": artifact_id, "profile_revision_id": new_revision_id})
    assert regenerated.status_code == 200, regenerated.text
    assert regenerated.json()["profile_sha256"] == edited.json()["profile_sha256"]
    assert "Synthetic Revised Candidate" in (directory / "candidate_profile.html").read_text()
    assert (directory / "candidate_profile.json").read_bytes() == draft_bytes
    forged = FinalOutputAcceptance(**{key: regenerated.json()[key] for key in identity_fields}, accepted_at=acceptance.accepted_at)
    (directory / "final_acceptance.json").write_text(forged.model_dump_json())
    assert client.get(metadata_url).json()["client_ready"] is False  # Local data cannot grant acceptance in DB mode.
    with engine.connect() as connection:
        history = connection.execute(select(generation_versions).where(generation_versions.c.artifact_id == artifact_id)).mappings().all()
        assert len(history) == 3
        assert any(row["manifest"] == manifest.model_dump(mode="json") for row in history)
        assert len(connection.execute(select(final_output_acceptances)).all()) == 1
    for table in (generation_versions, final_output_acceptances):
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(update(table).where(table.c.artifact_id == artifact_id).values(artifact_id=artifact_id))
    PostgresArtifactStore(engine).record_design_reference(artifact_id="synthetic_other_generation",
        artifact_dir=tmp_path / "synthetic_other_generation", design_id="synthetic", design_state="ready", debug_artifacts={})
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(update(artifact_sessions).where(artifact_sessions.c.artifact_id == "synthetic_other_generation")
                .values(current_generation_id=history[0]["generation_id"]))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(update(profile_revisions).where(
                profile_revisions.c.artifact_id == artifact_id,
            ).values(profile_sha256="0" * 64))
    assert repository.load(artifact_id, original_revision.profile_revision_id) == original_revision


@pytest.mark.parametrize("operation", ["generation", "acceptance"])
def test_postgres_rejects_revision_superseded_between_validation_and_commit(
    postgres_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    url, engine = postgres_database
    _migrate(engine)
    monkeypatch.setattr(main, "DATABASE_URL", url)
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    artifact_id = "synthetic_commit_race"
    directory = tmp_path / artifact_id
    directory.mkdir()
    profile = CandidateProfile(full_name="Synthetic Candidate", skills=["Python"])
    draft = profile.model_dump_json().encode()
    (directory / "candidate_profile.json").write_bytes(draft)
    client = TestClient(main.app)
    saved = client.post(f"/api/artifacts/{artifact_id}/profiles/revisions", json={"profile": profile.model_dump(mode="json")})
    assert saved.status_code == 200, saved.text
    request = {"artifact_id": artifact_id, "profile_revision_id": saved.json()["profile_revision_id"]}
    if operation == "acceptance":
        generated = client.post("/api/generate", json=request)
        assert generated.status_code == 200, generated.text
    owner = PostgresGenerationRepository if operation == "generation" else PostgresFinalAcceptanceRepository
    original = owner.save

    def supersede_before_commit(self, *args, **kwargs):
        updated = client.post(f"/api/artifacts/{artifact_id}/profiles/revisions", json={"profile": {
            **profile.model_dump(mode="json"), "full_name": "Synthetic Newer Candidate",
        }})
        assert updated.status_code == 200, updated.text
        return original(self, *args, **kwargs)

    monkeypatch.setattr(owner, "save", supersede_before_commit)
    if operation == "generation":
        response = client.post("/api/generate", json=request)
    else:
        keys = ("profile_revision_id", "profile_sha256", "layout_template_version", "layout_spec_sha256", "html_sha256", "pdf_sha256")
        response = client.post(f"/api/artifacts/{artifact_id}/final-acceptance", json={key: generated.json()[key] for key in keys})
    assert response.status_code == 409 and "profile" in response.json()["detail"], response.text
    with engine.connect() as connection:
        assert len(connection.execute(select(generation_versions)).all()) == (0 if operation == "generation" else 1)
        assert connection.execute(select(final_output_acceptances)).first() is None
    assert (directory / "candidate_profile.json").read_bytes() == draft
    assert not (directory / "final_acceptance.json").exists()


def test_postgres_profile_history_is_scoped_and_atomic_under_concurrent_saves(
    postgres_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, engine = postgres_database
    _migrate(engine)
    monkeypatch.setattr(main, "DATABASE_URL", url)
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    artifact_id = "synthetic_revision_history"
    directory = tmp_path / artifact_id
    directory.mkdir()
    profile = CandidateProfile(full_name="Synthetic Candidate", skills=["Python"])
    (directory / "candidate_profile.json").write_text(profile.model_dump_json())

    def save(index: int):
        response = TestClient(main.app).post(f"/api/artifacts/{artifact_id}/profiles/revisions",
            json={"profile": {**profile.model_dump(mode="json"), "full_name": f"Synthetic Candidate {index}"}})
        assert response.status_code == 200, response.text
        return index, response.json()["profile_revision_id"]

    with ThreadPoolExecutor(max_workers=6) as workers:
        revisions = dict(workers.map(save, range(12)))
    assert len(set(revisions.values())) == 12
    repository = PostgresProfileRevisionRepository(engine)
    current = repository.current(artifact_id)
    assert current.profile_revision_id in revisions.values()
    with pytest.raises(ArtifactStoreError):
        repository.save(directory, current)  # Duplicate immutable identity rolls back the whole save.
    assert repository.current(artifact_id) == current
    for index, revision_id in revisions.items():
        saved = repository.load(artifact_id, revision_id)
        assert saved.profile.full_name == f"Synthetic Candidate {index}"
        assert saved.source_draft_sha256 == draft_sha256(directory)
    with engine.connect() as connection:
        assert len(connection.execute(select(profile_revisions)).all()) == 12
    assert not (directory / "profiles" / "index.json").exists()
    # A different artifact cannot adopt this revision, even via direct SQL.
    PostgresArtifactStore(engine).record_design_reference(artifact_id="synthetic_other", artifact_dir=tmp_path / "synthetic_other",
        design_id="synthetic", design_state="ready", debug_artifacts={})
    assert repository.load("synthetic_other", current.profile_revision_id) is None
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(update(artifact_sessions).where(artifact_sessions.c.artifact_id == "synthetic_other")
                .values(current_profile_revision_id=current.profile_revision_id))
    assert repository.current(artifact_id) == current
    assert repository.current("synthetic_other") is None


def test_postgres_requires_explicit_import_instead_of_reading_legacy_profile_files(
    postgres_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, engine = postgres_database
    _migrate(engine)
    monkeypatch.setattr(main, "DATABASE_URL", url)
    monkeypatch.setattr(main, "GENERATED_OUTPUTS_DIR", tmp_path)
    artifact_id = "synthetic_legacy_not_imported"
    directory = tmp_path / artifact_id
    directory.mkdir()
    profile = CandidateProfile(full_name="Synthetic Legacy Candidate")
    (directory / "candidate_profile.json").write_text(profile.model_dump_json())
    legacy = save_approved_profile(directory, artifact_id=artifact_id, profile=profile, source_draft_sha256=draft_sha256(directory))
    before = {path: path.read_bytes() for path in (directory / "profiles").iterdir()}
    response = TestClient(main.app).post("/api/generate", json={"artifact_id": artifact_id,
        "profile_revision_id": legacy.profile_revision_id})
    assert response.status_code == 400 and response.json()["detail"]["error_code"] == "approved_profile_invalid"
    assert {path: path.read_bytes() for path in before} == before
    assert not (directory / "candidate_profile.html").exists()
    # An unavailable revision schema cannot redirect a new save into the legacy files.
    _migrate(engine, "0001_artifact_sessions", downgrade=True)
    failed = TestClient(main.app).post(f"/api/artifacts/{artifact_id}/profiles/revisions",
        json={"profile": profile.model_dump(mode="json")})
    assert failed.status_code == 500 and "no filesystem fallback" in failed.json()["detail"]
    assert {path: path.read_bytes() for path in (directory / "profiles").iterdir()} == before
    with engine.connect() as connection:
        assert connection.execute(select(artifact_sessions.c.artifact_id)).first() is None


def test_postgres_concurrent_lifecycle_updates_merge_references_without_lost_fields(
    postgres_database, tmp_path: Path,
) -> None:
    _, engine = postgres_database
    _migrate(engine)
    store = PostgresArtifactStore(engine)
    missing = [MissingField(field_name="salary_expectation", label="Salary expectation", reason="Synthetic missing field")]
    store.record_processed_resume(artifact_id="synthetic_concurrent", artifact_dir=tmp_path,
        source_filename="synthetic.docx", source_file_type="docx", original_pdf_preview_url=None,
        original_preview_error=None, missing_fields=missing, debug_artifacts={"source": "source_resume.docx"})
    created_at = store.get_artifact("synthetic_concurrent")["created_at"]

    def write(index: int) -> None:
        common = dict(artifact_id="synthetic_concurrent", artifact_dir=tmp_path,
                      debug_artifacts={f"reference_{index}": f"synthetic-{index}.json"})
        if index % 3 == 0:
            store.record_target_format(**common, target_format_role="pdf_reference", target_format_filename="target.pdf",
                target_format_download_url="/target.pdf")
        elif index % 3 == 1:
            store.record_generated_outputs(**common, template_name="synthetic", blind_profile=True,
                html_surface_url="/preview.html", pdf_download_url="/preview.pdf", pdf_preview_url="/preview.pdf",
                missing_fields=missing)
        else:
            store.record_design_reference(**common, design_id="synthetic_design", design_state="approved")

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(write, range(24)))
    result = store.get_artifact("synthetic_concurrent")
    assert result["created_at"] == created_at
    assert result["source_filename"] == "synthetic.docx" and result["target_format_filename"] == "target.pdf"
    assert result["template_name"] == "synthetic" and result["blind_profile"] is True
    assert result["missing_field_labels"] == ["Salary expectation"]
    assert result["debug_artifacts"] == {"source": "source_resume.docx", "design_id": "synthetic_design", "design_state": "approved",
        **{f"reference_{index}": f"synthetic-{index}.json" for index in range(24)}}
    assert store.list_artifacts(limit=0) == [result]
