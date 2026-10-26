"""Select the explicitly configured metadata backend without runtime fallback."""

from pathlib import Path

from app.storage.artifact_store import ArtifactStore
from app.storage.local_db import LocalArtifactStore
from app.profile_approval import ProfileRevisionRepository
from app.final_acceptance import FinalAcceptanceRepository
from app.generation.artifact_manifest import GenerationRepository
from app.template_analysis.layout_proof_service import LayoutProofRepository
from app.template_analysis.design_service import DesignRepository


def artifact_store(*, database_url: str | None, local_database_path: Path) -> ArtifactStore:
    if database_url is None:
        return LocalArtifactStore(local_database_path)
    from app.storage.postgres import PostgresArtifactStore, postgres_engine

    return PostgresArtifactStore(postgres_engine(database_url))


def profile_revision_repository(database_url: str | None) -> ProfileRevisionRepository | None:
    if database_url is None:
        return None
    from app.storage.postgres import PostgresProfileRevisionRepository, postgres_engine

    return PostgresProfileRevisionRepository(postgres_engine(database_url))


def generation_repository(database_url: str | None) -> GenerationRepository | None:
    if database_url is None:
        return None
    from app.storage.postgres import PostgresGenerationRepository, postgres_engine

    return PostgresGenerationRepository(postgres_engine(database_url))


def final_acceptance_repository(database_url: str | None) -> FinalAcceptanceRepository | None:
    if database_url is None:
        return None
    from app.storage.postgres import PostgresFinalAcceptanceRepository, postgres_engine

    return PostgresFinalAcceptanceRepository(postgres_engine(database_url))


def layout_proof_repository(database_url: str | None) -> LayoutProofRepository | None:
    if database_url is None:
        return None
    from app.storage.postgres import PostgresLayoutProofRepository, postgres_engine

    return PostgresLayoutProofRepository(postgres_engine(database_url))


def design_repository(database_url: str | None) -> DesignRepository | None:
    if database_url is None:
        return None
    from app.storage.postgres import PostgresDesignRepository, postgres_engine

    return PostgresDesignRepository(postgres_engine(database_url))
