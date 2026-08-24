from __future__ import annotations

import os
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.schema import CreateSchema, DropSchema

from alpha.config import Settings
from alpha.db import Database
from alpha.main import create_app
from alpha.models import (
    AuditEvent,
    Base,
    Challenge,
    Event,
    Membership,
    OutboxEvent,
    ScoreEvent,
    SessionToken,
    Solve,
    Submission,
    Team,
    User,
    utcnow,
)
from alpha.security import RegexResult, hash_flag, hash_password, hash_session
from alpha.services import dynamic_points, public_scoreboard
from alpha.store import MemoryStore

POSTGRES_URL = os.getenv("ALPHA_TEST_POSTGRES_URL")


@pytest.fixture
def postgres_app():
    if not POSTGRES_URL:
        pytest.skip("ALPHA_TEST_POSTGRES_URL is required for PostgreSQL concurrency tests")
    schema = f"alpha_test_{uuid.uuid4().hex}"
    database = Database(POSTGRES_URL)
    administration_engine = database.engine
    with administration_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    translated_engine = database.engine.execution_options(schema_translate_map={None: schema})
    database.engine = translated_engine
    database.session_factory.configure(bind=translated_engine)
    Base.metadata.create_all(database.engine)
    settings = Settings(
        environment="test",
        database_url=POSTGRES_URL,
        redis_url="memory://",
        secret_key="postgres-concurrency-test-secret-that-is-long-enough",
        cookie_secure=False,
        allowed_origins=["http://testserver"],
        trusted_hosts=["testserver"],
        auth_rate_limit=100,
        auth_ip_rate_limit=100,
        submission_rate_limit=100,
        submission_ip_rate_limit=100,
        submission_challenge_rate_limit=100,
    )
    app = create_app(settings=settings, database=database, store=MemoryStore())
    try:
        yield database, settings, app
    finally:
        app.state.store.close()
        database.dispose()
        with administration_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        administration_engine.dispose()


def _csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _post(client: TestClient, path: str, token: str, body: dict) -> object:
    return client.post(path, headers={"X-CSRF-Token": token}, json=body)


def test_postgresql_public_scoreboard_uses_bounded_sql_aggregate(postgres_app):
    database, _settings, _app = postgres_app
    now = utcnow()
    with database.session_factory() as db:
        event = Event(name="SQL Scoreboard", slug="sql-scoreboard", state="live", start_at=now)
        users = [
            User(
                email=f"score-{index}@example.com",
                username=f"score-{index}",
                password_hash="unused",
            )
            for index in range(2)
        ]
        db.add_all([event, *users])
        db.flush()
        teams = [
            Team(
                name=f"Score Team {index}",
                invite_hash=f"{index + 1:064x}",
                creator_id=user.id,
            )
            for index, user in enumerate(users)
        ]
        db.add_all(teams)
        db.flush()
        db.add_all(
            [
                Membership(user_id=user.id, team_id=team.id, role="owner")
                for user, team in zip(users, teams, strict=True)
            ]
        )
        challenge = Challenge(
            event_id=event.id,
            slug="sql-dynamic",
            title="SQL Dynamic",
            category="Misc",
            connection_info=None,
            scoring_type="dynamic",
            initial_points=500,
            minimum_points=100,
            decay=10,
            visible=True,
            max_attempts=0,
            flag_type="exact",
            flag_hash="f" * 64,
        )
        db.add(challenge)
        db.flush()
        for index, (user, team) in enumerate(zip(users, teams, strict=True)):
            submission = Submission(
                team_id=team.id,
                challenge_id=challenge.id,
                user_id=user.id,
                submitted_hash=f"{index + 3:064x}",
                correct=True,
                awarded_points=dynamic_points(500, 100, 10, index + 1),
                idempotency_key=f"score-{index}",
                ip_hash=f"{index + 5:064x}",
            )
            db.add(submission)
            db.flush()
            solve = Solve(
                team_id=team.id,
                challenge_id=challenge.id,
                user_id=user.id,
                submission_id=submission.id,
            )
            db.add(solve)
            db.flush()
            db.add(
                ScoreEvent(
                    event_id=event.id,
                    team_id=team.id,
                    challenge_id=challenge.id,
                    solve_id=solve.id,
                    kind="solve",
                    points=submission.awarded_points,
                )
            )
        db.add(
            ScoreEvent(
                event_id=event.id,
                team_id=teams[0].id,
                kind="award",
                points=10,
            )
        )
        db.commit()

        board = public_scoreboard(db, event, now)
        current_points = dynamic_points(500, 100, 10, 2)
        assert board["total_entries"] == 2
        assert board["truncated"] is False
        assert [entry["score"] for entry in board["entries"]] == [
            current_points + 10,
            current_points,
        ]


