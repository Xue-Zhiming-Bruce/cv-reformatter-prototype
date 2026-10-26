"""Real local PostgreSQL checks in the canonical integration lane."""

import io
import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.schema import CreateSchema, DropSchema

from app import main
from app.extraction.candidate_schema import MissingField
from app.storage.postgres import PostgresArtifactStore, metadata, postgres_engine, postgres_url


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration


@pytest.fixture
def postgres_database():
    configured = os.getenv("TEST_DATABASE_URL")
    if configured is None:
        pytest.skip("Set TEST_DATABASE_URL to an isolated local PostgreSQL test database")
    url = postgres_url(configured)
    if url.host not in {"localhost", "127.0.0.1", "::1"} or not url.database.startswith("cv_reformatter_test"):
        pytest.fail("PostgreSQL integration tests require a local cv_reformatter_test database")
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "cv_test_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    scoped_url = url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    engine = postgres_engine(scoped_url)
    try:
        yield scoped_url, engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def _migrate(engine, revision: str = "head", *, downgrade: bool = False) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


def test_postgres_migrations_are_explicit_reversible_and_match_repository(
    postgres_database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, engine = postgres_database
    monkeypatch.setattr(main, "DATABASE_URL", url)
    sqlite_path = tmp_path / "unused.sqlite3"
    monkeypatch.setattr(main, "LOCAL_DATABASE_PATH", sqlite_path)
    client = TestClient(main.app)
    assert inspect(engine).get_table_names() == []
    unavailable = client.get("/api/artifacts")
    assert unavailable.status_code == 500
    assert "migrations" in unavailable.json()["detail"]
    assert not sqlite_path.exists()
    assert inspect(engine).get_table_names() == []  # Requests never create/repair schema.
    upgraded = subprocess.run([sys.executable, "-B", "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=tmp_path, env={**os.environ, "DATABASE_URL": url, "PYTHONPATH": ""},
        capture_output=True, text=True, timeout=30)
    assert upgraded.returncode == 0, upgraded.stderr
    assert client.get("/api/artifacts").json() == {"artifacts": []}
    cli = subprocess.run([sys.executable, "-B", "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "current"],
        cwd=tmp_path, env={**os.environ, "DATABASE_URL": url, "PYTHONPATH": ""},
        capture_output=True, text=True, timeout=30)
    assert cli.returncode == 0 and "0001_artifact_sessions" in cli.stdout, cli.stderr
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
    identity_fields = ("profile_revision_id", "profile_sha256", "layout_template_version",
                       "layout_spec_sha256", "html_sha256", "pdf_sha256")
    accepted = client.post(f"/api/artifacts/{artifact_id}/final-acceptance",
                           json={key: output[key] for key in identity_fields})
    assert accepted.status_code == 200 and accepted.json()["client_ready"], accepted.text
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
    assert set(inspect(engine).get_table_names()) == {"artifact_sessions", "alembic_version"}
    assert (tmp_path / artifact_id / "source_resume.docx").read_bytes() == source.getvalue()


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
