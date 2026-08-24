from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest
from conftest import TEST_SECRET, production_settings_values
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import StatementError

from alpha.cli import ChallengeFile, _bootstrap, _import_challenge, _set_password
from alpha.cli import main as cli_main
from alpha.config import Settings
from alpha.db import Database
from alpha.models import Announcement, Base, Challenge, Event, User
from alpha.security import compare_exact_flag, compare_regex_flag, hash_flag, verify_csrf, verify_password
from alpha.services import dynamic_points


def cli_settings(path: Path, **overrides) -> Settings:
    values = {
        "environment": "test",
        "database_url": f"sqlite:///{path}",
        "redis_url": "memory://",
        "secret_key": "cli-test-secret-that-is-at-least-thirty-two-characters",
        "cookie_secure": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_database_engine_redacts_bound_parameters():
    database = Database("sqlite://")
    assert database.engine.hide_parameters is True
    database.dispose()


def test_postgresql_engine_bounds_pool_lock_and_statement_waits(monkeypatch):
    import alpha.db as db_module

    real_create_engine = db_module.create_engine
    captured: dict = {}

    def capture_create_engine(url: str, **kwargs):
        captured.update(kwargs)
        return real_create_engine("sqlite://")

    monkeypatch.setattr(db_module, "create_engine", capture_create_engine)
    database = Database("postgresql+psycopg://alpha:secret@postgres/alpha")
    try:
        assert captured["pool_timeout"] == 5
        options = captured["connect_args"]["options"]
        assert "lock_timeout=3000" in options
        assert "statement_timeout=30000" in options
        assert "idle_in_transaction_session_timeout=30000" in options
    finally:
        database.dispose()


def test_bootstrap_is_idempotent_and_does_not_reset_existing_admin(tmp_path):
    settings = cli_settings(
        tmp_path / "cli.db",
        admin_email="admin@example.com",
        admin_username="operator",
        admin_password=SecretStr("InitialAdmin!123"),
        seed_demo=True,
    )
    database = Database(settings.database_url)
    Base.metadata.create_all(database.engine)
    _bootstrap(settings, database)
    with database.session_factory() as db:
        event = db.scalar(select(Event))
        admin = db.scalar(select(User))
        original_hash = admin.password_hash
        assert event.name == "CTFnight"
        assert event.slug == "ctfnight"
        assert event.description_md == "CTFnight에 오신 것을 환영합니다."
        assert event.state == "draft"
        assert admin.password_change_required is True
        assert verify_password(admin.password_hash, "InitialAdmin!123")
        assert db.scalar(select(func.count()).select_from(Challenge)) == 2
        assert db.scalar(select(func.count()).select_from(Announcement)) == 1
        exact = db.scalar(select(Challenge).where(Challenge.slug == "welcome"))
        regex = db.scalar(select(Challenge).where(Challenge.slug == "regex-demo"))
        announcement = db.scalar(select(Announcement))
        assert compare_exact_flag(
            settings.secret_key.get_secret_value(),
            exact.flag_hash,
            "FLAG{welcome-to-ctfnight}",
        )
        assert regex.flag_regex == r"^FLAG\{regex-[0-9]{4}\}$"
        assert announcement.title == "CTFnight 준비 완료"
    changed = settings.model_copy(update={"admin_password": SecretStr("MustNotReplace!456")})
    _bootstrap(changed, database)
    with database.session_factory() as db:
        admin = db.scalar(select(User))
        assert admin.password_hash == original_hash
        assert db.scalar(select(func.count()).select_from(Event)) == 1
        assert db.scalar(select(func.count()).select_from(Challenge)) == 2
    post_rotation = settings.model_copy(update={"admin_password": None})
    _bootstrap(post_rotation, database)
    with database.session_factory() as db:
        admin = db.scalar(select(User))
        assert admin.password_hash == original_hash
    database.dispose()


def test_bootstrap_skips_demo_seed_for_archived_event(tmp_path):
    settings = cli_settings(tmp_path / "archived.db", seed_demo=True)
    database = Database(settings.database_url)
    Base.metadata.create_all(database.engine)
    _bootstrap(settings, database)
    with database.session_factory() as db:
        event = db.scalar(select(Event))
        event.state = "archived"
        original_challenges = db.scalar(select(func.count()).select_from(Challenge))
        db.commit()

    _bootstrap(settings, database)
    with database.session_factory() as db:
        assert db.scalar(select(Event)).state == "archived"
        assert db.scalar(select(func.count()).select_from(Challenge)) == original_challenges
    database.dispose()


def test_import_challenge_upserts_and_never_stores_exact_plaintext(tmp_path):
    settings = cli_settings(tmp_path / "import.db")
    database = Database(settings.database_url)
    Base.metadata.create_all(database.engine)
    _bootstrap(settings, database)
    challenge_file = tmp_path / "challenge.yaml"
    challenge_file.write_text(
        """event_slug: ctfnight
slug: imported
title: Imported Challenge
category: Web
description_md: Test importer
scoring:
  type: dynamic
  initial: 500
  minimum: 100
  decay: 20
visible: true
flag:
  type: exact
  value: FLAG{import-secret}
""",
        encoding="utf-8",
    )
    _import_challenge(settings, database, challenge_file)
    _import_challenge(settings, database, challenge_file)
    with database.session_factory() as db:
        challenge = db.scalar(select(Challenge).where(Challenge.slug == "imported"))
        assert challenge is not None
        assert challenge.flag_hash != "FLAG{import-secret}"
        assert challenge.flag_regex is None
        assert db.scalar(select(func.count()).select_from(Challenge).where(Challenge.slug == "imported")) == 1
    database.dispose()


def test_set_password_uses_environment_secret_and_requires_change(tmp_path):
    settings = cli_settings(
        tmp_path / "reset.db",
        admin_email="admin@example.com",
        admin_password=SecretStr("InitialAdmin!123"),
    )
    database = Database(settings.database_url)
    Base.metadata.create_all(database.engine)
    _bootstrap(settings, database)
    reset = settings.model_copy(update={"admin_password": SecretStr("RecoveredAdmin!456")})
    _set_password(reset, database, "admin@example.com")
    with database.session_factory() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        assert verify_password(admin.password_hash, "RecoveredAdmin!456")
        assert admin.password_change_required is True
    database.dispose()


def test_settings_validate_bootstrap_identity_and_password(monkeypatch):
    with pytest.raises(ValidationError):
        cli_settings(Path("unused.db"), admin_email="not-an-email")
    with pytest.raises(ValidationError):
        cli_settings(Path("unused.db"), admin_password="too-short")

    monkeypatch.setenv("ALPHA_ADMIN_PASSWORD", "")
    empty = Settings(_env_file=None)
    assert empty.admin_password is None


def test_existing_admin_restart_accepts_empty_environment_password(tmp_path, monkeypatch):
    database_path = tmp_path / "empty-password.db"
    initial = cli_settings(
        database_path,
        admin_email="admin@example.com",
        admin_password="InitialAdmin!123",
    )
    database = Database(initial.database_url)
    Base.metadata.create_all(database.engine)
    _bootstrap(initial, database)

    monkeypatch.setenv("ALPHA_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("ALPHA_ADMIN_PASSWORD", "")
    restarted = cli_settings(database_path)
    assert restarted.admin_password is None
    _bootstrap(restarted, database)
    with database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.role == "admin")) == 1
    database.dispose()


