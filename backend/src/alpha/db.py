from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable and verify SQLite FK enforcement on every DB-API connection."""

    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA foreign_keys")
            enabled = cursor.fetchone()
            if enabled != (1,):
                raise RuntimeError("SQLite foreign-key enforcement could not be enabled")
        finally:
            cursor.close()


class Database:
    def __init__(self, url: str) -> None:
        # SQLAlchemy exceptions can otherwise include bound values such as
        # regex flags, emails, or audit metadata in application logs.
        kwargs: dict[str, object] = {"pool_pre_ping": True, "hide_parameters": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url in {"sqlite://", "sqlite:///:memory:"}:
                kwargs["poolclass"] = StaticPool
        elif url.startswith(("postgresql://", "postgresql+psycopg://")):
            # Bound both connection-pool waits and database lock/query waits so
            # an abandoned or contended transaction cannot pin API workers
            # indefinitely. PostgreSQL applies these settings to every session.
            kwargs["pool_timeout"] = 5
            kwargs["connect_args"] = {
                "options": (
                    "-c lock_timeout=3000 "
                    "-c statement_timeout=30000 "
                    "-c idle_in_transaction_session_timeout=30000"
                )
            }
        self.engine: Engine = create_engine(url, **kwargs)
        enable_sqlite_foreign_keys(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    def session(self) -> Generator[Session, None, None]:
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()

    def dispose(self) -> None:
        self.engine.dispose()
