"""PostgreSQL artifact metadata repository; schema changes belong to Alembic."""

from collections.abc import Mapping
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any
import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, Identity, Index, Integer, MetaData, Select, Table, Text, create_engine, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from app.storage.artifact_store import ArtifactStore, ArtifactStoreConflict, ArtifactStoreError
from app.profile_approval import ApprovedProfileVersion, ProfileApprovalError, profile_sha256
from app.final_acceptance import FinalOutputAcceptance, FinalOutputIdentity
from app.generation.artifact_manifest import ArtifactManifest
from app.generation.render_plan import layout_spec_sha256
from app.template_analysis.schemas import LayoutTemplateSpec


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
profile_revisions = Table(
    "candidate_profile_revisions", metadata,
    Column("artifact_id", Text, ForeignKey("artifact_sessions.artifact_id", ondelete="CASCADE"), primary_key=True),
    Column("profile_revision_id", Text, primary_key=True),
    Column("profile_sha256", Text, nullable=False),
    Column("source_draft_sha256", Text, nullable=False),
    Column("body", JSONB, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()),
    CheckConstraint("profile_sha256 ~ '^[0-9a-f]{64}$'", name="ck_revision_profile_sha256"),
    CheckConstraint("source_draft_sha256 ~ '^[0-9a-f]{64}$'", name="ck_revision_source_sha256"),
    CheckConstraint("jsonb_typeof(body) = 'object'", name="ck_revision_body_object"),
)
artifact_sessions.append_column(Column("current_profile_revision_id", Text))
artifact_sessions.append_constraint(ForeignKeyConstraint(
    ["artifact_id", "current_profile_revision_id"],
    ["candidate_profile_revisions.artifact_id", "candidate_profile_revisions.profile_revision_id"],
    name="fk_artifact_current_revision", use_alter=True, deferrable=True, initially="DEFERRED",
))
generation_versions = Table(
    "generation_versions", metadata,
    Column("artifact_id", Text, primary_key=True),
    Column("generation_id", Text, primary_key=True),
    Column("profile_revision_id", Text, nullable=False),
    Column("manifest_sha256", Text, nullable=False),
    Column("manifest", JSONB, nullable=False),
    Column("layout_spec", JSONB, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()),
    ForeignKeyConstraint(["artifact_id", "profile_revision_id"],
        ["candidate_profile_revisions.artifact_id", "candidate_profile_revisions.profile_revision_id"], ondelete="CASCADE"),
    CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_generation_manifest_sha256"),
    CheckConstraint("jsonb_typeof(manifest) = 'object' AND jsonb_typeof(layout_spec) = 'object'", name="ck_generation_objects"),
)
artifact_sessions.append_column(Column("current_generation_id", Text))
artifact_sessions.append_constraint(ForeignKeyConstraint(
    ["artifact_id", "current_generation_id"], ["generation_versions.artifact_id", "generation_versions.generation_id"],
    name="fk_artifact_current_generation", use_alter=True, deferrable=True, initially="DEFERRED",
))
final_output_acceptances = Table(
    "final_output_acceptances", metadata,
    Column("acceptance_id", BigInteger, Identity(), primary_key=True),
    Column("artifact_id", Text, nullable=False),
    Column("generation_id", Text, nullable=False),
    Column("body", JSONB, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()),
    ForeignKeyConstraint(["artifact_id", "generation_id"],
        ["generation_versions.artifact_id", "generation_versions.generation_id"], ondelete="CASCADE"),
    CheckConstraint("jsonb_typeof(body) = 'object'", name="ck_acceptance_body_object"),
)
Index("ix_final_acceptance_artifact", final_output_acceptances.c.artifact_id, final_output_acceptances.c.acceptance_id)


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


class PostgresProfileRevisionRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(self, artifact_dir: Path, version: ApprovedProfileVersion) -> None:
        if version.artifact_id != artifact_dir.name:
            raise ProfileApprovalError("Profile revision artifact scope mismatch.")
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(artifact_sessions).values(
                    artifact_id=version.artifact_id, artifact_dir=str(artifact_dir), status="profile_revision_saved",
                ).on_conflict_do_nothing(index_elements=[artifact_sessions.c.artifact_id]))
                connection.execute(select(artifact_sessions.c.artifact_id).where(
                    artifact_sessions.c.artifact_id == version.artifact_id,
                ).with_for_update()).one()
                connection.execute(insert(profile_revisions).values(
                    artifact_id=version.artifact_id, profile_revision_id=version.profile_revision_id,
                    profile_sha256=version.profile_sha256, source_draft_sha256=version.source_draft_sha256,
                    body=version.model_dump(mode="json"),
                ))
                connection.execute(update(artifact_sessions).where(
                    artifact_sessions.c.artifact_id == version.artifact_id,
                ).values(current_profile_revision_id=version.profile_revision_id, updated_at=func.clock_timestamp()))
        except SQLAlchemyError as exc:
            raise ArtifactStoreError("Profile revision could not be saved to PostgreSQL; no filesystem fallback was used.") from exc

    def load(self, artifact_id: str, revision_id: str) -> ApprovedProfileVersion | None:
        return self._load(select(profile_revisions).where(
            profile_revisions.c.artifact_id == artifact_id, profile_revisions.c.profile_revision_id == revision_id,
        ))

    def current(self, artifact_id: str) -> ApprovedProfileVersion | None:
        return self._load(select(profile_revisions).join(artifact_sessions,
            (profile_revisions.c.artifact_id == artifact_sessions.c.artifact_id)
            & (profile_revisions.c.profile_revision_id == artifact_sessions.c.current_profile_revision_id),
        ).where(artifact_sessions.c.artifact_id == artifact_id))

    def _load(self, statement: Select) -> ApprovedProfileVersion | None:
        try:
            with self.engine.connect() as connection:
                row = connection.execute(statement).mappings().first()
        except SQLAlchemyError as exc:
            raise ArtifactStoreError("Profile revision could not be read from PostgreSQL; verify availability and migrations.") from exc
        if row is None:
            return None
        try:
            version = ApprovedProfileVersion.model_validate(row["body"])
            if (version.artifact_id != row["artifact_id"] or version.profile_revision_id != row["profile_revision_id"]
                    or version.profile_sha256 != row["profile_sha256"]
                    or version.source_draft_sha256 != row["source_draft_sha256"]
                    or profile_sha256(version.profile) != version.profile_sha256):
                raise ValueError("Stored revision identity mismatch")
            return version
        except ValueError as exc:
            raise ProfileApprovalError("Stored profile revision failed schema or identity validation.") from exc


