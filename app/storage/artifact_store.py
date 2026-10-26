"""Shared artifact metadata operations; drivers own persistence and transactions."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.extraction.candidate_schema import MissingField


class ArtifactStoreError(RuntimeError):
    """Safe storage error suitable for API responses, without credentials or SQL."""


class ArtifactStoreConflict(ArtifactStoreError):
    """A saved revision or output changed before the transaction committed."""


class ArtifactStore(ABC):
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
                "missing_field_labels": [field.label for field in missing_fields],
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
        html_surface_url: str,
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
                "blind_profile": blind_profile,
                "html_surface_url": html_surface_url,
                "pdf_download_url": pdf_download_url,
                "pdf_preview_url": pdf_preview_url,
                "needs_review_count": len(missing_fields),
                "missing_field_labels": [field.label for field in missing_fields],
            },
            debug_artifacts=debug_artifacts,
        )

    def record_design_reference(
        self,
        *,
        artifact_id: str,
        artifact_dir: str | Path,
        design_id: str,
        design_state: str,
        debug_artifacts: Mapping[str, str],
    ) -> None:
        """Record a design artifact reference without adding metadata columns.

        Design lifecycle state lives on the filesystem under the artifact
        store boundary; this index entry is metadata only.
        """
        self._upsert_artifact(
            artifact_id=artifact_id,
            artifact_dir=artifact_dir,
            status="design_created",
            updates={},
            debug_artifacts={
                **debug_artifacts,
                "design_id": design_id,
                "design_state": design_state,
            },
        )


    @abstractmethod
    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def list_artifacts(self, *, limit: int = 50) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def _upsert_artifact(
        self, *, artifact_id: str, artifact_dir: str | Path, status: str,
        updates: Mapping[str, object], debug_artifacts: Mapping[str, str],
    ) -> None:
        raise NotImplementedError
