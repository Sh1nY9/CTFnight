from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

from alembic import command
from alpha.db import Database
from alpha.models import Base, Challenge, Event, RegistrationCode, SessionToken, User, utcnow

POSTGRES_URL = os.getenv("ALPHA_TEST_POSTGRES_URL")


def test_sqlite_upgrade_head_installs_submission_limit_constraint(tmp_path, monkeypatch):
    database_path = tmp_path / "migration.db"
    migration_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("ALPHA_ENVIRONMENT", "test")
    monkeypatch.setenv("ALPHA_DATABASE_URL", migration_url)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))

    command.upgrade(config, "head")
    database = Database(migration_url)
    try:
        with database.engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert connection.execute(text("PRAGMA foreign_key_check")).first() is None
            trigger_names = set(
                connection.scalars(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'trigger' AND "
                        "(name LIKE 'ck_challenges_max_attempts_upper_%' OR "
                        "name LIKE 'ck_events_registration_access_mode_%')"
                    )
                )
            )
        assert trigger_names == {
            "ck_challenges_max_attempts_upper_insert",
            "ck_challenges_max_attempts_upper_update",
            "ck_events_registration_access_mode_insert",
            "ck_events_registration_access_mode_update",
        }
        with database.session_factory() as db:
            event = Event(name="Migration", slug="migration", state="draft", team_mode="team")
            db.add(event)
            db.flush()
            db.add(
                Challenge(
                    event_id=event.id,
                    slug="too-many-attempts",
                    title="Too many attempts",
                    category="Misc",
                    connection_info=None,
                    scoring_type="fixed",
                    initial_points=100,
                    minimum_points=100,
                    decay=20,
                    visible=False,
                    max_attempts=1001,
                    flag_type="exact",
                    flag_hash="a" * 64,
                )
            )
            with pytest.raises(IntegrityError, match="ck_challenges_max_attempts_upper"):
                db.commit()
            db.rollback()
    finally:
        database.dispose()


def test_sqlite_registration_access_migration_constraints_and_foreign_keys(tmp_path, monkeypatch):
    database_path = tmp_path / "registration-migration.db"
    migration_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("ALPHA_ENVIRONMENT", "test")
    monkeypatch.setenv("ALPHA_DATABASE_URL", migration_url)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")

    database = Database(migration_url)
    try:
        inspector = inspect(database.engine)
        assert "registration_codes" in inspector.get_table_names()
        event_columns = {item["name"] for item in inspector.get_columns("events")}
        assert "registration_access_mode" in event_columns

        with database.session_factory() as db:
            db.add(
                Event(
                    name="Invalid mode",
                    slug="invalid-mode",
                    state="draft",
                    registration_access_mode="invite",
                )
            )
            with pytest.raises(IntegrityError, match="ck_events_registration_access_mode"):
                db.commit()
            db.rollback()

            admin = User(
                email="migration-admin@example.com",
                username="migration-admin",
                password_hash="hash",
                role="admin",
            )
            event = Event(
                name="Registration migration",
                slug="registration-migration",
                state="registration",
                registration_access_mode="code",
            )
            db.add_all([admin, event])
            db.flush()
            db.add(
                RegistrationCode(
                    event_id=event.id,
                    token_hash="a" * 64,
                    label="Invalid use limit",
                    max_uses=0,
                    created_by=admin.id,
                )
            )
            with pytest.raises(IntegrityError, match="ck_registration_codes_max_uses"):
                db.commit()
            db.rollback()

        with database.session_factory() as db:
            admin = User(
                email="migration-admin@example.com",
                username="migration-admin",
                password_hash="hash",
                role="admin",
            )
            event = Event(
                name="Registration migration",
                slug="registration-migration",
                state="registration",
                registration_access_mode="code",
            )
            db.add_all([admin, event])
            db.flush()
            code = RegistrationCode(
                event_id=event.id,
                token_hash="b" * 64,
                label="Unlimited",
                max_uses=None,
                created_by=admin.id,
            )
            db.add(code)
            db.commit()
            event_id = event.id
            code_id = code.id

        with database.engine.begin() as connection:
            connection.execute(text("DELETE FROM events WHERE id = :id"), {"id": event_id.hex})
        with database.session_factory() as db:
            assert db.get(RegistrationCode, code_id) is None
    finally:
        database.dispose()