def _json_sha256(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _generation_manifest(row: Mapping[str, Any]) -> ArtifactManifest:
    try:
        manifest = ArtifactManifest.model_validate_json(json.dumps(row["manifest"]))
        layout = LayoutTemplateSpec.model_validate_json(json.dumps(row["layout_spec"]))
        if (manifest.artifact_id != row["artifact_id"]
                or manifest.output_identity.profile_revision_id != row["profile_revision_id"]
                or _json_sha256(row["manifest"]) != row["manifest_sha256"]
                # Revalidation normalizes legacy separator whitespace; hash the saved JSON, not a rewritten model.
                or _json_sha256(row["layout_spec"]) != manifest.output_identity.layout_spec_sha256
                or layout.template_version != manifest.output_identity.layout_template_version):
            raise ValueError("Stored generation identity mismatch")
        return manifest
    except ValueError as exc:
        raise ArtifactStoreError("Stored generation failed schema or identity validation.") from exc


class PostgresGenerationRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(self, manifest: ArtifactManifest, layout_spec: LayoutTemplateSpec) -> None:
        if (layout_spec_sha256(layout_spec) != manifest.output_identity.layout_spec_sha256
                or layout_spec.template_version != manifest.output_identity.layout_template_version):
            raise ArtifactStoreError("Generation template does not match its output identity.")
        generation_id = "generation_" + uuid.uuid4().hex
        body = manifest.model_dump(mode="json")
        try:
            with self.engine.begin() as connection:
                artifact = connection.execute(select(artifact_sessions).where(
                    artifact_sessions.c.artifact_id == manifest.artifact_id,
                ).with_for_update()).mappings().first()
                revision = connection.execute(select(profile_revisions).where(
                    profile_revisions.c.artifact_id == manifest.artifact_id,
                    profile_revisions.c.profile_revision_id == manifest.output_identity.profile_revision_id,
                )).mappings().first()
                if (artifact is None or revision is None
                        or artifact["current_profile_revision_id"] != manifest.output_identity.profile_revision_id
                        or revision["profile_sha256"] != manifest.output_identity.profile_sha256
                        or revision["source_draft_sha256"] != manifest.inputs["source_draft"].sha256):
                    raise ArtifactStoreConflict("The saved profile changed before generation was recorded; generate the current revision.")
                connection.execute(insert(generation_versions).values(
                    artifact_id=manifest.artifact_id, generation_id=generation_id,
                    profile_revision_id=manifest.output_identity.profile_revision_id,
                    manifest_sha256=_json_sha256(body), manifest=body, layout_spec=layout_spec.model_dump(mode="json"),
                ))
                connection.execute(update(artifact_sessions).where(
                    artifact_sessions.c.artifact_id == manifest.artifact_id,
                ).values(current_generation_id=generation_id, updated_at=func.clock_timestamp()))
        except SQLAlchemyError as exc:
            raise ArtifactStoreError("Generation could not be saved to PostgreSQL; no filesystem fallback was used.") from exc

    def current(self, artifact_id: str) -> ArtifactManifest | None:
        try:
            with self.engine.connect() as connection:
                row = connection.execute(select(generation_versions).join(artifact_sessions,
                    (generation_versions.c.artifact_id == artifact_sessions.c.artifact_id)
                    & (generation_versions.c.generation_id == artifact_sessions.c.current_generation_id),
                ).where(artifact_sessions.c.artifact_id == artifact_id)).mappings().first()
        except SQLAlchemyError as exc:
            raise ArtifactStoreError("Generation could not be read from PostgreSQL; verify availability and migrations.") from exc
        return _generation_manifest(row) if row is not None else None


class PostgresFinalAcceptanceRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(self, artifact_id: str, acceptance: FinalOutputAcceptance) -> None:
        try:
            with self.engine.begin() as connection:
                artifact = connection.execute(select(artifact_sessions).where(
                    artifact_sessions.c.artifact_id == artifact_id,
                ).with_for_update()).mappings().first()
                generation = connection.execute(select(generation_versions).where(
                    generation_versions.c.artifact_id == artifact_id,
                    generation_versions.c.generation_id == (artifact["current_generation_id"] if artifact else None),
                )).mappings().first()
                if (artifact is None or generation is None
                        or artifact["current_profile_revision_id"] != acceptance.profile_revision_id
                        or _generation_manifest(generation).output_identity != FinalOutputIdentity.model_validate(
                            acceptance.model_dump(exclude={"schema_version", "accepted_at", "reviewer_note"}))):
                    raise ArtifactStoreConflict("The saved profile or generation changed; review and accept the current output.")
                connection.execute(insert(final_output_acceptances).values(
                    artifact_id=artifact_id, generation_id=generation["generation_id"], body=acceptance.model_dump(mode="json"),
                ))
        except SQLAlchemyError as exc:
            raise ArtifactStoreError("Final acceptance could not be saved to PostgreSQL; no filesystem fallback was used.") from exc

    def load(self, artifact_id: str) -> FinalOutputAcceptance | None:
        try:
            with self.engine.connect() as connection:
                row = connection.execute(select(final_output_acceptances.c.body, generation_versions)
                    .join(generation_versions,
                        (final_output_acceptances.c.artifact_id == generation_versions.c.artifact_id)
                        & (final_output_acceptances.c.generation_id == generation_versions.c.generation_id))
                    .where(final_output_acceptances.c.artifact_id == artifact_id)
                    .order_by(final_output_acceptances.c.acceptance_id.desc()).limit(1)).mappings().first()
        except SQLAlchemyError as exc:
            raise ArtifactStoreError("Final acceptance could not be read from PostgreSQL; verify availability and migrations.") from exc
        if row is None:
            return None
        try:
            acceptance = FinalOutputAcceptance.model_validate_json(json.dumps(row["body"]))
            identity = FinalOutputIdentity.model_validate(
                acceptance.model_dump(exclude={"schema_version", "accepted_at", "reviewer_note"}))
            if _generation_manifest(row).output_identity != identity:
                raise ValueError("Stored acceptance identity mismatch")
            current = PostgresGenerationRepository(self.engine).current(artifact_id)
            if current is None or current.output_identity != identity:
                return None
            return acceptance
        except ValueError as exc:
            raise ArtifactStoreError("Stored final acceptance failed schema or identity validation.") from exc