def test_password_change_wins_against_stale_concurrent_login(postgres_app, monkeypatch):
    database, _settings, app = postgres_app
    old_password = "OriginalPassword!123"
    new_password = "ChangedPassword!456"
    with database.session_factory() as db:
        user = User(
            email="race@example.com",
            username="race",
            password_hash=hash_password(old_password),
        )
        db.add(user)
        db.commit()
        user_id = user.id

    victim = TestClient(app)
    attacker = TestClient(app)
    initial_login = _post(
        victim,
        "/api/v1/auth/login",
        _csrf(victim),
        {"email": "race@example.com", "password": old_password},
    )
    assert initial_login.status_code == 200
    victim_csrf = _csrf(victim)
    attacker_csrf = _csrf(attacker)

    from alpha import routes_auth

    real_verify = routes_auth.verify_password
    first_verification_complete = threading.Event()
    resume_stale_login = threading.Event()
    attacker_calls = 0

    def controlled_verify(password_hash: str, password: str) -> bool:
        nonlocal attacker_calls
        result = real_verify(password_hash, password)
        if password == old_password:
            attacker_calls += 1
            if attacker_calls == 1 and result:
                first_verification_complete.set()
                assert resume_stale_login.wait(timeout=10)
        return result

    monkeypatch.setattr(routes_auth, "verify_password", controlled_verify)
    attacker_responses: list[object] = []

    def stale_login() -> None:
        attacker_responses.append(
            _post(
                attacker,
                "/api/v1/auth/login",
                attacker_csrf,
                {"email": "race@example.com", "password": old_password},
            )
        )

    thread = threading.Thread(target=stale_login, name="stale-login")
    thread.start()
    assert first_verification_complete.wait(timeout=10)
    try:
        changed = _post(
            victim,
            "/api/v1/auth/change-password",
            victim_csrf,
            {"current_password": old_password, "new_password": new_password},
        )
        assert changed.status_code == 200
    finally:
        resume_stale_login.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(attacker_responses) == 1
    assert attacker_responses[0].status_code == 401

    with database.session_factory() as db:
        user = db.get(User, user_id)
        sessions = list(db.scalars(select(SessionToken).where(SessionToken.user_id == user_id)))
        assert user is not None
        assert user.credential_version == 1
        assert sessions
        assert all(item.credential_version == user.credential_version for item in sessions)
    victim.close()
    attacker.close()


def test_postgresql_event_lock_enforces_participant_capacity_under_concurrency(postgres_app, monkeypatch):
    database, _settings, app = postgres_app
    from alpha import routes_auth

    monkeypatch.setattr(routes_auth, "MAX_PARTICIPANT_USERS", 1)
    with database.session_factory() as db:
        db.add(
            Event(
                name="Registration Capacity",
                slug="registration-capacity",
                state="registration",
                registration_at=utcnow(),
            )
        )
        db.commit()

    clients = [TestClient(app), TestClient(app)]
    csrf_tokens = [_csrf(client) for client in clients]
    start = threading.Barrier(2)
    responses: list[object] = []

    def register(index: int) -> None:
        start.wait(timeout=10)
        responses.append(
            _post(
                clients[index],
                "/api/v1/auth/register",
                csrf_tokens[index],
                {
                    "email": f"capacity-{index}@example.com",
                    "username": f"capacity-{index}",
                    "password": "CapacityPassword!123",
                },
            )
        )

    threads = [threading.Thread(target=register, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(response.status_code for response in responses) == [201, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["error"]["code"] == "participant_capacity_reached"
    with database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.role == "participant")) == 1
        assert db.scalar(select(func.count()).select_from(SessionToken)) == 1
        assert (
            db.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "auth.register")
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count()).select_from(OutboxEvent).where(OutboxEvent.topic == "user.registered")
            )
            == 1
        )
    for client in clients:
        client.close()


