import type { Challenge, CurrentUser, EventSummary } from "@/api/types";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChallengeDetailPage } from "./ChallengeDetailPage";

const detailMocks = vi.hoisted(() => ({
  challenge: vi.fn(),
  event: vi.fn(),
  submit: vi.fn(),
  push: vi.fn(),
  meta: vi.fn(),
}));

const user: CurrentUser = {
  id: "user-1",
  email: "player@example.test",
  username: "player",
  role: "participant",
  team: { id: "team-1", name: "0xCTFnight", role: "owner" },
};

const challenge: Challenge = {
  id: "challenge-1",
  slug: "welcome",
  title: "Welcome",
  category: "Misc",
  description_md: "hello",
  connection_info: null,
  scoring_type: "fixed",
  current_points: 100,
  solve_count: 0,
  solved: false,
  max_attempts: 0,
  attempts: 0,
  visible_at: null,
  prerequisite_ids: [],
};

const event = (state: EventSummary["state"]): EventSummary => ({
  id: "event-1",
  name: "CTFnight",
  slug: "ctfnight",
  state,
  team_mode: "team",
});

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user }) }));
vi.mock("@/components/Toast", () => ({ useToast: () => ({ push: detailMocks.push }) }));
vi.mock("@/api/endpoints", () => ({
  api: {
    meta: detailMocks.meta,
    participant: { challenge: detailMocks.challenge, event: detailMocks.event, submit: detailMocks.submit },
  },
}));

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/challenges/challenge-1"]}>
      <Routes><Route path="/challenges/:id" element={<ChallengeDetailPage />} /></Routes>
    </MemoryRouter>,
  );
}

describe("challenge submission gate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    detailMocks.challenge.mockResolvedValue(challenge);
    detailMocks.event.mockResolvedValue(event("live"));
    detailMocks.meta.mockResolvedValue({ limits: { max_flag_length: 2048 } });
  });

  it("uses the server-provided flag limit while submissions are open", async () => {
    renderPage();

    const flag = await screen.findByLabelText("플래그");
    expect(flag).toHaveAttribute("maxlength", "2048");
    expect(screen.getByText(/최대 2,048자/)).toBeInTheDocument();
    expect(flag).toHaveAttribute("placeholder", "FLAG{...}");
    fireEvent.change(flag, { target: { value: "FLAG{test}" } });
    expect(screen.getByRole("button", { name: "제출" })).toBeEnabled();
  });

  it("falls back to 512 when the metadata request fails", async () => {
    detailMocks.meta.mockRejectedValue(new Error("metadata unavailable"));
    renderPage();

    expect(await screen.findByLabelText("플래그")).toHaveAttribute("maxlength", "512");
    expect(screen.getByText(/최대 512자/)).toBeInTheDocument();
  });

  it.each<EventSummary["state"]>(["ended", "archived"])(
    "hides the submission form and explains the %s state",
    async (state) => {
      detailMocks.event.mockResolvedValue(event(state));
      renderPage();

      await screen.findByText("이벤트 제출이 종료되었습니다.");
      expect(screen.queryByLabelText("플래그")).not.toBeInTheDocument();
      expect(detailMocks.submit).not.toHaveBeenCalled();
    },
  );
});
