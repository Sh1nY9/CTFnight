import type { AdminSubmission } from "@/api/types";
import { serializeCsv } from "./csv";

export const ADMIN_SUBMISSION_PAGE_SIZE = 200;

export type AdminSubmissionQuery = Record<string, string | number | boolean | undefined>;
export type AdminSubmissionCursor = Pick<AdminSubmission, "created_at" | "id">;

export function submissionCursor(rows: readonly AdminSubmission[]): AdminSubmissionCursor | undefined {
  const last = rows.at(-1);
  return last ? { created_at: last.created_at, id: last.id } : undefined;
}

export function appendUniqueSubmissions(
  current: readonly AdminSubmission[],
  page: readonly AdminSubmission[],
): AdminSubmission[] {
  const seenIds = new Set(current.map((submission) => submission.id));
  return [
    ...current,
    ...page.filter((submission) => {
      if (seenIds.has(submission.id)) return false;
      seenIds.add(submission.id);
      return true;
    }),
  ];
}

export function isAdminSubmissionExportReady(options: {
  loading: boolean;
  error: string;
  activeFilterKey: string;
  loadedFilterKey: string | null;
  count: number;
}): boolean {
  return !options.loading
    && !options.error
    && options.activeFilterKey === options.loadedFilterKey
    && options.count > 0;
}

export function filterAdminSubmissions(
  submissions: readonly AdminSubmission[],
  search: string,
): AdminSubmission[] {
  const normalized = search.trim().toLocaleLowerCase();
  return submissions.filter((item) => (
    !normalized
    || `${item.team_name ?? ""} ${item.username} ${item.challenge_title}`
      .toLocaleLowerCase()
      .includes(normalized)
  ));
}

export function serializeAdminSubmissionsCsv(
  submissions: readonly AdminSubmission[],
): string {
  return serializeCsv([
    ["created_at", "team", "username", "challenge", "result", "points", "submitted_fingerprint", "ip_fingerprint"],
    ...submissions.map((item) => [
      item.created_at,
      item.team_name,
      item.username,
      item.challenge_title,
      item.correct ? "correct" : "wrong",
      item.awarded_points ?? 0,
      item.submitted_fingerprint ?? "",
      item.ip_fingerprint ?? "",
    ]),
  ]);
}