def test_incorrect_regex_submissions_from_different_teams_run_in_parallel(postgres_app, monkeypatch):
    database, settings, app = postgres_app
    session_data: list[tuple[str, uuid.UUID]] = []
    with database.session_factory() as db:
        event = Event(name="Concurrency", slug="concurrency", state="live", start_at=utcnow())
        db.add(event)
        db.flush()
        challenge = Challenge(
            event_id=event.id,
            slug="regex",
            title="Regex",
            category="Misc",
            description_md="Parallel regex test",
            visible=True,
            flag_type="regex",
            flag_regex=r"^FLAG\{[0-9]{4}\}$",
        )
        db.add(challenge)
        for index in range(2):
            user = User(
                email=f"player{index}@example.com",
                username=f"player{index}",
                password_hash=hash_password("UnusedPassword!123"),
            )
            db.add(user)
            db.flush()
            team = Team(
                name=f"Team {index}",
                invite_hash=f"{index + 1:064x}",
                creator_id=user.id,
            )
            db.add(team)
            db.flush()
            db.add(Membership(user_id=user.id, team_id=team.id, role="owner"))
            raw_token = f"parallel-session-token-{index}"
            db.add(
                SessionToken(
                    token_hash=hash_session(settings.secret_key.get_secret_value(), raw_token),
                    user_id=user.id,
                    credential_version=0,
                    expires_at=utcnow().replace(year=utcnow().year + 1),
                )
            )
            session_data.append((raw_token, user.id))
        db.commit()
        challenge_id = challenge.id

    clients = [TestClient(app), TestClient(app)]
    tokens: list[str] = []
    for client, (raw_token, _user_id) in zip(clients, session_data, strict=True):
        client.cookies.set("alpha_session", raw_token)
        tokens.append(_csrf(client))

    rendezvous = threading.Barrier(2)

    def concurrent_non_match(pattern: str | None, candidate: str, timeout: float) -> RegexResult:
        rendezvous.wait(timeout=10)
        return RegexResult(False)

    monkeypatch.setattr("alpha.routes_participant.compare_regex_flag", concurrent_non_match)
    responses: list[object] = []

    def submit(index: int) -> None:
        responses.append(
            _post(
                clients[index],
                f"/api/v1/challenges/{challenge_id}/submit",
                tokens[index],
                {"flag": f"wrong-{index}", "idempotency_key": f"parallel-{index}"},
            )
        )

    threads = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert len(responses) == 2
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["correct"] is False for response in responses)
    for client in clients:
        client.close()


def test_postgresql_team_lock_enforces_submission_cap_under_concurrency(postgres_app, monkeypatch):
    database, settings, app = postgres_app
    from alpha import routes_participant

    monkeypatch.setattr(routes_participant, "MAX_CHALLENGE_ATTEMPTS", 1)
    monkeypatch.setattr(routes_participant, "MAX_SUBMISSIONS_PER_TEAM_EVENT", 10)
    raw_token = "same-team-cap-session-token"
    with database.session_factory() as db:
        event = Event(name="Cap", slug="cap", state="live", start_at=utcnow())
        db.add(event)
        db.flush()
        challenge = Challenge(
            event_id=event.id,
            slug="cap-regex",
            title="Cap Regex",
            category="Misc",
            description_md="Concurrent cap test",
            visible=True,
            flag_type="regex",
            flag_regex=r"^FLAG\{correct\}$",
        )
        user = User(
            email="cap-player@example.com",
            username="cap-player",
            password_hash=hash_password("UnusedPassword!123"),
        )
        db.add_all([challenge, user])
        db.flush()
        team = Team(name="Cap Team", invite_hash="f" * 64, creator_id=user.id)
        db.add(team)
        db.flush()
        db.add(Membership(user_id=user.id, team_id=team.id, role="owner"))
        db.add(
            SessionToken(
                token_hash=hash_session(settings.secret_key.get_secret_value(), raw_token),
                user_id=user.id,
                credential_version=0,
                expires_at=utcnow().replace(year=utcnow().year + 1),
            )
        )
        db.commit()
        challenge_id = challenge.id

    clients = [TestClient(app), TestClient(app)]
    tokens = []
    for client in clients:
        client.cookies.set("alpha_session", raw_token)
        tokens.append(_csrf(client))
    start = threading.Barrier(2)
    responses: list[object] = []

    def submit(index: int) -> None:
        start.wait(timeout=10)
        responses.append(
            _post(
                clients[index],
                f"/api/v1/challenges/{challenge_id}/submit",
                tokens[index],
                {"flag": f"wrong-{index}", "idempotency_key": f"cap-race-{index}"},
            )
        )

    threads = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(response.status_code for response in responses) == [200, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["error"]["code"] == "submission_storage_limit_reached"
    with database.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.topic == "submission.incorrect")
            )
            == 0
        )
    for client in clients:
        client.close()


