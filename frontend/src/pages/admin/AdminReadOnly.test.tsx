import type { AdminChallenge, AdminEvent, Announcement } from "@/api/types";
import { ToastProvider } from "@/components/Toast";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminAnnouncementsPage } from "./AdminAnnouncementsPage";
import { AdminChallengesPage } from "./AdminChallengesPage";
import { AdminSettingsPage } from "./AdminSettingsPage";

const adminMocks = vi.hoisted(() => ({
  event: vi.fn(),
  updateEvent: vi.fn(),
  challenges: vi.fn(),
  createChallenge: vi.fn(),
  updateChallenge: vi.fn(),
  setChallengeVisibility: vi.fn(),
  announcements: vi.fn(),
  createAnnouncement: vi.fn(),
  updateAnnouncement: vi.fn(),
  deleteAnnouncement: vi.fn(),
  registrationCodes: vi.fn(),
  createRegistrationCode: vi.fn(),
  revokeRegistrationCode: vi.fn(),
  meta: vi.fn(),
}));

vi.mock("@/api/endpoints", () => ({
  api: { admin: adminMocks, meta: adminMocks.meta },
}));

const archivedEvent: AdminEvent = {
  id: "event-1",
  name: "Archived CTFnight",
  slug: "archived-ctfnight",
  state: "archived",
  team_mode: "team",
};

const challenge: AdminChallenge = {
  id: "challenge-1",
  slug: "welcome",
  title: "Welcome",
  category: "Misc",
  description_md: "hello",
  connection_info: null,
  scoring_type: "fixed",
  current_points: 100,
  solve_count: 0,
  max_attempts: 0,
  visible_at: null,
  prerequisite_ids: [],
  flag_type: "exact",
  has_flag: true,
  visible: true,
  initial_points: 100,
  minimum_points: 100,
  decay: 20,
};

const announcement: Announcement = {
  id: "announcement-1",
  title: "Welcome notice",
  body_md: "hello",
};

function withToasts(element: React.ReactElement) {
  return render(<ToastProvider>{element}</ToastProvider>);
}

describe("archived admin UI", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    adminMocks.event.mockResolvedValue(archivedEvent);
    adminMocks.challenges.mockResolvedValue([challenge]);
    adminMocks.announcements.mockResolvedValue([announcement]);
    adminMocks.registrationCodes.mockResolvedValue([]);
    adminMocks.meta.mockResolvedValue({ limits: { max_flag_length: 512 } });
  });

  it("makes event settings read-only", async () => {
    withToasts(<AdminSettingsPage />);

    await screen.findByText(/보관된 이벤트는 읽기 전용/);
    expect(screen.getByRole("textbox", { name: "이벤트 이름" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "읽기 전용" })).toBeDisabled();
  });

  it("disables challenge creation, editing, and visibility changes", async () => {
    withToasts(<AdminChallengesPage />);

    await screen.findByText(/문제는 읽기 전용/);
    expect(screen.getByRole("button", { name: "새 문제" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Welcome 수정" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Welcome 비공개 전환" })).toBeDisabled();
  });

  it("disables announcement creation, editing, and deletion", async () => {
    withToasts(<AdminAnnouncementsPage />);

    await screen.findByText(/공지는 읽기 전용/);
    expect(screen.getByRole("button", { name: "새 공지" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Welcome notice 수정" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Welcome notice 삭제" })).toBeDisabled();
  });
});

describe("admin event and flag contracts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    adminMocks.event.mockResolvedValue({ ...archivedEvent, state: "draft", name: "Draft CTFnight" });
    adminMocks.challenges.mockResolvedValue([]);
    adminMocks.announcements.mockResolvedValue([]);
    adminMocks.registrationCodes.mockResolvedValue([]);
    adminMocks.meta.mockResolvedValue({ limits: { max_flag_length: 512 } });
  });

  it("allows only the current and immediately next event state", async () => {
    withToasts(<AdminSettingsPage />);

    expect(await screen.findByRole("radio", { name: /준비 중/ })).toBeEnabled();
    expect(screen.getByRole("radio", { name: /등록 중/ })).toBeEnabled();
    expect(screen.getByRole("radio", { name: /진행 중/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /보관됨/ })).toBeDisabled();
  });

  it("matches challenge editor fields to the server limits", async () => {
    adminMocks.meta.mockResolvedValue({ limits: { max_flag_length: 2048 } });
    withToasts(<AdminChallengesPage />);
    const create = await screen.findByRole("button", { name: "새 문제" });
    fireEvent.click(create);

    expect(screen.getByRole("textbox", { name: "제목" })).toHaveAttribute("maxlength", "120");
    expect(screen.getByPlaceholderText("web-welcome")).toHaveAttribute("maxlength", "63");
    expect(screen.getByPlaceholderText("web-welcome")).toHaveAttribute("pattern", "[a-z0-9][a-z0-9-]{1,62}");
    expect(screen.getByRole("combobox", { name: "카테고리" })).toHaveAttribute("maxlength", "80");
    expect(screen.getByPlaceholderText("nc challenge.example.com 31337")).toHaveAttribute("maxlength", "2000");
    expect(screen.getByRole("spinbutton", { name: "점수" })).toHaveAttribute("max", "1000000");
    expect(screen.getByRole("spinbutton", { name: /최대 시도/ })).toHaveAttribute("max", "1000000");
    expect(screen.getByPlaceholderText("FLAG{...}")).toHaveAttribute("maxlength", "2048");
    expect(screen.getByText(/실제 형식은 문제별로 자유롭게 정할 수 있습니다/)).toBeInTheDocument();
    expect(screen.getByText(/최대 2,048자/)).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "점수 방식" }), { target: { value: "dynamic" } });
    expect(screen.getByRole("spinbutton", { name: "시작 점수" })).toHaveAttribute("max", "1000000");
    expect(screen.getByRole("spinbutton", { name: "최소 점수" })).toHaveAttribute("max", "1000000");
    expect(screen.getByRole("spinbutton", { name: /감쇠 기준/ })).toHaveAttribute("max", "1000000");
  });

  it("locks all scoring controls after the scoreboard freezes", async () => {
    adminMocks.event.mockResolvedValue({ ...archivedEvent, state: "frozen", name: "Frozen CTFnight" });
    adminMocks.challenges.mockResolvedValue([{
      ...challenge,
      scoring_type: "dynamic",
      initial_points: 500,
      minimum_points: 100,
      current_points: 400,
    }]);
    withToasts(<AdminChallengesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Welcome 수정" }));

    expect(screen.getByText(/점수판 동결이 시작되어 점수 방식과 배점을 변경할 수 없습니다/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "점수 방식" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: "시작 점수" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: "최소 점수" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: /감쇠 기준/ })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: /최대 시도/ })).toBeEnabled();
  });

  it("locks the freeze cutoff once a live cutoff has passed", async () => {
    adminMocks.event.mockResolvedValue({
      ...archivedEvent,
      state: "live",
      name: "Live CTFnight",
      freeze_at: new Date(Date.now() - 60_000).toISOString(),
    });
    withToasts(<AdminSettingsPage />);

    await screen.findByText(/동결 기준 시각을 변경할 수 없습니다/);
    expect(screen.getByLabelText("점수판 동결")).toBeDisabled();
    expect(screen.getByLabelText("경기 종료")).toBeEnabled();
  });
});
