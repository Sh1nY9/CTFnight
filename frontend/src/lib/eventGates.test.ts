import type { EventSummary } from "@/api/types";
import { describe, expect, it } from "vitest";
import { scoringIsLocked, submissionsAreOpen, teamChangesAreOpen } from "./eventGates";

const NOW = Date.parse("2026-08-24T12:00:00Z");
const event = (state: EventSummary["state"], values: Partial<EventSummary> = {}): EventSummary => ({
  id: "event-1",
  name: "CTFnight",
  slug: "ctfnight",
  state,
  ...values,
});

describe("event operation gates", () => {
  it("opens team changes only during the registration time window", () => {
    expect(teamChangesAreOpen(event("registration"), NOW)).toBe(true);
    expect(teamChangesAreOpen(event("registration", { registration_at: new Date(NOW).toISOString() }), NOW)).toBe(true);
    expect(teamChangesAreOpen(event("registration", { registration_at: new Date(NOW + 1).toISOString() }), NOW)).toBe(false);
    expect(teamChangesAreOpen(event("registration", { end_at: new Date(NOW).toISOString() }), NOW)).toBe(false);
    expect(teamChangesAreOpen(event("live"), NOW)).toBe(false);
  });

  it("opens submissions in live or frozen only inside the match window", () => {
    expect(submissionsAreOpen(event("live"), NOW)).toBe(true);
    expect(submissionsAreOpen(event("frozen", { start_at: new Date(NOW).toISOString() }), NOW)).toBe(true);
    expect(submissionsAreOpen(event("live", { start_at: new Date(NOW + 1).toISOString() }), NOW)).toBe(false);
    expect(submissionsAreOpen(event("frozen", { end_at: new Date(NOW).toISOString() }), NOW)).toBe(false);
    expect(submissionsAreOpen(event("ended"), NOW)).toBe(false);
    expect(submissionsAreOpen(event("archived"), NOW)).toBe(false);
  });

  it("locks scoring after a live freeze cutoff and in all later states", () => {
    expect(scoringIsLocked(event("live", { freeze_at: new Date(NOW + 1).toISOString() }), NOW)).toBe(false);
    expect(scoringIsLocked(event("live", { freeze_at: new Date(NOW).toISOString() }), NOW)).toBe(true);
    expect(scoringIsLocked(event("frozen"), NOW)).toBe(true);
    expect(scoringIsLocked(event("ended"), NOW)).toBe(true);
    expect(scoringIsLocked(event("archived"), NOW)).toBe(true);
    expect(scoringIsLocked(event("registration", { freeze_at: new Date(NOW - 1).toISOString() }), NOW)).toBe(false);
  });
});
