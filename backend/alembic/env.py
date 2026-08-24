from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from alpha.config import Settings
from alpha.db import enable_sqlite_foreign_keys
from alpha.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
database_url = Settings().database_url
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    enable_sqlite_foreign_keys(connectable)
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            violation = connection.exec_driver_sql("PRAGMA foreign_key_check").first()
            if violation is not None:
                raise RuntimeError(f"SQLite foreign-key violation before migration: {violation!r}")
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        if connection.dialect.name == "sqlite":
            violation = connection.exec_driver_sql("PRAGMA foreign_key_check").first()
            if violation is not None:
                raise RuntimeError(f"SQLite foreign-key violation after migration: {violation!r}")
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
