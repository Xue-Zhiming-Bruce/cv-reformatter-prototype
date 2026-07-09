import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.extraction.candidate_schema import MissingField


DEFAULT_DATABASE_PATH = Path("data/local/cv_reformatter.sqlite3")


class LocalDatabaseError(RuntimeError):
    """Raised when local SQLite artifact metadata cannot be read or written."""


class LocalArtifactStore:
    """SQLite-backed metadata index for local demo artifact sessions."""

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path)

    def initialize_schema(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS artifact_jobs (
                        artifact_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        artifact_dir TEXT NOT NULL,
                        source_filename TEXT,
                        source_file_type TEXT,
                        original_pdf_preview_url TEXT,
                        original_preview_error TEXT,
                        target_format_role TEXT,
                        target_format_filename TEXT,
                        target_format_download_url TEXT,
                        template_name TEXT,
                        blind_profile INTEGER,
                        docx_download_url TEXT,
                        pdf_download_url TEXT,
                        pdf_preview_url TEXT,
                        needs_review_count INTEGER,
                        missing_field_labels_json TEXT NOT NULL DEFAULT '[]',
                        debug_artifacts_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise LocalDatabaseError(f"Local database schema could not be initialized: {exc}") from exc

    def record_processed_resume(
        self,
        *,
        artifact_id: str,
        artifact_dir: str | Path,
        source_filename: str,
        source_file_type: str,
        original_pdf_preview_url: str | None,
        original_preview_error: str | None,
        missing_fields: list[MissingField],
        debug_artifacts: Mapping[str, str],
    ) -> None:
        self._upsert_artifact(
            artifact_id=artifact_id,
            artifact_dir=artifact_dir,
            status="processed",
            updates={
                "source_filename": source_filename,
                "source_file_type": source_file_type,
                "original_pdf_preview_url": original_pdf_preview_url,
                "original_preview_error": original_preview_error,
                "needs_review_count": len(missing_fields),
                "missing_field_labels_json": _missing_field_labels_json(missing_fields),
            },
            debug_artifacts=debug_artifacts,
        )

    def record_target_format(
        self,
        *,
        artifact_id: str,
        artifact_dir: str | Path,
        target_format_role: str,
        target_format_filename: str,
        target_format_download_url: str,
        debug_artifacts: Mapping[str, str],
    ) -> None:
        self._upsert_artifact(
            artifact_id=artifact_id,
            artifact_dir=artifact_dir,
            status="target_format_uploaded",
            updates={
                "target_format_role": target_format_role,
                "target_format_filename": target_format_filename,
                "target_format_download_url": target_format_download_url,
            },
            debug_artifacts=debug_artifacts,
        )

    def record_generated_outputs(
        self,
        *,
        artifact_id: str,
        artifact_dir: str | Path,
        template_name: str,
        blind_profile: bool,
        docx_download_url: str,
        pdf_download_url: str,
        pdf_preview_url: str,
        missing_fields: list[MissingField],
        debug_artifacts: Mapping[str, str],
    ) -> None:
        self._upsert_artifact(
            artifact_id=artifact_id,
            artifact_dir=artifact_dir,
            status="generated",
            updates={
                "template_name": template_name,
                "blind_profile": int(blind_profile),
                "docx_download_url": docx_download_url,
                "pdf_download_url": pdf_download_url,
                "pdf_preview_url": pdf_preview_url,
                "needs_review_count": len(missing_fields),
                "missing_field_labels_json": _missing_field_labels_json(missing_fields),
            },
            debug_artifacts=debug_artifacts,
        )

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        self.initialize_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM artifact_jobs WHERE artifact_id = ?",
                    (artifact_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise LocalDatabaseError(f"Local artifact metadata could not be read: {exc}") from exc
        return _row_to_dict(row) if row else None

    def list_artifacts(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self.initialize_schema()
        bounded_limit = max(1, min(limit, 200))
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM artifact_jobs
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (bounded_limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise LocalDatabaseError(f"Local artifact metadata could not be listed: {exc}") from exc
        return [_row_to_dict(row) for row in rows]

    def _upsert_artifact(
        self,
        *,
        artifact_id: str,
        artifact_dir: str | Path,
        status: str,
        updates: Mapping[str, object],
        debug_artifacts: Mapping[str, str],
    ) -> None:
        self.initialize_schema()
        now = _utc_now()
        merged_debug_artifacts = self._merged_debug_artifacts(artifact_id, debug_artifacts)

        columns: dict[str, object] = {
            "artifact_id": artifact_id,
            "created_at": now,
            "updated_at": now,
            "status": status,
            "artifact_dir": str(artifact_dir),
            "debug_artifacts_json": json.dumps(merged_debug_artifacts, sort_keys=True),
            **updates,
        }
        update_columns = [column for column in columns if column not in {"artifact_id", "created_at"}]
        placeholders = ", ".join("?" for _ in columns)
        column_names = ", ".join(columns)
        update_assignments = ", ".join(f"{column} = excluded.{column}" for column in update_columns)

        try:
            with self._connect() as connection:
                connection.execute(
                    f"""
                    INSERT INTO artifact_jobs ({column_names})
                    VALUES ({placeholders})
                    ON CONFLICT(artifact_id) DO UPDATE SET
                    {update_assignments}
                    """,
                    tuple(columns.values()),
                )
        except sqlite3.Error as exc:
            raise LocalDatabaseError(f"Local artifact metadata could not be written: {exc}") from exc

    def _merged_debug_artifacts(
        self,
        artifact_id: str,
        debug_artifacts: Mapping[str, str],
    ) -> dict[str, str]:
        current = self.get_artifact(artifact_id)
        merged = dict(current["debug_artifacts"]) if current else {}
        merged.update(debug_artifacts)
        return merged

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["blind_profile"] = None if payload["blind_profile"] is None else bool(payload["blind_profile"])
    payload["missing_field_labels"] = json.loads(payload.pop("missing_field_labels_json") or "[]")
    payload["debug_artifacts"] = json.loads(payload.pop("debug_artifacts_json") or "{}")
    return payload


def _missing_field_labels_json(missing_fields: list[MissingField]) -> str:
    labels = [field.label for field in missing_fields]
    return json.dumps(labels, ensure_ascii=False)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