def test_postgresql_row_lock_wait_returns_bounded_retryable_error(postgres_app):
    database, _settings, app = postgres_app
    password = "LockTimeoutPassword!123"
    with database.session_factory() as db:
        user = User(
            email="locked@example.com",
            username="locked",
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        user_id = user.id

    client = TestClient(app)
    with database.session_factory() as locker:
        locked = locker.scalar(select(User).where(User.id == user_id).with_for_update())
        assert locked is not None
        response = _post(
            client,
            "/api/v1/auth/login",
            _csrf(client),
            {"email": "locked@example.com", "password": password},
        )
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        assert response.json()["error"]["code"] == "database_temporarily_unavailable"
    client.close()


def test_argon_work_returns_postgresql_connections_to_the_pool(postgres_app, monkeypatch):
    database, _settings, app = postgres_app
    current_password = "PoolReleasePassword!123"
    register_password = "RegisterWithoutConnection!123"
    new_password = "ChangedWithoutConnection!456"
    with database.session_factory() as db:
        db.add(
            Event(
                name="Pool Release",
                slug="pool-release",
                state="registration",
                registration_at=utcnow(),
            )
        )
        db.add(
            User(
                email="pool@example.com",
                username="pool-user",
                password_hash=hash_password(current_password),
            )
        )
        db.commit()

    from alpha import routes_auth

    real_hash = routes_auth.hash_password
    real_verify = routes_auth.verify_password
    observations: list[tuple[str, int]] = []

    def assert_released(label: str) -> None:
        checked_out = database.engine.pool.checkedout()
        observations.append((label, checked_out))
        assert checked_out == 0

    def tracked_hash(password: str) -> str:
        assert_released(f"hash:{password}")
        return real_hash(password)

    def tracked_verify(password_hash: str, password: str) -> bool:
        assert_released(f"verify:{password}")
        return real_verify(password_hash, password)

    monkeypatch.setattr(routes_auth, "hash_password", tracked_hash)
    monkeypatch.setattr(routes_auth, "verify_password", tracked_verify)

    client = TestClient(app)
    logged_in = _post(
        client,
        "/api/v1/auth/login",
        _csrf(client),
        {"email": "pool@example.com", "password": current_password},
    )
    assert logged_in.status_code == 200

    registering_client = TestClient(app)
    registered = _post(
        registering_client,
        "/api/v1/auth/register",
        _csrf(registering_client),
        {
            "email": "pool-register@example.com",
            "username": "pool-register",
            "password": register_password,
        },
    )
    assert registered.status_code == 201

    changed = _post(
        client,
        "/api/v1/auth/change-password",
        _csrf(client),
        {"current_password": current_password, "new_password": new_password},
    )
    assert changed.status_code == 200
    assert {label for label, _count in observations} >= {
        f"verify:{current_password}",
        f"hash:{register_password}",
        f"hash:{new_password}",
    }
    client.close()
    registering_client.close()


def _seed_team_operation_race(database, settings, prefix: str, member_count: int = 1):
    raw_owner_token = f"{prefix}-owner-session-token"
    with database.session_factory() as db:
        event = Event(
            name=f"{prefix} event",
            slug=f"{prefix}-event",
            state="registration",
            registration_at=utcnow(),
            team_mode="team",
        )
        owner = User(
            email=f"{prefix}-owner@example.com",
            username=f"{prefix}-owner",
            password_hash="unused",
        )
        members = [
            User(
                email=f"{prefix}-member-{index}@example.com",
                username=f"{prefix}-member-{index}",
                password_hash="unused",
            )
            for index in range(member_count)
        ]
        db.add_all([event, owner, *members])
        db.flush()
        team = Team(name=f"{prefix} team", invite_hash="e" * 64, creator_id=owner.id)
        db.add(team)
        db.flush()
        db.add(Membership(user_id=owner.id, team_id=team.id, role="owner"))
        db.add_all([Membership(user_id=member.id, team_id=team.id, role="member") for member in members])
        db.add(
            SessionToken(
                token_hash=hash_session(settings.secret_key.get_secret_value(), raw_owner_token),
                user_id=owner.id,
                credential_version=0,
                expires_at=utcnow().replace(year=utcnow().year + 1),
            )
        )
        db.commit()
        return {
            "event_id": event.id,
            "team_id": team.id,
            "owner_id": owner.id,
            "member_ids": [member.id for member in members],
            "owner_token": raw_owner_token,
        }


def test_postgresql_concurrent_owner_transfers_have_exactly_one_winner(postgres_app):
    database, settings, app = postgres_app
    seeded = _seed_team_operation_race(database, settings, "transfer-race", member_count=2)
    clients = [TestClient(app), TestClient(app)]
    csrf_tokens = []
    for client in clients:
        client.cookies.set("alpha_session", seeded["owner_token"])
        csrf_tokens.append(_csrf(client))
    start = threading.Barrier(2)
    responses: list[object] = []

    def transfer(index: int) -> None:
        start.wait(timeout=10)
        responses.append(
            _post(
                clients[index],
                "/api/v1/teams/transfer-owner",
                csrf_tokens[index],
                {"user_id": str(seeded["member_ids"][index])},
            )
        )

    threads = [threading.Thread(target=transfer, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(response.status_code for response in responses) == [200, 403]
    rejected = next(response for response in responses if response.status_code == 403)
    assert rejected.json()["error"]["code"] == "team_owner_required"

    with database.session_factory() as db:
        memberships = list(db.scalars(select(Membership).where(Membership.team_id == seeded["team_id"])))
        assert sum(item.role == "owner" for item in memberships) == 1
        assert next(item for item in memberships if item.user_id == seeded["owner_id"]).role == "member"
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "team.owner_transferred")
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.topic == "team.owner_transferred")
            )
            == 1
        )
    for client in clients:
        client.close()


