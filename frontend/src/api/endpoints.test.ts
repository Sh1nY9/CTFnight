import { api } from "./endpoints";
import { resetCsrfToken } from "./client";
import type { ChallengeWrite } from "./types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const challenge: ChallengeWrite = {
  slug: "welcome",
  title: "Welcome",
  category: "Misc",
  description_md: "hello",
  connection_info: null,
  scoring_type: "fixed",
  initial_points: 100,
  minimum_points: 100,
  decay: 0,
  max_attempts: 0,
  visible_at: null,
  prerequisite_ids: [],
  flag_type: "exact",
  flag: "FLAG{test}",
  visible: false,
};

describe("API endpoint serializers", () => {
  beforeEach(() => resetCsrfToken());
  afterEach(() => vi.unstubAllGlobals());

  it("serializes a challenge flag as the backend write-only object", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "challenge-id" }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await api.admin.createChallenge(challenge);

    const body = JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body)) as Record<string, unknown>;
    expect(body.flag).toEqual({ type: "exact", value: "FLAG{test}" });
    expect(body).not.toHaveProperty("flag_type");
  });

  it("unwraps the team envelope used by participant endpoints", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      team: { id: "team-1", name: "0xCTFnight", role: "owner", members: [] },
    }), { status: 200, headers: { "content-type": "application/json" } })));

    await expect(api.team.mine()).resolves.toMatchObject({ id: "team-1", name: "0xCTFnight" });
  });

  it("sends exact member ids for team ownership transfer and removal", async () => {
    const team = { id: "team-1", name: "0xCTFnight", role: "owner", members: [] };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ team }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ team }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await api.team.transferOwner("member/one");
    await api.team.removeMember("member-two");

    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/teams/transfer-owner");
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({ user_id: "member/one" });
    expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/teams/remove-member");
    expect(JSON.parse(String((fetchMock.mock.calls[2][1] as RequestInit).body))).toEqual({ user_id: "member-two" });
  });

  it("encodes the moderated user id and sends the status reason", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "user/one",
        username: "player",
        email: "player@example.test",
        role: "participant",
        active: false,
      }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await api.admin.setUserStatus("user/one", { active: false, reason: "운영 정책 위반" });

    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/admin/users/user%2Fone/status");
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({
      active: false,
      reason: "운영 정책 위반",
    });
  });

  it("sends a registration access code only through the registration write body", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "user-1",
        username: "player",
        email: "player@example.test",
        role: "participant",
        team: null,
      }), { status: 201, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await api.auth.register("player@example.test", "player", "CorrectHorse!123", "private-code");

    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/register");
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({
      email: "player@example.test",
      username: "player",
      password: "CorrectHorse!123",
      access_code: "private-code",
    });
  });

  it("encodes registration-code ids and preserves nullable usage limits", async () => {
    const created = {
      id: "code/one",
      event_id: "event-1",
      label: "invited",
      max_uses: null,
      use_count: 0,
      expires_at: null,
      active: true,
      created_by: "admin-1",
      created_at: "2026-08-24T00:00:00Z",
      revoked_at: null,
      access_code: "one-time-secret",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(created), { status: 201, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.admin.createRegistrationCode({ label: "invited", max_uses: null, expires_at: null }))
      .resolves.toMatchObject({ access_code: "one-time-secret", max_uses: null });
    await api.admin.revokeRegistrationCode("code/one");

    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({
      label: "invited",
      max_uses: null,
      expires_at: null,
    });
    expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/admin/registration-codes/code%2Fone");
  });

  it("normalizes nested admin submission identities for the audit table", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{
      id: "submission-1",
      team: { id: "team-1", name: "0xCTFnight" },
      user: { id: "user-1", username: "player" },
      challenge: { id: "challenge-1", title: "Welcome" },
      correct: true,
      awarded_points: 100,
      submitted_fingerprint: "abc123",
      ip_fingerprint: "def456",
      created_at: "2026-08-24T00:00:00Z",
    }] }), { status: 200, headers: { "content-type": "application/json" } })));

    await expect(api.admin.submissions()).resolves.toEqual([
      expect.objectContaining({
        team_name: "0xCTFnight",
        username: "player",
        challenge_title: "Welcome",
        submitted_fingerprint: "abc123",
      }),
    ]);
  });

  it("retries logout once with a freshly issued CSRF token", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "old" }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "csrf_failed", message: "expired" } }), { status: 403, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: "new" }), { status: 200, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.auth.logout()).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/auth/logout");
    expect(fetchMock.mock.calls[3][0]).toBe("/api/v1/auth/logout");
    expect(new Headers((fetchMock.mock.calls[3][1] as RequestInit).headers).get("X-CSRF-Token"))
      .toBe("new");
  });
});
