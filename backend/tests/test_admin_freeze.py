from __future__ import annotations

from conftest import create_admin, login, mutate, register
from test_challenges import create_challenge, set_live


def test_announcements_admin_audit_and_flag_never_returned(ctx):
    create_admin(ctx)
    login(ctx.client, "admin@example.com", "AdminPassword!123")
    created = mutate(
        ctx.client,
        "POST",
        "/api/v1/admin/announcements",
        json={"title": "Notice", "body_md": "**Hello**"},
    )
    assert created.status_code == 201
    public = ctx.client.get("/api/v1/announcements").json()["items"]
    assert public[0]["title"] == "Notice"
    updated = mutate(
        ctx.client,
        "PUT",
        f"/api/v1/admin/announcements/{created.json()['id']}",
        json={"title": "Updated"},
    )
    assert updated.json()["title"] == "Updated"
    assert ctx.client.get("/api/v1/admin/audit").status_code == 200
    outbox = ctx.client.get("/api/v1/admin/outbox")
    assert outbox.status_code == 200
    assert outbox.json()["items"]
    assert all(item["delivered_at"] is None and item["attempts"] == 0 for item in outbox.json()["items"])
    assert (
        mutate(ctx.client, "DELETE", f"/api/v1/admin/announcements/{created.json()['id']}").status_code == 204
    )


def test_frozen_scoreboard_hides_later_solves_while_accepting_them(ctx):
    create_admin(ctx)
    login(ctx.client, "admin@example.com", "AdminPassword!123")
    challenge = create_challenge(ctx)

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    register(ctx.client, "one")
    mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "One"})
    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    register(ctx.client, "two")
    mutate(ctx.client, "POST", "/api/v1/teams", json={"name": "Two"})

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "admin@example.com", "AdminPassword!123")
    set_live(ctx)

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "one@example.com", "CorrectHorse!123")
    mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{welcome}", "idempotency_key": "before-freeze"},
    )

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "admin@example.com", "AdminPassword!123")
    frozen = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": "frozen"})
    assert frozen.status_code == 200
    assert frozen.json()["freeze_at"] is not None

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "two@example.com", "CorrectHorse!123")
    after = mutate(
        ctx.client,
        "POST",
        f"/api/v1/challenges/{challenge['id']}/submit",
        json={"flag": "FLAG{welcome}", "idempotency_key": "after-freeze"},
    )
    assert after.status_code == 200 and after.json()["correct"] is True
    board = ctx.client.get("/api/v1/scoreboard").json()
    assert board["frozen"] is True
    assert [entry["team_name"] for entry in board["entries"]] == ["One"]

    mutate(ctx.client, "POST", "/api/v1/auth/logout")
    login(ctx.client, "admin@example.com", "AdminPassword!123")
    ended = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": "ended"})
    assert ended.status_code == 200
    final_board = ctx.client.get("/api/v1/scoreboard").json()
    assert final_board["frozen"] is False
    assert {entry["team_name"] for entry in final_board["entries"]} == {"One", "Two"}


def test_event_state_cannot_move_backwards(ctx):
    create_admin(ctx)
    login(ctx.client, "admin@example.com", "AdminPassword!123")
    set_live(ctx)
    response = mutate(ctx.client, "PUT", "/api/v1/admin/event", json={"state": "registration"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state_transition"
