import type { MetaResponse } from "@/api/types";

export const DEFAULT_MAX_FLAG_LENGTH = 512;

export function maxFlagLengthFromMeta(meta?: MetaResponse | null): number {
  const value = meta?.limits?.max_flag_length;
  return typeof value === "number" && Number.isInteger(value) && value >= 16 && value <= 4096
    ? value
    : DEFAULT_MAX_FLAG_LENGTH;
}