def test_postgresql_concurrent_member_removals_have_exactly_one_winner(postgres_app):
    database, settings, app = postgres_app
    seeded = _seed_team_operation_race(database, settings, "remove-race")
    clients = [TestClient(app), TestClient(app)]
    csrf_tokens = []
    for client in clients:
        client.cookies.set("alpha_session", seeded["owner_token"])
        csrf_tokens.append(_csrf(client))
    start = threading.Barrier(2)
    responses: list[object] = []

    def remove(index: int) -> None:
        start.wait(timeout=10)
        responses.append(
            _post(
                clients[index],
                "/api/v1/teams/remove-member",
                csrf_tokens[index],
                {"user_id": str(seeded["member_ids"][0])},
            )
        )

    threads = [threading.Thread(target=remove, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(response.status_code for response in responses) == [200, 404]
    rejected = next(response for response in responses if response.status_code == 404)
    assert rejected.json()["error"]["code"] == "team_member_not_found"

    with database.session_factory() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Membership).where(Membership.team_id == seeded["team_id"])
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "team.member_removed")
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.topic == "team.member_removed")
            )
            == 1
        )
    for client in clients:
        client.close()


def test_postgresql_state_transition_waits_for_inflight_owner_transfer(postgres_app, monkeypatch):
    database, settings, app = postgres_app
    seeded = _seed_team_operation_race(database, settings, "state-transfer")
    raw_admin_token = "state-transfer-admin-session-token"
    with database.session_factory() as db:
        admin = User(
            email="state-transfer-admin@example.com",
            username="state-transfer-admin",
            password_hash="unused",
            role="admin",
        )
        db.add(admin)
        db.flush()
        db.add(
            SessionToken(
                token_hash=hash_session(settings.secret_key.get_secret_value(), raw_admin_token),
                user_id=admin.id,
                credential_version=0,
                expires_at=utcnow().replace(year=utcnow().year + 1),
            )
        )
        db.commit()

    from alpha import routes_teams

    real_lock_users = routes_teams._lock_users
    event_lock_acquired = threading.Event()
    resume_transfer = threading.Event()
    pause_once = True

    def pause_after_event_lock(db, user_ids):
        nonlocal pause_once
        if pause_once and len(set(user_ids)) == 2:
            pause_once = False
            event_lock_acquired.set()
            assert resume_transfer.wait(timeout=10)
        return real_lock_users(db, user_ids)

    monkeypatch.setattr(routes_teams, "_lock_users", pause_after_event_lock)
    owner_client = TestClient(app)
    admin_client = TestClient(app)
    owner_client.cookies.set("alpha_session", seeded["owner_token"])
    admin_client.cookies.set("alpha_session", raw_admin_token)
    owner_csrf = _csrf(owner_client)
    admin_csrf = _csrf(admin_client)
    transfer_responses: list[object] = []
    state_responses: list[object] = []

    transfer_thread = threading.Thread(
        target=lambda: transfer_responses.append(
            _post(
                owner_client,
                "/api/v1/teams/transfer-owner",
                owner_csrf,
                {"user_id": str(seeded["member_ids"][0])},
            )
        )
    )
    transfer_thread.start()
    assert event_lock_acquired.wait(timeout=10)
    state_thread = threading.Thread(
        target=lambda: state_responses.append(
            admin_client.put(
                "/api/v1/admin/event",
                headers={"X-CSRF-Token": admin_csrf},
                json={"state": "live"},
            )
        )
    )
    state_thread.start()
    try:
        state_thread.join(timeout=0.25)
        assert state_thread.is_alive()
    finally:
        resume_transfer.set()
    transfer_thread.join(timeout=10)
    state_thread.join(timeout=10)
    assert not transfer_thread.is_alive()
    assert not state_thread.is_alive()
    assert transfer_responses[0].status_code == 200
    assert state_responses[0].status_code == 200
    with database.session_factory() as db:
        event = db.get(Event, seeded["event_id"])
        assert event is not None and event.state == "live"
        owner_membership = db.scalar(select(Membership).where(Membership.user_id == seeded["member_ids"][0]))
        assert owner_membership is not None and owner_membership.role == "owner"
    owner_client.close()
    admin_client.close()