def test_challenge_file_contract_accepts_examples_and_rejects_unsafe_values():
    import yaml

    template_root = Path(__file__).parents[2] / "templates" / "challenges"
    examples = sorted(template_root.glob("*/challenge.yaml"))
    assert len(examples) == 2
    for path in examples:
        spec = ChallengeFile.model_validate(
            yaml.safe_load(path.read_text(encoding="utf-8")),
            context={"max_flag_length": 512},
        )
        assert spec.event_slug == "ctfnight"

    base = {
        "event_slug": "ctfnight",
        "slug": "valid-slug",
        "title": "Valid",
        "category": "Misc",
        "description_md": "Description",
        "scoring": {"type": "dynamic", "initial": 500},
        "visible_at": "2026-08-24T12:00:00",
        "flag": {"type": "exact", "value": "A" * 17},
        "prerequisites": ["welcome", "welcome"],
    }
    with pytest.raises(ValidationError) as exc_info:
        ChallengeFile.model_validate(base, context={"max_flag_length": 16})
    messages = str(exc_info.value)
    assert "dynamic scoring requires minimum and decay" in messages
    assert "timezone offset is required" in messages
    assert "prerequisites must be unique" in messages


def test_flag_contract_does_not_require_a_brand_prefix():
    base = {
        "event_slug": "ctfnight",
        "slug": "custom-format",
        "title": "Custom Format",
        "category": "Misc",
        "description_md": "Flags are challenge-defined",
        "scoring": {"type": "fixed", "initial": 100},
        "flag": {"type": "exact", "value": "plain-answer-without-braces"},
    }
    exact = ChallengeFile.model_validate(base, context={"max_flag_length": 512})
    secret = "test-secret-for-arbitrary-flag-formats"
    assert compare_exact_flag(secret, hash_flag(secret, exact.flag.value), "plain-answer-without-braces")

    regex_pattern = r"^night-[a-f0-9]{8}$"
    regex = ChallengeFile.model_validate(
        base | {"flag": {"type": "regex", "value": regex_pattern}},
        context={"max_flag_length": 512},
    )
    assert compare_regex_flag(regex.flag.value, "night-deadbeef", 0.05).matched


