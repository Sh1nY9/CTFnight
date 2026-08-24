import type { EventState } from "@/api/types";

export function cx(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function formatRelative(value?: string | null): string {
  if (!value) return "기록 없음";
  const date = new Date(value);
  const difference = date.getTime() - Date.now();
  if (Number.isNaN(difference)) return "기록 없음";
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  const formatter = new Intl.RelativeTimeFormat("ko", { numeric: "auto" });
  if (Math.abs(difference) < hour) return formatter.format(Math.round(difference / minute), "minute");
  if (Math.abs(difference) < day) return formatter.format(Math.round(difference / hour), "hour");
  return formatter.format(Math.round(difference / day), "day");
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat("ko-KR").format(value);
}

export const eventStateLabel: Record<EventState, string> = {
  draft: "준비 중",
  registration: "등록 중",
  live: "진행 중",
  frozen: "점수판 동결",
  ended: "종료",
  archived: "보관됨",
};

export function makeIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `alpha-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function toDateTimeLocal(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

export function fromDateTimeLocal(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}
