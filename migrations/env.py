"""Apply versioned PostgreSQL schema changes through the configured connection."""

import os

from alembic import context

from app.storage.postgres import metadata, postgres_engine, postgres_url


def migrate(connection) -> None:
    context.configure(connection=connection, target_metadata=metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if context.is_offline_mode():
    context.configure(url=postgres_url(os.environ["DATABASE_URL"]), target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif connection is not None:
    migrate(connection)
else:
    with postgres_engine(os.environ["DATABASE_URL"]).connect() as connection:
        migrate(connection)
