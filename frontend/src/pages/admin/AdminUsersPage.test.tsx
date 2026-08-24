import type { AdminUser } from "@/api/types";
import { ToastProvider } from "@/components/Toast";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminUsersPage } from "./AdminUsersPage";

const userMocks = vi.hoisted(() => ({
  users: vi.fn(),
  setUserStatus: vi.fn(),
}));

vi.mock("@/api/endpoints", () => ({ api: { admin: userMocks } }));

function makeUser(index: number, overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    id: `user-${index}`,
    username: `player-${index}`,
    email: `player-${index}@example.test`,
    role: "participant",
    active: true,
    created_at: "2026-08-24T00:00:00Z",
    ...overrides,
  };
}

function pendingUntilAbort(signal: AbortSignal): Promise<AdminUser[]> {
  return new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), {
      once: true,
    });
  });
}

function renderPage() {
  return render(<ToastProvider><AdminUsersPage /></ToastProvider>);
}

describe("admin user moderation", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("loads users in bounded pages and requests the next offset explicitly", async () => {
    userMocks.users
      .mockResolvedValueOnce(Array.from({ length: 100 }, (_, index) => makeUser(index + 1)))
      .mockResolvedValueOnce([makeUser(101)]);
    renderPage();

    expect(await screen.findByText(/서버에서 현재 100명을 불러왔습니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "더 불러오기" }));

    expect(await screen.findByText(/서버에서 현재 101명을 불러왔습니다/)).toBeInTheDocument();
    expect(userMocks.users).toHaveBeenCalledTimes(2);
    expect(userMocks.users.mock.calls[0][0]).toEqual({ limit: 100, offset: 0 });
    expect(userMocks.users.mock.calls[1][0]).toEqual({ limit: 100, offset: 100 });
    expect(userMocks.users.mock.calls[1][1].signal).toBeInstanceOf(AbortSignal);
  });

  it("never offers moderation actions for admins but supports both participant states", async () => {
    userMocks.users.mockResolvedValue([
      makeUser(1, { username: "operator", email: "admin@example.test", role: "admin" }),
      makeUser(2, { username: "active-player" }),
      makeUser(3, { username: "paused-player", active: false }),
    ]);
    renderPage();

    expect(await screen.findByText("operator")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "operator 계정 정지" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "operator 계정 재활성화" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "active-player 계정 정지" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "paused-player 계정 재활성화" })).toBeEnabled();
  });

  it("requires a bounded reason and a second confirmation before suspending", async () => {
    const participant = makeUser(1, { username: "target" });
    userMocks.users.mockResolvedValue([participant]);
    userMocks.setUserStatus.mockResolvedValue({ ...participant, active: false });
    const confirm = vi.spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "target 계정 정지" }));
    const submit = screen.getByRole("button", { name: "사유 확인 후 정지" });
    const form = submit.closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    expect(await screen.findByText("정지 사유를 입력해 주세요.")).toBeInTheDocument();
    expect(userMocks.setUserStatus).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole("textbox", { name: "정지 사유" }), {
      target: { value: "  반복적인 운영 정책 위반  " },
    });
    fireEvent.submit(form!);
    expect(confirm).toHaveBeenCalledOnce();
    expect(userMocks.setUserStatus).not.toHaveBeenCalled();

    fireEvent.submit(form!);
    await waitFor(() => expect(userMocks.setUserStatus).toHaveBeenCalledWith("user-1", {
      active: false,
      reason: "반복적인 운영 정책 위반",
    }));
    expect(await screen.findByText("target 계정을 정지했습니다.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "target 계정 재활성화" })).toBeEnabled();
  });

  it("requires confirmation and sends an explicit empty reason when reactivating", async () => {
    const participant = makeUser(1, { username: "target", active: false });
    userMocks.users.mockResolvedValue([participant]);
    userMocks.setUserStatus.mockResolvedValue({ ...participant, active: true });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "target 계정 재활성화" }));

    await waitFor(() => expect(userMocks.setUserStatus).toHaveBeenCalledWith("user-1", {
      active: true,
      reason: "",
    }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("다시 활성화"));
    expect(await screen.findByText("target 계정을 다시 활성화했습니다.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "target 계정 정지" })).toBeEnabled();
  });

  it("aborts and ignores a stale list request when an operator refreshes", async () => {
    const signals: AbortSignal[] = [];
    userMocks.users
      .mockImplementationOnce((_query: unknown, options: { signal: AbortSignal }) => {
        signals.push(options.signal);
        return pendingUntilAbort(options.signal);
      })
      .mockResolvedValueOnce([makeUser(2, { username: "fresh-user" })]);
    renderPage();
    await waitFor(() => expect(signals).toHaveLength(1));

    fireEvent.click(screen.getByRole("button", { name: "사용자 목록 새로고침" }));

    await waitFor(() => expect(signals[0].aborted).toBe(true));
    expect(await screen.findByText("fresh-user")).toBeInTheDocument();
    expect(screen.queryByText(/문제가 발생했습니다/)).not.toBeInTheDocument();
  });

  it("keeps the suspension dialog open and shows a server error safely", async () => {
    const participant = makeUser(1, { username: "target" });
    userMocks.users.mockResolvedValue([participant]);
    userMocks.setUserStatus.mockRejectedValue(new Error("계정 상태를 변경하지 못했습니다."));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "target 계정 정지" }));
    fireEvent.change(screen.getByRole("textbox", { name: "정지 사유" }), { target: { value: "조사 필요" } });
    fireEvent.submit(screen.getByRole("button", { name: "사유 확인 후 정지" }).closest("form")!);

    expect(await screen.findByText("계정 상태를 변경하지 못했습니다.")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "target 계정 정지" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "사유 확인 후 정지" })).toBeEnabled();
  });
});