def test_postgresql_suspension_committed_before_user_lock_cancels_team_mutation(postgres_app, monkeypatch):
    database, settings, app = postgres_app
    seeded = _seed_team_operation_race(database, settings, "suspend-transfer")
    raw_admin_token = "suspend-transfer-admin-session-token"
    with database.session_factory() as db:
        admin = User(
            email="suspend-transfer-admin@example.com",
            username="suspend-transfer-admin",
            password_hash="unused",
            role="admin",
        )
        db.add(admin)
        db.flush()
        db.add(
            SessionToken(
                token_hash=hash_session(settings.secret_key.get_secret_value(), raw_admin_token),
                user_id=admin.id,
                credential_version=0,
                expires_at=utcnow().replace(year=utcnow().year + 1),
            )
        )
        db.commit()

    from alpha import routes_teams

    real_lock_users = routes_teams._lock_users
    ready_to_lock_users = threading.Event()
    resume_transfer = threading.Event()
    pause_once = True

    def pause_before_user_lock(db, user_ids):
        nonlocal pause_once
        if pause_once and len(set(user_ids)) == 2:
            pause_once = False
            ready_to_lock_users.set()
            assert resume_transfer.wait(timeout=10)
        return real_lock_users(db, user_ids)

    monkeypatch.setattr(routes_teams, "_lock_users", pause_before_user_lock)
    owner_client = TestClient(app)
    admin_client = TestClient(app)
    owner_client.cookies.set("alpha_session", seeded["owner_token"])
    admin_client.cookies.set("alpha_session", raw_admin_token)
    owner_csrf = _csrf(owner_client)
    admin_csrf = _csrf(admin_client)
    transfer_responses: list[object] = []
    transfer_thread = threading.Thread(
        target=lambda: transfer_responses.append(
            _post(
                owner_client,
                "/api/v1/teams/transfer-owner",
                owner_csrf,
                {"user_id": str(seeded["member_ids"][0])},
            )
        )
    )
    transfer_thread.start()
    assert ready_to_lock_users.wait(timeout=10)
    try:
        suspended = admin_client.put(
            f"/api/v1/admin/users/{seeded['owner_id']}/status",
            headers={"X-CSRF-Token": admin_csrf},
            json={"active": False, "reason": "concurrency regression"},
        )
        assert suspended.status_code == 200
    finally:
        resume_transfer.set()
    transfer_thread.join(timeout=10)
    assert not transfer_thread.is_alive()
    assert transfer_responses[0].status_code == 401
    assert transfer_responses[0].json()["error"]["code"] == "invalid_session"
    with database.session_factory() as db:
        owner = db.get(User, seeded["owner_id"])
        owner_membership = db.scalar(select(Membership).where(Membership.user_id == seeded["owner_id"]))
        target_membership = db.scalar(select(Membership).where(Membership.user_id == seeded["member_ids"][0]))
        assert owner is not None and owner.active is False and owner.credential_version == 1
        assert owner_membership is not None and owner_membership.role == "owner"
        assert target_membership is not None and target_membership.role == "member"
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "team.owner_transferred")
            )
            == 0
        )
    owner_client.close()
    admin_client.close()


