import type { CurrentUser } from "@/api/types";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccountSecurityPage } from "./AccountSecurityPage";
import { LoginPage, RegisterPage } from "./AuthPages";
import { TeamPage } from "./TeamPage";

const constraintMocks = vi.hoisted(() => ({
  user: null as CurrentUser | null,
  login: vi.fn(),
  register: vi.fn(),
  refresh: vi.fn(),
  push: vi.fn(),
  event: vi.fn(),
  changePassword: vi.fn(),
  mine: vi.fn(),
  createTeam: vi.fn(),
  joinTeam: vi.fn(),
  rotateInvite: vi.fn(),
  leaveTeam: vi.fn(),
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: constraintMocks.user,
    login: constraintMocks.login,
    register: constraintMocks.register,
    refresh: constraintMocks.refresh,
  }),
}));

vi.mock("@/components/Toast", () => ({
  useToast: () => ({ push: constraintMocks.push }),
}));

vi.mock("@/api/endpoints", () => ({
  api: {
    auth: { changePassword: constraintMocks.changePassword },
    participant: { event: constraintMocks.event },
    team: {
      mine: constraintMocks.mine,
      create: constraintMocks.createTeam,
      join: constraintMocks.joinTeam,
      rotateInvite: constraintMocks.rotateInvite,
      leave: constraintMocks.leaveTeam,
    },
  },
}));

const participant: CurrentUser = {
  id: "user-1",
  email: "player@example.test",
  username: "player",
  role: "participant",
  team: null,
};

describe("frontend input limits", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    constraintMocks.user = null;
    constraintMocks.event.mockResolvedValue({
      id: "event-1",
      name: "CTFnight",
      slug: "ctfnight",
      state: "registration",
      team_mode: "team",
    });
    constraintMocks.mine.mockResolvedValue({
      id: "team-1",
      name: "0xCTFnight",
      role: "owner",
      members: [{ id: "user-1", username: "player", role: "owner" }],
    });
  });

  it("caps login and registration passwords at the server maximum", () => {
    const login = render(<MemoryRouter><LoginPage /></MemoryRouter>);
    expect(screen.getByPlaceholderText("비밀번호 입력")).toHaveAttribute("maxlength", "128");
    login.unmount();

    render(<MemoryRouter><RegisterPage /></MemoryRouter>);
    expect(screen.getByPlaceholderText("12자 이상 입력")).toHaveAttribute("maxlength", "128");
    expect(screen.getByPlaceholderText("12자 이상 입력")).toHaveAttribute("minlength", "12");
  });

  it("caps all password-change fields at 128 characters", () => {
    constraintMocks.user = participant;
    const { container } = render(<AccountSecurityPage />);
    const passwordFields = Array.from(container.querySelectorAll<HTMLInputElement>('input[type="password"]'));

    expect(passwordFields).toHaveLength(3);
    passwordFields.forEach((input) => expect(input).toHaveAttribute("maxlength", "128"));
    expect(passwordFields[1]).toHaveAttribute("minlength", "12");
    expect(passwordFields[2]).toHaveAttribute("minlength", "12");
  });

  it("requires the server minimum length for team invite codes", async () => {
    constraintMocks.user = participant;
    render(<TeamPage />);
    fireEvent.click(await screen.findByRole("tab", { name: "초대 코드 입력" }));

    const invite = screen.getByPlaceholderText("CTFnight-XXXX-XXXX");
    expect(invite).toHaveAttribute("minlength", "16");
    expect(invite).toHaveAttribute("maxlength", "128");
  });

  it("uses the CTFnight team-name example", async () => {
    constraintMocks.user = participant;
    render(<TeamPage />);

    expect(await screen.findByPlaceholderText("0xCTFnight")).toBeInTheDocument();
  });

  it.each(["safe\u202Eevil", "safe\u200Bhidden", "safe\u001Bcontrol"])(
    "blocks invisible or control characters in a team name: %j",
    async (unsafeName) => {
      constraintMocks.user = participant;
      render(<TeamPage />);

      fireEvent.change(await screen.findByPlaceholderText("0xCTFnight"), {
        target: { value: unsafeName },
      });
      fireEvent.click(screen.getByRole("button", { name: "팀 만들기" }));

      expect(await screen.findByText(/제어 문자나 보이지 않는 방향 전환 문자/)).toBeInTheDocument();
      expect(constraintMocks.createTeam).not.toHaveBeenCalled();
    },
  );

  it("makes team creation and joining read-only outside registration", async () => {
    constraintMocks.user = participant;
    constraintMocks.event.mockResolvedValue({
      id: "event-1", name: "CTFnight", slug: "ctfnight", state: "live", team_mode: "team",
    });
    render(<TeamPage />);

    await screen.findByText(/팀 구성 변경은 등록 기간에만 가능/);
    expect(screen.getByRole("tab", { name: "새 팀 만들기" })).toBeDisabled();
    expect(screen.getByRole("tab", { name: "초대 코드 입력" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "팀 만들기" })).toBeDisabled();
  });

  it("locks invite rotation and leaving after registration", async () => {
    constraintMocks.user = { ...participant, team: { id: "team-1", name: "0xCTFnight", role: "owner" } };
    constraintMocks.event.mockResolvedValue({
      id: "event-1", name: "CTFnight", slug: "ctfnight", state: "ended", team_mode: "team",
    });
    render(<TeamPage />);

    await screen.findByText(/현재 팀 정보는 읽기 전용/);
    expect(await screen.findByRole("button", { name: "초대 코드 교체" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "팀 나가기" })).toBeDisabled();
  });
});
