import { describe, expect, it, vi } from "vitest";
import { eventStateLabel, formatNumber, formatRelative, fromDateTimeLocal, toDateTimeLocal } from "./utils";

describe("presentation utilities", () => {
  it("provides a Korean label for every event state", () => {
    expect(Object.keys(eventStateLabel)).toHaveLength(6);
    expect(eventStateLabel.frozen).toBe("점수판 동결");
  });

  it("formats scores with locale separators", () => {
    expect(formatNumber(1234567)).toContain("1,234,567");
  });

  it("formats nearby times relatively", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-24T12:00:00Z"));
    expect(formatRelative("2026-08-24T11:55:00Z")).toContain("5분 전");
    vi.useRealTimers();
  });

  it("round-trips a local datetime to an ISO value", () => {
    const iso = fromDateTimeLocal("2026-08-24T21:30");
    expect(iso).toMatch(/^2026-08-24T\d{2}:30:00\.000Z$/);
    expect(toDateTimeLocal(iso)).toBe("2026-08-24T21:30");
  });
});
