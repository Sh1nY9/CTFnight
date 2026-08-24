import { describe, expect, it } from "vitest";
import { DEFAULT_MAX_FLAG_LENGTH, maxFlagLengthFromMeta } from "./platformLimits";

describe("platform limits", () => {
  it.each([16, 512, 4096])("accepts a valid max flag length of %i", (value) => {
    expect(maxFlagLengthFromMeta({ limits: { max_flag_length: value } })).toBe(value);
  });

  it.each([undefined, null, 15, 4097, 128.5, Number.NaN])(
    "falls back safely for an invalid or missing value %s",
    (value) => {
      const meta = value === undefined
        ? undefined
        : { limits: { max_flag_length: value as number } };
      expect(maxFlagLengthFromMeta(meta)).toBe(DEFAULT_MAX_FLAG_LENGTH);
    },
  );
});
