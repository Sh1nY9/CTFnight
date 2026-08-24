from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from alpha.config import Settings
from alpha.db import Database
from alpha.main import create_app
from alpha.models import Base, Event, User, utcnow
from alpha.security import hash_password
from alpha.store import MemoryStore

TEST_SECRET = "test-secret-that-is-long-enough-for-production-use"


def production_settings_values(tmp_path, **overrides) -> dict:
    secret_paths = {
        "secret_key_file": ("alpha_secret_key", TEST_SECRET),
        "database_password_file": ("postgres_password", "database-test-password"),
        "redis_password_file": ("redis_password", "redis-test-password"),
        "admin_password_file": ("admin_password", ""),
    }
    values: dict = {
        "environment": "production",
        "database_host": "postgres",
        "database_name": "alpha",
        "database_user": "alpha",
        "redis_host": "redis",
        "cookie_secure": True,
        "allowed_origins": ["https://testserver"],
        "trusted_hosts": ["testserver"],
    }
    for field, (filename, value) in secret_paths.items():
        path = tmp_path / filename
        path.write_text(f"{value}\n", encoding="utf-8")
        values[field] = path
    values.update(overrides)
    return values


@dataclass
class TestContext:
    client: TestClient
    database: Database
    settings: Settings


@pytest.fixture
def ctx(tmp_path) -> Iterator[TestContext]:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(database.engine)
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        redis_url="memory://",
        secret_key=TEST_SECRET,
        cookie_secure=False,
        allowed_origins=["http://testserver"],
        trusted_hosts=["testserver"],
        submission_rate_limit=100,
        scoreboard_cache_seconds=30,
    )
    with database.session_factory() as db:
        db.add(
            Event(
                name="CTFnight Test CTF",
                slug="ctfnight-test",
                state="registration",
                registration_at=utcnow(),
                team_mode="team",
            )
        )
        db.commit()
    app = create_app(settings=settings, database=database, store=MemoryStore())
    with TestClient(app) as client:
        yield TestContext(client=client, database=database, settings=settings)


def csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def mutate(client: TestClient, method: str, url: str, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    headers.update(csrf_headers(client))
    return client.request(method, url, headers=headers, **kwargs)


def register(client: TestClient, username: str, email: str | None = None, password: str = "CorrectHorse!123"):
    return mutate(
        client,
        "POST",
        "/api/v1/auth/register",
        json={"email": email or f"{username}@example.com", "username": username, "password": password},
    )


def create_admin(
    ctx: TestContext, email: str = "admin@example.com", password: str = "AdminPassword!123"
) -> User:
    with ctx.database.session_factory() as db:
        user = User(
            email=email,
            username="admin",
            password_hash=hash_password(password),
            role="admin",
            active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def login(client: TestClient, email: str, password: str):
    return mutate(
        client,
        "POST",
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
