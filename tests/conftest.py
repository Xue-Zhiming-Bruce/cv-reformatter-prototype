"""Repository-wide offline provider guardrails."""

import pytest

from app import main
from tests.helpers.adobe_evidence import mock_run_adobe_layout


@pytest.fixture
def postgres_database():
    """Own one random schema in an explicitly selected local synthetic database."""
    import os
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.schema import CreateSchema, DropSchema
    from app.storage.postgres import postgres_engine, postgres_url

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
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()


@pytest.fixture
def postgres_restore_commands(postgres_database):
    """Use matching tools in an explicitly selected loopback test container."""
    import json
    import os
    import re
    import subprocess
    from sqlalchemy import text
    from app.storage.postgres import postgres_url

    container = os.getenv("TEST_POSTGRES_CONTAINER")
    if not container:
        pytest.skip("Set TEST_POSTGRES_CONTAINER for the isolated PostgreSQL backup/restore case")
    database_url, engine = postgres_database
    url = postgres_url(database_url)
    inspected = subprocess.run(["docker", "inspect", container], capture_output=True, text=True, timeout=10)
    assert inspected.returncode == 0, inspected.stderr
    info = json.loads(inspected.stdout)[0]
    assert info["State"]["Running"]
    bindings = info["NetworkSettings"]["Ports"].get("5432/tcp") or []
    assert any(item["HostIp"] in {"127.0.0.1", "::1"} and item["HostPort"] == str(url.port or 5432)
               for item in bindings), "Restore tools must belong to the TEST_DATABASE_URL loopback service"
    assert url.username, "Restore checks require an explicit test database username"
    with engine.connect() as connection:
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    assert re.fullmatch(r"cv_test_[0-9a-f]{32}", schema), "Only this fixture's disposable schema may be restored"
    connection_args = ["--host=127.0.0.1", "--port=5432", "--username", url.username,
                       "--dbname", url.database, "--no-password"]
    return {
        "dump": ["docker", "exec", container, "pg_dump", *connection_args,
                 "--schema", schema, "--format=custom", "--no-owner", "--no-acl"],
        "restore": ["docker", "exec", "-i", container, "pg_restore", *connection_args,
                    "--clean", "--if-exists", "--single-transaction", "--exit-on-error", "--no-owner", "--no-acl"],
    }


@pytest.fixture(autouse=True)
def _offline_storage_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """An inherited developer DATABASE_URL must never redirect ordinary tests."""
    monkeypatch.setattr(main, "DATABASE_URL", None)


@pytest.fixture(autouse=True)
def _offline_adobe_target_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """API tests exercise Adobe-shaped evidence without external calls."""
    monkeypatch.setattr(main, "run_adobe_layout", mock_run_adobe_layout)
    monkeypatch.setattr(main, "enrich_colors_from_local_pdf", lambda evidence, _path: evidence)
    monkeypatch.setattr(main, "enrich_badges_from_local_pdf", lambda evidence, _path: evidence)
    monkeypatch.setattr(main, "enrich_rules_from_local_pdf", lambda evidence, _path: evidence)


@pytest.fixture
def a_generation_without_model_loop(monkeypatch: pytest.MonkeyPatch):
    """Lifecycle checks use real A HTML; complete-loop tests restore the adapter.

    Approval/revision/checksum validation and production file staging stay real.
    These cases do not assert model-loop behavior, execution provenance or
    exported layout fidelity. Native manifests still use the production writer.
    """
    from app.generation import agentic, chrome_html_to_pdf
    from app.pipeline.candidate_content import candidate_document_from_context
    from app.pipeline.html_renderer import render_layout_template
    from pathlib import Path
    import hashlib
    from pypdf import PdfReader

    original = agentic.render_agentic
    original_manifest_writer = main.write_artifact_manifest
    monkeypatch.setattr(main, "write_artifact_manifest", lambda *args, **kwargs:
        None if kwargs["uses_agentic"] else original_manifest_writer(*args, **kwargs))
    monkeypatch.setenv("GENERATION_STRATEGY", "agentic")

    def render(*, context, layout_spec, target_pdf, evidence_dir, output_dir,
               presentation_label_approval=None):
        _, _, html = render_layout_template(layout_spec,
            candidate_document_from_context(context, candidate_id="offline-lifecycle",
                include_subheading=layout_spec.header.show_candidate_subheading),
            target_sha256=hashlib.sha256(target_pdf.read_bytes()).hexdigest(),
            page_count=len(PdfReader(target_pdf).pages))
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=False)
        source = directory / "render.html"
        source.write_text(html, encoding="utf-8")
        pdf = chrome_html_to_pdf.export_html_to_pdf(source, directory / "render.pdf")
        return source, pdf, {"test_scope": "A compilation/export; model loop stubbed"}

    monkeypatch.setattr(agentic, "render_agentic", render)
    return original
