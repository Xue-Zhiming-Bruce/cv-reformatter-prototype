"""Shared migration helper for the canonical PostgreSQL integration checks."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def migrate_database(engine: Engine, revision: str = "head", *, downgrade: bool = False) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)
