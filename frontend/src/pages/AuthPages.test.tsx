import type { CurrentUser } from "@/api/types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RegisterPage } from "./AuthPages";

const authMocks = vi.hoisted(() => ({
  login: vi.fn(),
  register: vi.fn(),
}));

const participantMocks = vi.hoisted(() => ({ event: vi.fn() }));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: null, login: authMocks.login, register: authMocks.register }),
}));

vi.mock("@/api/endpoints", () => ({
  api: { participant: participantMocks },
}));

const registeredUser: CurrentUser = {
  id: "user-1",
  email: "player@example.test",
  username: "player",
  role: "participant",
  team: null,
};

function renderRegister() {
  return render(<MemoryRouter initialEntries={["/register"]}><RegisterPage /></MemoryRouter>);
}

describe("registration access-code UI", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authMocks.register.mockResolvedValue(registeredUser);
  });

  it("requires the event access code and passes only the trimmed value", async () => {
    participantMocks.event.mockResolvedValue({
      id: "event-1",
      name: "CTFnight",
      slug: "ctfnight",
      state: "registration",
      registration_access_mode: "code",
    });
    renderRegister();

    const accessCode = await screen.findByLabelText("등록 접근 코드");
    expect(accessCode).toBeRequired();
    fireEvent.change(screen.getByPlaceholderText("player_one"), { target: { value: "player" } });
    fireEvent.change(screen.getByPlaceholderText("player@example.com"), { target: { value: "player@example.test" } });
    fireEvent.change(screen.getByPlaceholderText("12자 이상 입력"), { target: { value: "CorrectHorse!123" } });
    fireEvent.change(accessCode, { target: { value: "  invited-only  " } });
    fireEvent.click(screen.getByRole("button", { name: /계정 만들기/ }));

    await waitFor(() => expect(authMocks.register).toHaveBeenCalledWith(
      "player@example.test",
      "player",
      "CorrectHorse!123",
      "invited-only",
    ));
  });

  it("shows a fail-safe optional field when event metadata cannot be loaded", async () => {
    participantMocks.event.mockRejectedValue(new Error("unavailable"));
    renderRegister();

    const accessCode = await screen.findByLabelText("등록 접근 코드");
    expect(accessCode).not.toBeRequired();
    expect(screen.getByText(/이벤트 설정을 확인하지 못했습니다/)).toBeInTheDocument();
  });
});