@pytest.mark.parametrize("flag_type", ["exact", "regex"])
def test_import_rejects_flag_over_runtime_limit(tmp_path, flag_type):
    settings = cli_settings(tmp_path / f"too-long-{flag_type}.db", max_flag_length=16)
    database = Database(settings.database_url)
    Base.metadata.create_all(database.engine)
    _bootstrap(settings, database)
    challenge_file = tmp_path / f"{flag_type}.yaml"
    value = "A" * 17 if flag_type == "exact" else "^" + "A" * 15 + "$"
    challenge_file.write_text(
        f"""event_slug: ctfnight
slug: too-long-{flag_type}
title: Too Long
category: Misc
description_md: Reject oversized flags
scoring:
  type: fixed
  initial: 100
flag:
  type: {flag_type}
  value: '{value}'
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        _import_challenge(settings, database, challenge_file)
    with database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Challenge)) == 0
    database.dispose()


def test_cli_yaml_error_never_echoes_private_flag(tmp_path, monkeypatch, capsys):
    private_flag = "FLAG{TOP_SECRET_FLAG}"
    challenge_file = tmp_path / "malformed.yaml"
    challenge_file.write_text(
        f'''event_slug: ctfnight
slug: malformed
title: Malformed
category: Misc
description_md: Parser error test
scoring:
  type: fixed
  initial: 100
flag:
  type: exact
  value: "{private_flag}
''',
        encoding="utf-8",
    )
    monkeypatch.setenv("ALPHA_DATABASE_URL", f"sqlite:///{tmp_path / 'malformed.db'}")

    assert cli_main(["import-challenge", str(challenge_file)]) == 1
    error = capsys.readouterr().err
    assert "invalid YAML" in error
    assert "malformed.yaml" in error
    assert "line" in error and "column" in error
    assert private_flag not in error
    assert "value:" not in error


def test_cli_imports_private_challenge_from_stdin(tmp_path, monkeypatch):
    database_path = tmp_path / "stdin-import.db"
    settings = cli_settings(database_path)
    database = Database(settings.database_url)
    Base.metadata.create_all(database.engine)
    _bootstrap(settings, database)
    database.dispose()

    private_flag = "FLAG{STDIN_ONLY_SECRET}"
    source = f'''event_slug: ctfnight
slug: stdin-private
title: Stdin Private
category: Misc
description_md: Imported without a bind mount
scoring:
  type: fixed
  initial: 100
visible: false
flag:
  type: exact
  value: "{private_flag}"
'''
    monkeypatch.setenv("ALPHA_ENVIRONMENT", "test")
    monkeypatch.setenv("ALPHA_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ALPHA_REDIS_URL", "memory://")
    monkeypatch.setenv("ALPHA_SECRET_KEY", settings.secret_key.get_secret_value())
    monkeypatch.setattr(sys, "stdin", io.StringIO(source))

    assert cli_main(["import-challenge", "-"]) == 0
    database = Database(settings.database_url)
    with database.session_factory() as db:
        challenge = db.scalar(select(Challenge).where(Challenge.slug == "stdin-private"))
        assert challenge.flag_hash and challenge.flag_hash != private_flag
        assert challenge.flag_regex is None
    database.dispose()


def test_cli_database_error_never_echoes_private_flag_parameters(tmp_path, monkeypatch, capsys):
    private_flag = r"^FLAG\{DATABASE_SECRET\}$"
    challenge_file = tmp_path / "database-error.yaml"
    challenge_file.write_text("placeholder: true\n", encoding="utf-8")
    monkeypatch.setenv("ALPHA_DATABASE_URL", f"sqlite:///{tmp_path / 'database-error.db'}")

    def fail_with_sensitive_parameters(*_args, **_kwargs):
        raise StatementError(
            "insert failed",
            "INSERT INTO challenges (flag_regex) VALUES (:flag_regex)",
            {"flag_regex": private_flag},
            ValueError("driver rejected value"),
        )

    monkeypatch.setattr("alpha.cli._import_challenge", fail_with_sensitive_parameters)
    assert cli_main(["import-challenge", str(challenge_file)]) == 1
    error = capsys.readouterr().err
    assert error.strip() == "alpha-cli: database operation failed"
    assert private_flag not in error
    assert "flag_regex" not in error


def test_security_timeouts_and_dynamic_score_bounds():
    assert compare_regex_flag(r"^FLAG\{[0-9]+\}$", "FLAG{2026}", 0.05).matched
    assert not compare_regex_flag(r"^FLAG\{[0-9]+\}$", "wrong", 0.05).matched
    assert dynamic_points(500, 100, 20, 0) == 500
    assert dynamic_points(500, 100, 20, 1) < 500
    assert dynamic_points(500, 100, 20, 10_000) == 100
    assert not verify_csrf("secret", "forged", 60)


def test_production_configuration_rejects_unsafe_fallbacks(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHA_ALLOWED_ORIGINS", "https://ctf.example,https://admin.example")
    monkeypatch.setenv("ALPHA_TRUSTED_HOSTS", '["ctf.example"]')
    parsed = Settings()
    assert parsed.allowed_origins == ["https://ctf.example", "https://admin.example"]
    assert parsed.trusted_hosts == ["ctf.example"]
    assert Settings(environment=" TEST ").environment == "test"
    with pytest.raises(ValidationError):
        Settings(environment="prod")

    with pytest.raises(ValidationError):
        Settings(environment="production")
    values = production_settings_values(
        tmp_path,
        allowed_origins=["https://ctf.example"],
        trusted_hosts=["ctf.example"],
    )
    valid = Settings(**values)
    assert valid.secure_cookies is True
    assert valid.database_url.startswith("postgresql+psycopg://alpha:")
    assert "@postgres:5432/alpha?sslmode=disable" in valid.database_url
    assert valid.redis_url.startswith("redis://:")
    with pytest.raises(ValidationError):
        Settings(**(values | {"trusted_hosts": ["*"]}))
    with pytest.raises(ValidationError):
        Settings(**(values | {"database_host": "external-db.example"}))
    with pytest.raises(ValidationError):
        Settings(**(values | {"redis_host": "external-cache.example"}))
    tls_values = dict(values)
    tls_values.update(database_host="external-db.example", database_tls=True)
    assert "sslmode=verify-full" in Settings(**tls_values).database_url
    redis_tls_values = dict(values)
    redis_tls_values.update(redis_host="external-cache.example", redis_tls=True)
    assert Settings(**redis_tls_values).redis_url.startswith("rediss://:")
    with pytest.raises(ValidationError):
        Settings(**(values | {"allowed_origins": ["https://ctf.example/path"]}))
    with pytest.raises(ValidationError):
        Settings(**(values | {"allowed_origins": ["http://ctf.example"]}))
    with pytest.raises(ValidationError):
        cli_settings(Path("unused.db"), admin_username="bad operator")


def test_production_reads_compose_secrets_without_plaintext_environment(monkeypatch, tmp_path):
    values = production_settings_values(tmp_path)
    env_values = {
        "ALPHA_ENVIRONMENT": "production",
        "ALPHA_DATABASE_HOST": "postgres",
        "ALPHA_DATABASE_NAME": "alpha",
        "ALPHA_DATABASE_USER": "alpha",
        "ALPHA_DATABASE_PASSWORD_FILE": str(values["database_password_file"]),
        "ALPHA_REDIS_HOST": "redis",
        "ALPHA_REDIS_PASSWORD_FILE": str(values["redis_password_file"]),
        "ALPHA_SECRET_KEY_FILE": str(values["secret_key_file"]),
        "ALPHA_ADMIN_PASSWORD_FILE": str(values["admin_password_file"]),
        "ALPHA_ALLOWED_ORIGINS": '["https://ctf.example"]',
        "ALPHA_TRUSTED_HOSTS": '["ctf.example"]',
    }
    for name, value in env_values.items():
        monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("postgresql+psycopg://alpha:database-test-password@")
    assert settings.redis_url.startswith("redis://:redis-test-password@")
    assert settings.secret_key.get_secret_value() == TEST_SECRET
    assert "database-test-password" not in repr(settings)
    assert "redis-test-password" not in repr(settings)


def test_secret_files_reject_symlinks_and_multiline_values(tmp_path):
    target = tmp_path / "actual-secret"
    target.write_text("safe-value\n", encoding="utf-8")
    link = tmp_path / "linked-secret"
    link.symlink_to(target)
    with pytest.raises(ValidationError):
        Settings(secret_key_file=link)

    multiline = tmp_path / "multiline-secret"
    multiline.write_text("first\nsecond\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        Settings(secret_key_file=multiline)

    fifo = tmp_path / "secret-fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValidationError):
        Settings(secret_key_file=fifo)
