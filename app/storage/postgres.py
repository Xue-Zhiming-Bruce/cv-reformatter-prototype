"""PostgreSQL artifact metadata repository; schema changes belong to Alembic."""

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Index, Integer, MetaData, Table, Text, create_engine, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from app.storage.artifact_store import ArtifactStore, ArtifactStoreError


metadata = MetaData()
artifact_sessions = Table(
    "artifact_sessions", metadata,
    Column("artifact_id", Text, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("status", Text, nullable=False),
    Column("artifact_dir", Text, nullable=False),
    *(Column(name, Text) for name in (
        "source_filename", "source_file_type", "original_pdf_preview_url", "original_preview_error",
        "target_format_role", "target_format_filename", "target_format_download_url", "template_name",
        "html_surface_url", "pdf_download_url", "pdf_preview_url",
    )),
    Column("blind_profile", Boolean),
    Column("needs_review_count", Integer),
    Column("missing_field_labels", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("debug_artifacts", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    CheckConstraint("needs_review_count IS NULL OR needs_review_count >= 0", name="ck_artifact_review_count"),
    CheckConstraint("jsonb_typeof(missing_field_labels) = 'array'", name="ck_artifact_missing_labels"),
    CheckConstraint("jsonb_typeof(debug_artifacts) = 'object'", name="ck_artifact_debug_object"),
)
Index("ix_artifact_sessions_updated", artifact_sessions.c.updated_at, artifact_sessions.c.artifact_id)


def postgres_url(value: str) -> URL:
    try:
        url = make_url(value)
        if url.drivername not in {"postgresql", "postgresql+psycopg"} or not url.database:
            raise ValueError
        return url.set(drivername="postgresql+psycopg")
    except (ValueError, SQLAlchemyError) as exc:
        raise ArtifactStoreError("DATABASE_URL must select a PostgreSQL database using Psycopg 3.") from exc


@lru_cache(maxsize=4)
def postgres_engine(database_url: str) -> Engine:
    return create_engine(postgres_url(database_url), pool_pre_ping=True,
        connect_args={"connect_timeout": 5}, pool_timeout=5, hide_parameters=True)


class PostgresArtifactStore(ArtifactStore):
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _upsert_artifact(
        self, *, artifact_id: str, artifact_dir: str | Path, status: str,
        updates: Mapping[str, object], debug_artifacts: Mapping[str, str],
    ) -> None:
        statement = insert(artifact_sessions).values(
            artifact_id=artifact_id, artifact_dir=str(artifact_dir), status=status,
            debug_artifacts=dict(debug_artifacts), **updates,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[artifact_sessions.c.artifact_id],
            set_={
                "artifact_dir": statement.excluded.artifact_dir,
                "status": statement.excluded.status,
                "updated_at": func.now(),
                "debug_artifacts": artifact_sessions.c.debug_artifacts.op("||")(statement.excluded.debug_artifacts),
                **{key: statement.excluded[key] for key in updates},
            },
        )
        try:
            with self.engine.begin() as connection:
                connection.execute(statement)
        except SQLAlchemyError as exc:
            raise ArtifactStoreError("Artifact metadata could not be saved to PostgreSQL; no local fallback was used.") from exc

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        try:
            with self.engine.connect() as connection:
                row = connection.execute(select(artifact_sessions).where(artifact_sessions.c.artifact_id == artifact_id)).mappings().first()
        except SQLAlchemyError as exc:
            raise ArtifactStoreError("Artifact metadata could not be read from PostgreSQL; verify availability and migrations.") from exc
        return _record(row) if row is not None else None

    def list_artifacts(self, *, limit: int = 50) -> list[dict[str, Any]]:
        try:
            with self.engine.connect() as connection:
                rows = connection.execute(select(artifact_sessions).order_by(
                    artifact_sessions.c.updated_at.desc(), artifact_sessions.c.artifact_id,
                ).limit(max(1, min(limit, 200)))).mappings().all()
        except SQLAlchemyError as exc:
            raise ArtifactStoreError("Artifact metadata could not be listed from PostgreSQL; verify availability and migrations.") from exc
        return [_record(row) for row in rows]


def _record(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("created_at", "updated_at"):
        result[key] = result[key].isoformat()
    return result
