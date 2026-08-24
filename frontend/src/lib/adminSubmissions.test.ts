import type { AdminSubmission } from "@/api/types";
import { describe, expect, it } from "vitest";
import {
  ADMIN_SUBMISSION_PAGE_SIZE,
  appendUniqueSubmissions,
  filterAdminSubmissions,
  isAdminSubmissionExportReady,
  serializeAdminSubmissionsCsv,
  submissionCursor,
} from "./adminSubmissions";

function makeSubmission(index: number): AdminSubmission {
  return {
    id: `submission-${index}`,
    created_at: `2026-08-24T00:${String(index % 60).padStart(2, "0")}:00Z`,
    username: `player-${index}`,
    team_name: `team-${index}`,
    challenge_id: "challenge-1",
    challenge_title: index === 201 ? "Final challenge" : "Welcome",
    correct: index % 2 === 0,
    awarded_points: index,
    submitted_fingerprint: `submission-hash-${index}`,
    ip_fingerprint: `ip-hash-${index}`,
  };
}

describe("admin submission pagination", () => {
  it("derives the next keyset cursor from only the current page", () => {
    const page = Array.from({ length: ADMIN_SUBMISSION_PAGE_SIZE }, (_, index) => makeSubmission(index + 1));
    expect(submissionCursor(page)).toEqual({
      created_at: page.at(-1)?.created_at,
      id: page.at(-1)?.id,
    });
    expect(submissionCursor([])).toBeUndefined();
  });

  it("appends one page and removes overlapping ids without traversing again", () => {
    const current = [makeSubmission(1), makeSubmission(2)];
    const next = [makeSubmission(2), makeSubmission(3)];

    expect(appendUniqueSubmissions(current, next).map((row) => row.id)).toEqual([
      "submission-1",
      "submission-2",
      "submission-3",
    ]);
  });

  it("searches and exports only records explicitly loaded into the current range", () => {
    const rows = Array.from({ length: 201 }, (_, index) => makeSubmission(index + 1));

    expect(filterAdminSubmissions(rows, "final challenge")).toEqual([rows[200]]);

    const csv = serializeAdminSubmissionsCsv(rows);
    expect(csv.split("\r\n")).toHaveLength(202);
    expect(csv).toContain('"player-201"');
    expect(csv).toContain('"Final challenge"');
  });

  it("enables CSV only for the successfully loaded server-filter generation", () => {
    const base = {
      loading: false,
      error: "",
      activeFilterKey: "correct\u0000challenge-2",
      loadedFilterKey: "correct\u0000challenge-2",
      count: 1,
    };

    expect(isAdminSubmissionExportReady(base)).toBe(true);
    expect(isAdminSubmissionExportReady({ ...base, loading: true })).toBe(false);
    expect(isAdminSubmissionExportReady({ ...base, error: "request failed" })).toBe(false);
    expect(isAdminSubmissionExportReady({ ...base, loadedFilterKey: "all\u0000" })).toBe(false);
    expect(isAdminSubmissionExportReady({ ...base, count: 0 })).toBe(false);
  });
});
