import { ApiError, apiRequest, resetCsrfToken } from "./client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("API client", () => {
  beforeEach(() => {
    resetCsrfToken();
    vi.restoreAllMocks();
  });

  afterEach(() => vi.unstubAllGlobals());

  it("adds the issued CSRF token to mutating requests", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "signed-token" }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/teams", { method: "POST", body: { name: "ctfnight" } });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/auth/csrf");
    const secondOptions = fetchMock.mock.calls[1][1] as RequestInit;
    expect(new Headers(secondOptions.headers).get("X-CSRF-Token")).toBe("signed-token");
    expect((fetchMock.mock.calls[0][1] as RequestInit).cache).toBe("no-store");
    expect(secondOptions.cache).toBe("no-store");
    expect(secondOptions.credentials).toBe("same-origin");
    expect(secondOptions.body).toBe('{"name":"ctfnight"}');
  });

  it("does not fetch a CSRF token for read-only requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ version: "test" }));
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/meta");

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/meta");
    expect((fetchMock.mock.calls[0][1] as RequestInit).cache).toBe("no-store");
  });

  it("forwards AbortSignal while forcing no-store over caller cache options", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ version: "test" }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await apiRequest("/meta", { cache: "reload", signal: controller.signal });

    const options = fetchMock.mock.calls[0][1] as RequestInit;
    expect(options.signal).toBe(controller.signal);
    expect(options.cache).toBe("no-store");
  });

  it("converts the documented error envelope to ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      error: { code: "invalid_credentials", message: "인증 정보가 올바르지 않습니다.", request_id: "req-1" },
    }, 401)));

    const request = apiRequest("/auth/me");
    await expect(request).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      code: "invalid_credentials",
      requestId: "req-1",
    } satisfies Partial<ApiError>);
  });

  it("keeps proxy errors generic when the response is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("gateway error", { status: 502 })));
    await expect(apiRequest("/scoreboard")).rejects.toThrow("잠시 후 다시 시도");
  });

  it.each(["csrf_failed", "csrf_invalid"])(
    "refreshes the token and retries a rejected mutation exactly once for %s",
    async (code) => {
      const fetchMock = vi.fn()
        .mockResolvedValueOnce(jsonResponse({ csrf_token: "stale-token" }))
        .mockResolvedValueOnce(jsonResponse({ error: { code, message: "CSRF rejected" } }, 403))
        .mockResolvedValueOnce(jsonResponse({ csrf_token: "fresh-token" }))
        .mockResolvedValueOnce(jsonResponse({ ok: true }));
      vi.stubGlobal("fetch", fetchMock);

      await expect(apiRequest("/teams", { method: "POST", body: { name: "ctfnight" } }))
        .resolves.toEqual({ ok: true });

      expect(fetchMock).toHaveBeenCalledTimes(4);
      expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
        "/api/v1/auth/csrf",
        "/api/v1/teams",
        "/api/v1/auth/csrf",
        "/api/v1/teams",
      ]);
      const firstMutation = fetchMock.mock.calls[1][1] as RequestInit;
      const retriedMutation = fetchMock.mock.calls[3][1] as RequestInit;
      expect(new Headers(firstMutation.headers).get("X-CSRF-Token")).toBe("stale-token");
      expect(new Headers(retriedMutation.headers).get("X-CSRF-Token")).toBe("fresh-token");
      expect(retriedMutation.body).toBe(firstMutation.body);
    },
  );

  it("does not loop when the one CSRF retry is also rejected", async () => {
    const rejected = jsonResponse({ error: { code: "csrf_failed", message: "CSRF rejected" } }, 403);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "stale-token" }))
      .mockResolvedValueOnce(rejected)
      .mockResolvedValueOnce(jsonResponse({ csrf_token: "fresh-token" }))
      .mockResolvedValueOnce(jsonResponse({ error: { code: "csrf_failed", message: "still rejected" } }, 403));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest("/teams", { method: "POST", body: { name: "ctfnight" } }))
      .rejects.toMatchObject({ status: 403, code: "csrf_failed" });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("only broadcasts unauthorized when the session itself is invalid", async () => {
    const unauthorized = vi.fn();
    window.addEventListener("alpha:unauthorized", unauthorized);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: { code: "invalid_current_password", message: "wrong" } }, 401))
      .mockResolvedValueOnce(jsonResponse({ error: { code: "invalid_session", message: "expired" } }, 401));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest("/auth/change-password")).rejects.toMatchObject({ code: "invalid_current_password" });
    expect(unauthorized).not.toHaveBeenCalled();
    await expect(apiRequest("/auth/me")).rejects.toMatchObject({ code: "invalid_session" });
    expect(unauthorized).toHaveBeenCalledOnce();
    window.removeEventListener("alpha:unauthorized", unauthorized);
  });
});