def test_postgresql_suspension_committed_before_user_lock_cancels_submission(postgres_app, monkeypatch):
    database, settings, app = postgres_app
    raw_participant_token = "suspend-submit-participant-session-token"
    raw_admin_token = "suspend-submit-admin-session-token"
    secret = settings.secret_key.get_secret_value()
    with database.session_factory() as db:
        event = Event(
            name="Suspend submission",
            slug="suspend-submission",
            state="live",
            start_at=utcnow(),
        )
        participant = User(
            email="suspend-submit-participant@example.com",
            username="suspend-submit-participant",
            password_hash="unused",
        )
        admin = User(
            email="suspend-submit-admin@example.com",
            username="suspend-submit-admin",
            password_hash="unused",
            role="admin",
        )
        db.add_all([event, participant, admin])
        db.flush()
        team = Team(
            name="Suspend submit team",
            invite_hash="d" * 64,
            creator_id=participant.id,
        )
        challenge = Challenge(
            event_id=event.id,
            slug="suspend-submit-challenge",
            title="Suspend submit challenge",
            category="Misc",
            visible=True,
            flag_type="exact",
            flag_hash=hash_flag(secret, "FLAG{suspend-submit}"),
        )
        db.add_all([team, challenge])
        db.flush()
        db.add(Membership(user_id=participant.id, team_id=team.id, role="owner"))
        db.add_all(
            [
                SessionToken(
                    token_hash=hash_session(secret, raw_participant_token),
                    user_id=participant.id,
                    credential_version=0,
                    expires_at=utcnow().replace(year=utcnow().year + 1),
                ),
                SessionToken(
                    token_hash=hash_session(secret, raw_admin_token),
                    user_id=admin.id,
                    credential_version=0,
                    expires_at=utcnow().replace(year=utcnow().year + 1),
                ),
            ]
        )
        db.commit()
        participant_id = participant.id
        challenge_id = challenge.id

    from alpha import routes_participant

    real_check_submission_rate = routes_participant._check_submission_rate
    ready_for_suspension = threading.Event()
    resume_submission = threading.Event()
    pause_once = True

    def pause_after_rate_limit(*args, **kwargs):
        nonlocal pause_once
        real_check_submission_rate(*args, **kwargs)
        if pause_once:
            pause_once = False
            ready_for_suspension.set()
            assert resume_submission.wait(timeout=10)

    monkeypatch.setattr(routes_participant, "_check_submission_rate", pause_after_rate_limit)
    participant_client = TestClient(app)
    admin_client = TestClient(app)
    participant_client.cookies.set("alpha_session", raw_participant_token)
    admin_client.cookies.set("alpha_session", raw_admin_token)
    participant_csrf = _csrf(participant_client)
    admin_csrf = _csrf(admin_client)
    submission_responses: list[object] = []
    submission_thread = threading.Thread(
        target=lambda: submission_responses.append(
            _post(
                participant_client,
                f"/api/v1/challenges/{challenge_id}/submit",
                participant_csrf,
                {"flag": "FLAG{suspend-submit}", "idempotency_key": "suspend-submit"},
            )
        )
    )
    submission_thread.start()
    assert ready_for_suspension.wait(timeout=10)
    try:
        suspended = admin_client.put(
            f"/api/v1/admin/users/{participant_id}/status",
            headers={"X-CSRF-Token": admin_csrf},
            json={"active": False, "reason": "concurrency regression"},
        )
        assert suspended.status_code == 200
    finally:
        resume_submission.set()
    submission_thread.join(timeout=10)
    assert not submission_thread.is_alive()
    assert submission_responses[0].status_code == 401
    assert submission_responses[0].json()["error"]["code"] == "invalid_session"
    with database.session_factory() as db:
        participant = db.get(User, participant_id)
        assert participant is not None and participant.active is False
        assert participant.credential_version == 1
        assert db.scalar(select(func.count()).select_from(Submission)) == 0
        assert (
            db.scalar(
                select(func.count()).select_from(SessionToken).where(SessionToken.user_id == participant_id)
            )
            == 0
        )
    participant_client.close()
    admin_client.close()
