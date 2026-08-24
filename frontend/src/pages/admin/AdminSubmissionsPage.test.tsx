import type { AdminSubmission } from "@/api/types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSubmissionsPage } from "./AdminSubmissionsPage";

const submissionMocks = vi.hoisted(() => ({
  submissions: vi.fn(),
  challenges: vi.fn(),
}));

vi.mock("@/api/endpoints", () => ({
  api: {
    admin: {
      submissions: submissionMocks.submissions,
      challenges: submissionMocks.challenges,
    },
  },
}));

function makeSubmission(index: number): AdminSubmission {
  return {
    id: `submission-${index}`,
    created_at: `2026-08-24T00:${String(index % 60).padStart(2, "0")}:00Z`,
    username: `player-${index}`,
    team_name: `team-${index}`,
    challenge_id: "challenge-1",
    challenge_title: "Welcome",
    correct: index % 2 === 0,
    awarded_points: index,
  };
}

function pendingUntilAbort(signal: AbortSignal): Promise<AdminSubmission[]> {
  return new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), {
      once: true,
    });
  });
}

describe("admin submission page pagination", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    submissionMocks.challenges.mockResolvedValue([]);
  });

  it("loads one 200-row page and fetches the next keyset page only after confirmation", async () => {
    const firstPage = Array.from({ length: 200 }, (_, index) => makeSubmission(index + 1));
    const nextPage = [makeSubmission(201)];
    submissionMocks.submissions
      .mockResolvedValueOnce(firstPage)
      .mockResolvedValueOnce(nextPage);

    render(<AdminSubmissionsPage />);

    expect(await screen.findByText(/서버에서 현재 200개를 불러왔습니다/)).toBeInTheDocument();
    expect(submissionMocks.submissions).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "현재 200개 CSV" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "더 불러오기" }));

    expect(await screen.findByText(/서버에서 현재 201개를 불러왔습니다/)).toBeInTheDocument();
    expect(submissionMocks.submissions).toHaveBeenCalledTimes(2);
    expect(submissionMocks.submissions.mock.calls[1][0]).toMatchObject({
      limit: 200,
      before_created_at: firstPage.at(-1)?.created_at,
      before_id: firstPage.at(-1)?.id,
    });
    expect(submissionMocks.submissions.mock.calls[1][1].signal).toBeInstanceOf(AbortSignal);
    expect(screen.queryByRole("button", { name: "더 불러오기" })).not.toBeInTheDocument();
  });

  it("aborts stale requests on filter change, refresh, and unmount", async () => {
    const signals: AbortSignal[] = [];
    submissionMocks.submissions.mockImplementation(
      (_query: unknown, options: { signal: AbortSignal }) => {
        signals.push(options.signal);
        return pendingUntilAbort(options.signal);
      },
    );

    const view = render(<AdminSubmissionsPage />);
    await waitFor(() => expect(signals).toHaveLength(1));

    fireEvent.change(screen.getByRole("combobox", { name: "채점 결과" }), {
      target: { value: "correct" },
    });
    await waitFor(() => expect(signals).toHaveLength(2));
    expect(signals[0].aborted).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "새로고침" }));
    await waitFor(() => expect(signals).toHaveLength(3));
    expect(signals[1].aborted).toBe(true);

    view.unmount();
    expect(signals[2].aborted).toBe(true);
  });

  it("cancels an in-flight next page and clears its loading state when filters change", async () => {
    const firstPage = Array.from({ length: 200 }, (_, index) => makeSubmission(index + 1));
    let nextPageSignal: AbortSignal | undefined;
    submissionMocks.submissions
      .mockResolvedValueOnce(firstPage)
      .mockImplementationOnce((_query: unknown, options: { signal: AbortSignal }) => {
        nextPageSignal = options.signal;
        return pendingUntilAbort(options.signal);
      })
      .mockResolvedValueOnce([makeSubmission(999)]);

    render(<AdminSubmissionsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "더 불러오기" }));
    await waitFor(() => expect(nextPageSignal).toBeDefined());

    fireEvent.change(screen.getByRole("combobox", { name: "채점 결과" }), {
      target: { value: "correct" },
    });

    await waitFor(() => expect(nextPageSignal?.aborted).toBe(true));
    expect(await screen.findByRole("button", { name: "현재 1개 CSV" })).toBeEnabled();
    expect(screen.queryByText("불러오는 중…")).not.toBeInTheDocument();
  });
});
