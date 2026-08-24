import type { EventSummary } from "@/api/types";

type EventGateData = Pick<
  EventSummary,
  "state" | "registration_at" | "start_at" | "freeze_at" | "end_at"
>;

function timestamp(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? Number.NaN : parsed;
}

function reached(value: string | null | undefined, now: number): boolean {
  const parsed = timestamp(value);
  return parsed === null || (!Number.isNaN(parsed) && parsed <= now);
}

function before(value: string | null | undefined, now: number): boolean {
  const parsed = timestamp(value);
  return parsed === null || (!Number.isNaN(parsed) && now < parsed);
}

export function teamChangesAreOpen(event: EventGateData | null, now = Date.now()): boolean {
  return Boolean(
    event
      && event.state === "registration"
      && reached(event.registration_at, now)
      && before(event.end_at, now),
  );
}

export function submissionsAreOpen(event: EventGateData | null, now = Date.now()): boolean {
  return Boolean(
    event
      && (event.state === "live" || event.state === "frozen")
      && reached(event.start_at, now)
      && before(event.end_at, now),
  );
}

export function scoringIsLocked(event: EventGateData | null, now = Date.now()): boolean {
  if (!event) return false;
  if (["frozen", "ended", "archived"].includes(event.state)) return true;
  if (event.state !== "live" || !event.freeze_at) return false;
  const freezeAt = timestamp(event.freeze_at);
  return freezeAt !== null && !Number.isNaN(freezeAt) && freezeAt <= now;
}
