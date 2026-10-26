"""Select the explicitly configured metadata backend without runtime fallback."""

from pathlib import Path

from app.storage.artifact_store import ArtifactStore
from app.storage.local_db import LocalArtifactStore
from app.profile_approval import ProfileRevisionRepository


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
