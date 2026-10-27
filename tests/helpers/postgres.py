"""Shared migration helper for the canonical PostgreSQL integration checks."""

from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import tarfile

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy import inspect, select, text
from sqlalchemy.schema import DropSchema


def migrate_database(engine: Engine, revision: str = "head", *, downgrade: bool = False) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


def assert_local_backup_restore(
    engine: Engine, artifact_dir: Path, backup_dir: Path, commands: dict[str, list[str]],
) -> None:
    """Destroy and restore only the fixture's schema and its synthetic artifact tree."""
    from app.storage.postgres import metadata

    assert artifact_dir.parent.resolve() == backup_dir.parent.resolve()
    assert artifact_dir.name.startswith("artifact_") and artifact_dir.is_dir()
    with engine.connect() as connection:
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    assert re.fullmatch(r"cv_test_[0-9a-f]{32}", schema)

    def database_snapshot():
        with engine.connect() as connection:
            rows = {table.name: [dict(row) for row in connection.execute(
                select(table).order_by(*table.primary_key.columns)).mappings()]
                for table in metadata.tables.values()}
            rows["alembic_version"] = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            rows["sequences"] = list(connection.execute(text(
                "SELECT sequencename, last_value FROM pg_catalog.pg_sequences WHERE schemaname=:schema ORDER BY sequencename"
            ), {"schema": schema}).tuples())
            rows["constraints"] = list(connection.execute(text(
                "SELECT c.relname, k.conname, pg_get_constraintdef(k.oid) FROM pg_constraint k "
                "JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=:schema ORDER BY c.relname, k.conname"
            ), {"schema": schema}).tuples())
            rows["triggers"] = list(connection.execute(text(
                "SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) FROM pg_trigger t "
                "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=:schema AND NOT t.tgisinternal ORDER BY c.relname, t.tgname"
            ), {"schema": schema}).tuples())
            rows["functions"] = list(connection.execute(text(
                "SELECT p.proname, pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname=:schema AND p.prokind='f' ORDER BY p.proname"
            ), {"schema": schema}).tuples())
            rows["indexes"] = list(connection.execute(text(
                "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname=:schema ORDER BY tablename, indexname"
            ), {"schema": schema}).tuples())
        return rows

    def files():
        return {path.relative_to(artifact_dir).as_posix(): (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
                for path in artifact_dir.rglob("*") if path.is_file()}

    before_database, before_files = database_snapshot(), files()
    backup_dir.mkdir()
    dump_path, objects_path = backup_dir / "postgres.dump", backup_dir / "artifacts.tar.gz"
    with dump_path.open("wb") as output:
        dumped = subprocess.run(commands["dump"], stdout=output, stderr=subprocess.PIPE, timeout=60)
    assert dumped.returncode == 0, dumped.stderr.decode(errors="replace")
    assert dump_path.stat().st_size > 0
    with tarfile.open(objects_path, "w:gz") as archive:
        archive.add(artifact_dir, arcname=artifact_dir.name)
    engine.dispose()  # Do not reuse cached plans referring to the deleted objects.
    with engine.begin() as connection:
        connection.execute(DropSchema(schema, cascade=True))
    shutil.rmtree(artifact_dir)
    assert not inspect(engine).has_schema(schema) and not artifact_dir.exists()
    engine.dispose()
    with dump_path.open("rb") as source:
        restored = subprocess.run(commands["restore"], stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert restored.returncode == 0, restored.stderr.decode(errors="replace")
    with tarfile.open(objects_path, "r:gz") as archive:
        archive.extractall(artifact_dir.parent, filter="data")
    engine.dispose()
    assert database_snapshot() == before_database
    assert files() == before_files
    print(f"RESTORE VERIFIED: {len(before_files)} files; all structured rows, schema version and sequences preserved")
    print("Restored rows: " + ", ".join(f"{table.name}={len(before_database[table.name])}" for table in metadata.tables.values()))
    print("Restored definitions: " + ", ".join(f"{name}={len(before_database[name])}" for name in ("constraints", "triggers", "functions", "indexes")))
    print(f"Database backup: {dump_path.stat().st_size} bytes; SHA256 {hashlib.sha256(dump_path.read_bytes()).hexdigest()}")
    print(f"Artifact backup: {objects_path.stat().st_size} bytes; SHA256 {hashlib.sha256(objects_path.read_bytes()).hexdigest()}")