def test_sqlite_database_rejects_orphans_and_applies_on_delete_cascade(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'foreign-keys.db'}")
    Base.metadata.create_all(database.engine)
    now = utcnow()
    missing_user_id = uuid.uuid4()
    with database.session_factory() as db:
        db.add(
            SessionToken(
                token_hash="a" * 64,
                user_id=missing_user_id,
                expires_at=now,
                last_seen_at=now,
                created_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        user = User(
            email="cascade@example.com",
            username="cascade",
            password_hash="hash",
            role="participant",
            active=True,
        )
        db.add(user)
        db.flush()
        user_id = user.id
        db.add(
            SessionToken(
                token_hash="b" * 64,
                user_id=user_id,
                expires_at=now,
                last_seen_at=now,
                created_at=now,
            )
        )
        db.commit()

    with database.engine.begin() as connection:
        connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id.hex})
    with database.session_factory() as db:
        assert db.scalar(select(SessionToken).where(SessionToken.user_id == user_id)) is None
    database.dispose()


def test_credential_version_migration_preserves_existing_rows(monkeypatch):
    if not POSTGRES_URL:
        pytest.skip("ALPHA_TEST_POSTGRES_URL is required for PostgreSQL migration tests")

    schema = f"alpha_migration_{uuid.uuid4().hex}"
    administration = Database(POSTGRES_URL)
    with administration.engine.begin() as connection:
        connection.execute(CreateSchema(schema))

    migration_url = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={schema}"})
    migration_url_text = migration_url.render_as_string(hide_password=False)
    monkeypatch.setenv("ALPHA_ENVIRONMENT", "test")
    monkeypatch.setenv("ALPHA_DATABASE_URL", migration_url_text)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))

    engine = create_engine(migration_url_text, hide_parameters=True)
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    event_id = uuid.uuid4()
    try:
        command.upgrade(config, "20260824_0001")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO users
                        (id, email, username, password_hash, role, active,
                         password_change_required, created_at)
                    VALUES
                        (:id, 'legacy@example.com', 'legacy', 'hash', 'participant', true,
                         false, CURRENT_TIMESTAMP)
                    """
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO sessions
                        (id, token_hash, user_id, expires_at, last_seen_at, created_at)
                    VALUES
                        (:id, :token_hash, :user_id, CURRENT_TIMESTAMP + INTERVAL '1 hour',
                         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                ),
                {"id": session_id, "token_hash": "a" * 64, "user_id": user_id},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO events
                        (id, name, slug, description_md, state, team_mode, created_at, updated_at)
                    VALUES
                        (:id, 'Legacy event', 'legacy-event', '', 'draft', 'team',
                         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                ),
                {"id": event_id},
            )

        command.upgrade(config, "head")
        columns = {
            table: {item["name"] for item in inspect(engine).get_columns(table, schema=schema)}
            for table in ("users", "sessions")
        }
        assert "credential_version" in columns["users"]
        assert "credential_version" in columns["sessions"]
        check_names = {
            item["name"] for item in inspect(engine).get_check_constraints("challenges", schema=schema)
        }
        assert "ck_challenges_max_attempts_upper" in check_names
        event_check_names = {
            item["name"] for item in inspect(engine).get_check_constraints("events", schema=schema)
        }
        assert "ck_events_registration_access_mode" in event_check_names
        assert "registration_codes" in inspect(engine).get_table_names(schema=schema)
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT credential_version FROM users WHERE id = :id"), {"id": user_id}
                )
                == 0
            )
            assert (
                connection.scalar(
                    text("SELECT credential_version FROM sessions WHERE id = :id"), {"id": session_id}
                )
                == 0
            )
            assert (
                connection.scalar(
                    text("SELECT registration_access_mode FROM events WHERE id = :id"),
                    {"id": event_id},
                )
                == "open"
            )
    finally:
        engine.dispose()
        with administration.engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        administration.dispose()
