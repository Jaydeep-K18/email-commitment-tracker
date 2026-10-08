"""Alembic environment: how migrations connect, and what they compare against."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src import config as app_config
from src.storage.models import Base

alembic_config = context.config

# Logging comes from alembic.ini only when Alembic runs from its own command
# line. Inside the worker (upgrade_schema) the app has configured logging
# already, and the ini's WARNING root level would silence every later message.
if alembic_config.config_file_name is not None and alembic_config.attributes.get(
    "configure_logger", True
):
    fileConfig(alembic_config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

#: Postgres-only objects created by hand in migrations, which the ORM models do
#: not (and on SQLite cannot) describe. Without this, autogenerate would keep
#: proposing to drop them.
POSTGRES_ONLY = {
    "search_vector",
    "ix_raw_emails_search_vector",
    "ix_events_unpublished",
    "ix_jobs_runnable",
}


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    return name not in POSTGRES_ONLY


def database_url() -> str:
    """The URL set by the caller, else the app's own DATABASE_URL."""
    explicit = alembic_config.get_main_option("sqlalchemy.url")
    return explicit or app_config.DATABASE_URL


def run_migrations_offline() -> None:
    """Emit SQL without connecting — used to dump the schema for Node's tests."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = alembic_config.get_section(alembic_config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            # SQLite cannot ALTER most things in place; batch mode rebuilds the
            # table instead. Only the tests ever migrate SQLite.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
