import type { CurrentUser, EventSummary, TeamDetails } from "@/api/types";
import { ToastProvider } from "@/components/Toast";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TeamPage } from "./TeamPage";

const teamMocks = vi.hoisted(() => ({
  event: vi.fn(),
  mine: vi.fn(),
  create: vi.fn(),
  join: vi.fn(),
  rotateInvite: vi.fn(),
  transferOwner: vi.fn(),
  removeMember: vi.fn(),
  leave: vi.fn(),
}));

const authState = vi.hoisted(() => ({
  user: null as CurrentUser | null,
  refresh: vi.fn(),
}));

vi.mock("@/api/endpoints", () => ({
  api: {
    participant: { event: teamMocks.event },
    team: teamMocks,
  },
}));

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => authState }));

const registrationEvent: EventSummary = {
  id: "event-1",
  name: "CTFnight",
  slug: "ctfnight",
  state: "registration",
  team_mode: "team",
};

const ownerTeam: TeamDetails = {
  id: "team-1",
  name: "0xCTFnight",
  role: "owner",
  members: [
    { id: "owner-1", username: "owner", role: "owner" },
    { id: "member-1", username: "player", role: "member" },
  ],
};

function renderPage() {
  return render(<ToastProvider><TeamPage /></ToastProvider>);
}

describe("team member management", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user = {
      id: "owner-1",
      email: "owner@example.test",
      username: "owner",
      role: "participant",
      team: { id: "team-1", name: "0xCTFnight", role: "owner" },
    };
    authState.refresh.mockResolvedValue(authState.user);
    teamMocks.event.mockResolvedValue(registrationEvent);
    teamMocks.mine.mockResolvedValue(ownerTeam);
  });

  afterEach(() => vi.restoreAllMocks());

  it("requires confirmation, transfers ownership, and refreshes auth and team state", async () => {
    const transferred: TeamDetails = {
      ...ownerTeam,
      role: "member",
      members: ownerTeam.members?.map((member) => ({
        ...member,
        role: member.id === "member-1" ? "owner" as const : "member" as const,
      })),
    };
    teamMocks.transferOwner.mockResolvedValue(transferred);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "player에게 소유권 이전" }));

    await waitFor(() => expect(teamMocks.transferOwner).toHaveBeenCalledWith("member-1"));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("일반 멤버"));
    expect(authState.refresh).toHaveBeenCalledOnce();
    expect(await screen.findByText("player님에게 팀 소유권을 이전했습니다.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "player 팀에서 제외" })).not.toBeInTheDocument();
  });

  it("shows a safe error and leaves the owner controls usable after a rejected removal", async () => {
    teamMocks.removeMember.mockRejectedValue(new Error("멤버를 제외하지 못했습니다."));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "player 팀에서 제외" }));

    expect(await screen.findByText("멤버를 제외하지 못했습니다.")).toBeInTheDocument();
    expect(authState.refresh).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "player 팀에서 제외" })).toBeEnabled();
  });

  it("reveals the atomically rotated invite after a member is removed", async () => {
    teamMocks.removeMember.mockResolvedValue({
      team: { ...ownerTeam, members: ownerTeam.members?.filter((member) => member.id !== "member-1") },
      invite_code: "rotated-private-invite",
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "player 팀에서 제외" }));

    expect(await screen.findByText("rotated-private-invite")).toBeInTheDocument();
    expect(screen.getByText(/지금 한 번만 표시됩니다/)).toBeInTheDocument();
    expect(teamMocks.removeMember).toHaveBeenCalledWith("member-1");
  });

  it("does not expose member mutation controls outside registration or in individual mode", async () => {
    teamMocks.event.mockResolvedValueOnce({ ...registrationEvent, state: "live" });
    const first = renderPage();
    await screen.findByText("0xCTFnight");
    expect(screen.queryByRole("button", { name: "player에게 소유권 이전" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "player 팀에서 제외" })).not.toBeInTheDocument();
    first.unmount();

    teamMocks.event.mockResolvedValueOnce({ ...registrationEvent, team_mode: "individual" });
    renderPage();
    expect(await screen.findByText("INDIVIDUAL DIVISION")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /소유권 이전/ })).not.toBeInTheDocument();
    expect(teamMocks.mine).toHaveBeenCalledOnce();
  });
});
